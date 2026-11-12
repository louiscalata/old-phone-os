#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image policy and explicit SSH disabling. Host OpenSSH only parses; it never listens or logs in."""
import glob
import hashlib
import ctypes
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def require(ok, message):
    if not ok:
        raise ValueError(message)


def public_key(path):
    require(Path(path).is_file(), "a public SSH key file is required")
    text = Path(path).read_text()
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    require(lines and all(re.match(r"^(ssh-|ecdsa-sha2-|sk-)[\w@.-]+\s+\S+", line) for line in lines),
            "a nonempty public SSH key file is required")
    # Check each key, rather than accepting a file containing one good and one invalid key.
    with tempfile.NamedTemporaryFile(mode="w+") as f:
        for line in lines:
            f.seek(0)
            f.truncate()
            f.write(line + "\n")
            f.flush()
            p = subprocess.run(["ssh-keygen", "-l", "-f", f.name], capture_output=True, timeout=15)
            require(p.returncode == 0, "invalid public SSH key")


def ssh(root, recovery_key=None):
    root = Path(root).resolve()
    seen, configs = set(), []
    intended_include = False
    global_auth = {}
    auth = {"passwordauthentication", "kbdinteractiveauthentication", "challengeresponseauthentication"}

    def read(path, work, matched=False):
        nonlocal intended_include
        path = Path(path)
        require(path.resolve().is_relative_to(root), "SSH Include escapes image")
        require(path not in seen and len(seen) < 100, "SSH Include cycle or too many files")
        seen.add(path)
        output = Path(work) / str(len(configs))
        lines = []
        configs.append((output, lines))
        for line in path.read_text().splitlines():
            parts = shlex.split(re.sub(r"^(\s*[^\s=]+)\s*=\s*", r"\1 ", line), comments=True)
            if not parts:
                continue
            # OpenSSH accepts Key=value as well as Key value.
            parts = re.split(r"[\s=]+", parts[0], maxsplit=1) + parts[1:]
            key, values = parts[0].lower(), [p for p in parts[1:] if p != "="]
            if key == "include":
                if path == root / "etc/ssh/sshd_config" and not matched and values == ["/etc/ssh/sshd_config.d/*.conf"]:
                    intended_include = True
                children = []
                for pattern in values:
                    require(not any(c in pattern for c in ("%", "~", "..")), "unsupported SSH Include")
                    name = root / pattern.lstrip("/") if pattern.startswith("/") else root / "etc/ssh" / pattern
                    for child in sorted(glob.glob(str(name))):
                        children.append(read(child, work, matched))
                if children:
                    lines.append("Include " + " ".join('"' + str(child) + '"' for child in children))
            else:
                if key == "match":
                    matched = True
                if key in auth and recovery_key is None:
                    require([v.lower() for v in values] == ["no"], "SSH password authentication must be no in every scope")
                    if not matched:
                        global_auth.setdefault(key, "no")
                lines.append(line)
        seen.remove(path)
        return output

    with tempfile.TemporaryDirectory() as work:
        main = read(root / "etc/ssh/sshd_config", work)
        require(intended_include, "sshd_config needs the global Include /etc/ssh/sshd_config.d/*.conf")
        require(recovery_key is not None or (global_auth.get("passwordauthentication") == "no" and
                global_auth.get("kbdinteractiveauthentication", global_auth.get("challengeresponseauthentication")) == "no"),
                "global SSH password defaults are not disabled")
        exe = shutil.which("sshd") or "/usr/sbin/sshd"
        # Preserve Include/Match scope; only the paths change to private host-side copies.
        for output, lines in configs:
            output.write_text("\n".join(lines) + "\n")
        # sshd -T refuses to run with no host key, and a build machine need not have any (and the image's own are not
        # readable here): a throwaway key, made in the private work directory and gone with it, stands in.
        hostkey = Path(work) / "throwaway_host_key"
        p = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        require(p.returncode == 0, "could not make a throwaway host key for sshd -T: " + p.stderr.strip())
        for addr in ("127.0.0.1", "203.0.113.9", "::1"):
            p = subprocess.run([exe, "-T", "-f", str(main), "-h", str(hostkey), "-C", "user=droidian,host=localhost,addr=" + addr],
                               stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=15)
            require(p.returncode == 0, "sshd -T rejected image configuration: " + p.stderr.strip())
            effective = dict((line.split(None, 1)[0].lower(), line.split(None, 1)[1]) for line in p.stdout.splitlines() if " " in line)
            require(effective.get("pubkeyauthentication") == "yes", "effective SSH configuration disables public keys")
            if recovery_key is None:
                require(effective.get("passwordauthentication") == "no" and effective.get("kbdinteractiveauthentication") == "no",
                        "effective SSH configuration is not key-only")
            else:
                fields = next(line.split(":") for line in (root / "etc/passwd").read_text().splitlines() if line.startswith("droidian:"))
                paths = []
                for value in shlex.split(effective.get("authorizedkeysfile", "")):
                    value = re.sub(r"%[%huU]", lambda m: {"%%": "%", "%h": fields[5], "%u": "droidian", "%U": fields[2]}[m[0]], value)
                    if not value.startswith("/"):
                        value = fields[5] + "/" + value
                    paths.append(value)
                require(str(recovery_key.relative_to(root)) in [p.lstrip("/") for p in paths],
                        "AuthorizedKeysFile does not use the owner recovery key")


def sudo(root):
    root = Path(root).resolve()
    main = root / "etc/sudoers"
    require(main.is_file(), "missing sudoers policy")
    require(not (root / "etc/sudoers.d").is_symlink(), "linked sudoers policy directory")
    for path in [main] + sorted((root / "etc/sudoers.d").glob("*")):
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root), "unsupported sudoers policy file")
        for line in path.read_text().replace("\\\n", "").splitlines():
            line = line.strip()
            if re.match(r"^[@#]include", line):
                require(path == main and re.fullmatch(r"[@#]includedir\s+/etc/sudoers.d\s*(?:#.*)?", line),
                        "unsupported included sudoers policy")
            elif line and not line.startswith("#"):
                require(not (line.startswith("Defaults") and re.search(r"!\s*authenticate\b|\bexempt_group\b", line)),
                        "sudoers Defaults disables authentication")
                if "NOPASSWD" in line:
                    require(path == root / "etc/sudoers.d/90-opos-dev" and line == "droidian ALL=(ALL) NOPASSWD:ALL",
                            "non-OPOS passwordless sudo policy")


SSH_UNITS = ("ssh.service", "sshd.service", "ssh.socket", "sshd.socket")


def ssh_enablements(root):
    for path in (root / "etc/systemd/system").glob("*/*"):
        if path.parent.name.endswith((".wants", ".requires")) and path.name in SSH_UNITS:
            yield path


def disable_ssh(root):
    """Implement the explicit no-access choice in the output image, without running systemd."""
    root = Path(root)
    for path in ssh_enablements(root):
        path.unlink()
    for name in SSH_UNITS:
        path = root / "etc/systemd/system" / name
        if os.path.lexists(path):
            path.unlink()
        path.symlink_to("/dev/null")
    marker = root / "etc/opos/no-ssh"
    marker.write_text("OPOS_NO_SSH=1: no SSH access; services and sockets disabled and masked\n")
    marker.chmod(0o644)


def recovery(root):
    root = Path(root).resolve()
    fields = next(line.split(":") for line in (root / "etc/passwd").read_text().splitlines() if line.startswith("droidian:"))
    uid, gid, home = int(fields[2]), int(fields[3]), fields[5]
    require(home.startswith("/") and ".." not in Path(home).parts and home != "/", "invalid droidian home")
    key = root / home.lstrip("/") / ".ssh/authorized_keys"
    if (root / "etc/opos/no-ssh").exists():
        require(not os.path.lexists(key), "no-SSH image still has authorized_keys")
        require(not list(ssh_enablements(root)), "no-SSH image still enables SSH")
        for name in SSH_UNITS:
            path = root / "etc/systemd/system" / name
            require(path.is_symlink() and os.readlink(path) == "/dev/null", "no-SSH image must mask " + name)
        return
    for path in [root / home.lstrip("/"), key.parent, key]:
        require(path.exists() and not path.is_symlink(), "missing or linked SSH recovery path")
        s = path.stat()
        require(s.st_uid == uid and not s.st_mode & 0o022, "SSH recovery path has unsafe ownership or permissions")
        require(s.st_mode & (0o100 if path.is_dir() else 0o400), "SSH recovery path is not accessible")
    parent = key.parent.parent.parent
    while True:
        require(parent.resolve().is_relative_to(root) and not parent.is_symlink(), "linked SSH recovery ancestor")
        s = parent.stat()
        require(s.st_uid in (0, uid) and not s.st_mode & 0o022, "unsafe SSH recovery ancestor")
        require(s.st_mode & (0o100 if s.st_uid == uid else 0o010 if s.st_gid == gid else 0o001),
                "SSH recovery ancestor is not traversable")
        if parent == root:
            break
        parent = parent.parent
    public_key(key)
    daemon = root / "usr/sbin/sshd"
    require(daemon.is_file() and os.access(daemon, os.X_OK), "sshd is not installed")
    unit = root / "lib/systemd/system/ssh.service"
    if not unit.exists():
        unit = root / "usr/lib/systemd/system/ssh.service"
    override = root / "etc/systemd/system/ssh.service"
    require(not override.is_symlink() or os.readlink(override) != "/dev/null", "ssh.service is masked")
    if override.is_file() and not override.is_symlink():
        unit = override
    require(unit.is_file() and re.search(r"(?m)^ExecStart=/usr/sbin/sshd(?:\s|$)", unit.read_text()), "unsupported ssh.service startup")
    wants = root / "etc/systemd/system/multi-user.target.wants/ssh.service"
    require(wants.is_symlink() and os.readlink(wants) in ("/lib/systemd/system/ssh.service", "/usr/lib/systemd/system/ssh.service", "/etc/systemd/system/ssh.service"),
            "ssh.service is not enabled")
    require(not list((root / "etc/systemd/system/ssh.service.d").glob("*.conf")), "unsupported SSH startup overrides")
    require(not (root / "etc/ssh/sshd_not_to_be_run").exists(), "SSH startup is disabled")
    ssh(root, recovery_key=key)


def recovery_paths(root):
    root = Path(root).resolve()
    home = next(line.split(":")[5] for line in (root / "etc/passwd").read_text().splitlines() if line.startswith("droidian:"))
    require(home.startswith("/") and ".." not in Path(home).parts and home != "/", "invalid droidian home")
    path = root
    for part in Path(home).parts[1:] + (".ssh", "authorized_keys"):
        path /= part
        require(not path.is_symlink(), "linked SSH recovery path")
        require(path.resolve().is_relative_to(root), "SSH recovery path escapes image")


def release(root):
    root = Path(root)
    build_mode(root, "1")
    for rel in ("etc/default/opos-kiosk", "etc/sudoers.d/90-opos-dev", "etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf",
                "etc/systemd/system/multi-user.target.wants/opos-clock-set.path"):
        require(not os.path.lexists(root / rel), "release image contains dev state: " + rel)
    require((root / "etc/opos/usb-shell.conf").read_text() == "enabled=0\n", "release image has a USB shell")


def release_inputs(_):
    require(not os.environ.get("OPOS_DEV_SSH_PUBKEY"), "OPOS_RELEASE=1 refuses inherited development inputs: OPOS_DEV_SSH_PUBKEY is a path (including 0)")
    require(not any(name.startswith("OPOS_DEV_") and value not in ("", "0") for name, value in os.environ.items()),
            "OPOS_RELEASE=1 refuses inherited development inputs")


def image_sha256(image):
    digest = hashlib.sha256()
    with open(image, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_base(image):
    observed = image_sha256(image)
    allowlist = Path(__file__).resolve().with_name("release-bases.sha256")
    entries = Path(allowlist).read_text().splitlines()
    require(all(re.fullmatch(r"[0-9a-f]{64}  \S.*", line) for line in entries),
            "release base list must contain one 'sha256  description' per line; saw base SHA-256 " + observed)
    require(any(line[:64] == observed for line in entries), "release base SHA-256 is not listed: " + observed)
    print(observed)


def print_sha256(image):
    print(image_sha256(image))


def build_mode(root, release_mode="0"):
    require(release_mode in ("0", "1"), "OPOS_RELEASE must be 0 or 1")
    marker = Path(root) / "etc/opos/build-mode"
    require(marker.is_file() and not marker.is_symlink(), "missing or linked image build mode")
    mode = marker.read_text()
    require(mode in ("release\n", "dev\n"), "invalid image build mode")
    require(release_mode != "1" or mode == "release\n", "release verification refuses an image marked dev")


def output_parent(out):
    parent = Path(out).parent.resolve(strict=True)
    s = parent.stat()
    uid = int(os.environ.get("SUDO_UID", str(os.getuid())))
    require(parent.is_dir() and s.st_uid in (0, uid) and not s.st_mode & 0o022,
            "refusing build: OUT parent must be owned by root or the invoking user and not writable by group or others: " + str(parent))
    for ancestor in parent.parents:
        mode = ancestor.stat().st_mode
        require(not mode & 0o002 or mode & 0o1000,
                "refusing build: OUT ancestor is writable by others without the sticky bit: " + str(ancestor))
    print(parent)


def check_output(out):
    out = Path(out)
    require(not os.path.lexists(out), "OUT already exists (including a symlink): " + str(out))
    require(out.parent.is_dir() and os.access(out.parent, os.W_OK | os.X_OK, effective_ids=True),
            "OUT directory is not writable by the builder: " + str(out.parent))


def publish(image, out):
    """One atomic rename, refusing even an OUT created after the preflight check.

    Linux is the image builder; Darwin supports the same boundary in fixture tests.
    Fail closed if the host or filesystem does not provide an exclusive rename.
    """
    check_output(out)
    libc = ctypes.CDLL(None, use_errno=True)
    source, destination = os.fsencode(image), os.fsencode(out)
    if sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, source, -100, destination, 1)  # AT_FDCWD, RENAME_NOREPLACE
    elif sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (source, destination, 4)  # RENAME_EXCL
    else:
        raise ValueError("host lacks an atomic no-replace rename")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(out))


def release_base_layout(root):
    root = Path(root)
    # Main writes a plain os-release with ID=opos. Do not execute shell from a base.
    os_release = root / "etc/os-release"
    opos_id = (os_release.is_file() and not os_release.is_symlink() and
               any(shlex.split(line, comments=True) == ["ID=opos"] for line in os_release.read_text().splitlines()))
    # These identify this builder's output, even if its hash is accidentally admitted.
    require(not opos_id and not os.path.lexists(root / "etc/opos/image.json") and
            not os.path.lexists(root / "opt/opos-releases") and
            not (root / "opt/opos").is_symlink(),
            "release base is already an OPOS image (image.json, ID=opos or OPOS release layout); use the pinned community base")


if __name__ == "__main__":
    try:
        {"key": public_key, "ssh": ssh, "sudo": sudo, "recovery": recovery, "recovery-paths": recovery_paths,
         "release": release, "release-inputs": release_inputs, "disable-ssh": disable_ssh,
         "release-base": release_base, "image-sha256": print_sha256, "release-base-layout": release_base_layout,
         "build-mode": build_mode, "output-parent": output_parent, "check-output": check_output, "publish": publish}[sys.argv[1]](*sys.argv[2:])
    except (OSError, ValueError, AttributeError, StopIteration, subprocess.SubprocessError) as e:
        print("image-policy: " + str(e), file=sys.stderr)
        sys.exit(1)
