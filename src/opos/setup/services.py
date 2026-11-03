# SPDX-License-Identifier: GPL-3.0-or-later
"""Render and install user-level services for Old Phone OS Interface."""
from __future__ import annotations

import math
import os
import plistlib
import subprocess
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

UNIT_NAME = "opos-server.service"
LAUNCHD_LABEL = "org.oldphoneos.opos"
SYSTEM_ONLY = frozenset({"User", "Group", "StateDirectory", "StateDirectoryMode", "InaccessiblePaths", "ReadOnlyPaths", "ProtectHome", "PrivateDevices", "IPAddressDeny", "IPAddressAllow", "MemoryDenyWriteExecute", "SystemCallFilter", "ProtectProc", "ProcSubset", "ProtectClock", "ProtectKernelLogs", "RestrictRealtime", "CapabilityBoundingSet", "AmbientCapabilities"})
# Model-route settings copied from the setup environment into the installed
# service. Keys, tokens and other secrets are never on this list.
ROUTE_SETTINGS = ("OPOS_GLIMMER_URL", "OPOS_LAN_URL", "OPOS_LOCAL_URL", "OPOS_GLIMMER_ROUTING", "OPOS_MUSE_TIMEOUT")
Runner = Callable[[Sequence[str]], tuple[int, str]]


class ServiceError(RuntimeError):
    """Raised when a service manager command fails."""

    def __init__(self, message: str = "", actions: Sequence[str] | None = None) -> None:
        """Store message and partial actions collected before the failure."""
        super().__init__(message)
        self.actions: list[str] = list(actions) if actions is not None else []


def checkout_root(module_file: Path, *, require_template: bool = True) -> Path:
    """Resolve the retained source layout, never an installed site-packages root.

    The APK's system service owns its settings and lifecycle. Its Python CLI
    must not create a second per-user service or write a different config.
    """
    module = Path(module_file).resolve()
    root = module.parents[3] if len(module.parents) >= 4 else None
    if root is None or module.parent != root / "src" / "opos" / "setup" or not module.is_file():
        raise ServiceError(
            "The per-user CLI requires a retained source checkout. "
            "For postmarketOS APK installations, use kiosk Settings for available "
            "preferences. Have the system administrator manage the existing "
            "system service; remove OPOS with the package manager. "
            "No configuration or services were changed."
        )
    if require_template and not (root / "deploy" / UNIT_NAME).is_file():
        raise ServiceError(
            "The source checkout is missing deploy/opos-server.service. "
            "Restore the retained sources before running setup. "
            "No configuration or services were changed."
        )
    return root


def _validate_path(value: str | Path, systemd: bool = True) -> str:
    """Return a path as text, rejecting systemd specifier injection risks."""
    text = str(value)
    if systemd:
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == "%" for ch in text):
            raise ValueError(f"unsafe path: {text!r}")
    else:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            raise ValueError(f"unsafe path: {text!r}")
    return text


def _route_url_problem(name: str, value: str) -> str | None:
    """Return why a route URL is not copied, using the runtime's own checks."""
    from opos import assist, muse  # the runtime validators the service will apply

    if any(ord(ch) <= 32 or ord(ch) >= 127 for ch in value):
        return f"{name} contains spaces, control or non-ASCII characters"
    try:
        parts = urllib.parse.urlsplit(value)
        has_login = parts.username is not None or parts.password is not None
    except ValueError:
        return f"{name} is not a valid URL"
    if has_login:
        return f"{name} contains a user name or password, which setup does not write to the service"
    if "?" in value or "#" in value:
        return f"{name} has a query or fragment"
    if name == "OPOS_LAN_URL":
        route = assist.lan_route({name: value})
        if route is None or route.base_url != value:
            return f"{name} must be an http:// URL with a private IP address"
        return None
    if name == "OPOS_LOCAL_URL":
        route = assist.local_route({name: value}, free=assist.MIN_MB_3B)
    else:
        route = muse.glimmer_route({name: value})
    if route is None or route.base_url != value:
        return f"{name} must be an http:// URL on 127.0.0.1 or localhost"
    return None


def route_environment(env: Mapping[str, str] | None) -> tuple[dict[str, str], list[str]]:
    """Return the model-route settings the installed service keeps, and notes on any left out.

    Only names in ROUTE_SETTINGS are read, and only values the runtime accepts are
    kept, so the service uses the route the setup shell would use.
    """
    keep: dict[str, str] = {}
    notes: list[str] = []
    if not env:
        return keep, notes
    for name in ROUTE_SETTINGS:
        value = env.get(name)
        if value is None or value == "":
            continue
        if name == "OPOS_GLIMMER_ROUTING":
            # web.py turns Glimmer routing off only for exactly "0"; other values mean the default.
            if value == "0":
                keep[name] = "0"
            continue
        if name == "OPOS_MUSE_TIMEOUT":
            from opos import muse

            try:
                number = float(value)
            except ValueError:
                number = math.nan
            if not math.isfinite(number) or number <= 0:
                notes.append(f"{name} must be a positive number of seconds")
                continue
            seconds = muse.timeout_s({name: value})  # held between 5 and 600, as at run time
            keep[name] = str(int(seconds)) if seconds.is_integer() else repr(seconds)
            continue
        problem = _route_url_problem(name, value)
        if problem is None:
            keep[name] = value
        else:
            notes.append(problem)
    return keep, notes


def _systemd_environment_line(name: str, value: str) -> str:
    """Quote one Environment= assignment: backslash and quote are escaped, % is doubled for specifiers."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'Environment="{name}={escaped}"'


def read_unit_template(repo_root: Path) -> str:
    """Read the hardened system unit template from a repository checkout."""
    path = repo_root / "deploy" / "opos-server.service"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def render_user_unit(template: str, repo_root: Path, python: str, config_dir: Path, port: int = 8765, env: Mapping[str, str] | None = None) -> str:
    """Render the system unit template as a hardened user unit.

    env is the setup environment; only accepted model-route settings from it are written.
    """
    root = _validate_path(repo_root)
    interpreter = _validate_path(python)
    config = _validate_path(config_dir)
    route, _ = route_environment(env)
    sections: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    current = ""
    for raw in template.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current and "=" in line:
            key, value = line.split("=", 1)
            sections[current].append((key.strip(), value.strip()))
    rendered: list[str] = []
    for section in order:
        lines: list[str] = []
        env_done = False
        for key, value in sections[section]:
            if section == "Unit" and key in {"After", "Wants"} and "ModemManager" in value:
                continue
            if section == "Install" and key == "WantedBy":
                lines.append("WantedBy=default.target")
                continue
            if section != "Service":
                lines.append(f"{key}={value}")
                continue
            if key in SYSTEM_ONLY:
                continue
            if key == "ExecStart":
                lines.append(f"ExecStart={interpreter} -m opos.web --port {port} --token-file {config}/session.token")
            elif key == "WorkingDirectory":
                lines.append(f"WorkingDirectory={root}")
            elif key == "Environment":
                if not env_done:
                    lines.append(f"Environment=PYTHONPATH={root}/src")
                    lines.append("Environment=PYTHONDONTWRITEBYTECODE=1")
                    lines.append(f"Environment=OPOS_CONFIG_DIR={config}")
                    lines.extend(_systemd_environment_line(name, value) for name, value in route.items())
                    env_done = True
            else:
                lines.append(f"{key}={value}")
        if section == "Service":
            lines.append(f"ReadWritePaths={config}")
            lines.append("ProtectHome=read-only")
        rendered.append(f"[{section}]")
        rendered.extend(lines)
        rendered.append("")
    return "\n".join(rendered).rstrip("\n") + "\n"


def render_launchd_plist(repo_root: Path, python: str, config_dir: Path, port: int = 8765, env: Mapping[str, str] | None = None) -> str:
    """Render a launchd agent plist for the web service.

    env is the setup environment; only accepted model-route settings from it are written.
    """
    root = _validate_path(repo_root, systemd=False)
    interpreter = _validate_path(python, systemd=False)
    config = Path(_validate_path(config_dir, systemd=False))
    route, _ = route_environment(env)
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [interpreter, "-m", "opos.web", "--port", str(port), "--token-file", str(config / "session.token")],
        "WorkingDirectory": root,
        "EnvironmentVariables": {"PYTHONPATH": f"{root}/src", "PYTHONDONTWRITEBYTECODE": "1", "OPOS_CONFIG_DIR": str(config), **route},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(config / "logs" / "stdout.log"),
        "StandardErrorPath": str(config / "logs" / "stderr.log"),
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_XML).decode("utf-8")


def unit_dir(home: Path, platform: str) -> Path:
    """Return the user service directory for a platform."""
    if platform == "linux":
        return home / ".config" / "systemd" / "user"
    if platform == "darwin":
        return home / "Library" / "LaunchAgents"
    raise ValueError(f"unsupported platform: {platform}")


def default_runner(cmd: Sequence[str]) -> tuple[int, str]:
    """Run a command without a shell and return its code and combined output."""
    proc = subprocess.run(list(cmd), shell=False, capture_output=True, text=True, timeout=30, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def _write_atomic(path: Path, text: str) -> None:
    """Write text to a path atomically with mode 0o644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _run(runner: Runner, cmd: list[str], actions: list[str]) -> None:
    """Run one command, record it, and raise ServiceError on failure."""
    code, output = runner(cmd)
    if code != 0:
        raise ServiceError(output, actions=list(actions))
    actions.append(f"ran: {' '.join(cmd)}")


def install_services(repo_root: Path, python: str, config_dir: Path, home: Path, platform: str, port: int = 8765, dry_run: bool = False, runner: Runner | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    """Install and start the user service for the current platform.

    Accepted model-route settings in env (the setup environment) are written into the service.
    """
    if platform not in {"linux", "darwin"}:
        return [f"services not supported on {platform}: run python -m opos.web manually"]
    run = runner if runner is not None else default_runner
    unload = None
    notes = [f"not set in the service: {note}" for note in route_environment(env)[1]]
    if platform == "linux":
        path = unit_dir(home, platform) / UNIT_NAME
        text = render_user_unit(read_unit_template(repo_root), repo_root, python, config_dir, port, env=env)
        commands = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", UNIT_NAME]]
        if path.exists():
            # enable --now leaves an already running service on its old settings.
            commands.append(["systemctl", "--user", "restart", UNIT_NAME])
    else:
        path = unit_dir(home, platform) / f"{LAUNCHD_LABEL}.plist"
        text = render_launchd_plist(repo_root, python, config_dir, port, env=env)
        commands = [["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]]
        if path.exists():
            # launchd refuses to bootstrap a label that is still loaded, so a re-run unloads it first.
            unload = ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    if dry_run:
        pre = [f"would run: {' '.join(unload)}"] if unload else []
        return [f"would write {path}"] + notes + pre + [f"would run: {' '.join(cmd)}" for cmd in commands]
    # Write first: if the write fails, the running service stays loaded.
    _write_atomic(path, text)
    actions = [f"wrote {path}"] + notes
    if unload:
        code, _ = run(unload)
        actions.append(f"ran: {' '.join(unload)}" if code == 0 else "previous service was not loaded")
    for cmd in commands:
        _run(run, cmd, actions)
    if platform == "linux":
        actions.append('To start at boot without login, run: sudo loginctl enable-linger "$USER"')
    return actions


def uninstall_services(home: Path, platform: str, dry_run: bool = False, runner: Runner | None = None) -> list[str]:
    """Stop and remove the user service for the current platform."""
    if platform not in {"linux", "darwin"}:
        return [f"services not supported on {platform}: run python -m opos.web manually"]
    run = runner if runner is not None else default_runner
    if platform == "linux":
        path = unit_dir(home, platform) / UNIT_NAME
        if not path.exists():
            return [f"no service installed under {home}: nothing to stop"]
        stop = ["systemctl", "--user", "disable", "--now", UNIT_NAME]
        reload_cmd = ["systemctl", "--user", "daemon-reload"]
        if dry_run:
            return [f"would run: {' '.join(stop)}", f"would delete {path}", f"would run: {' '.join(reload_cmd)}"]
        actions = []
        try:
            _run(run, stop, actions)
        except ServiceError as exc:
            raise ServiceError(f"Service stop failed; kept {path}: {exc}", actions=exc.actions) from exc
        path.unlink(missing_ok=True)
        actions.append(f"deleted {path}")
        _run(run, reload_cmd, actions)
        return actions
    path = unit_dir(home, platform) / f"{LAUNCHD_LABEL}.plist"
    if not path.exists():
        return [f"no service installed under {home}: nothing to stop"]
    stop = ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    if dry_run:
        return [f"would run: {' '.join(stop)}", f"would delete {path}"]
    actions = []
    try:
        _run(run, stop, actions)
    except ServiceError as exc:
        # launchctl bootout also fails when the job is already absent. Check
        # the per-user domain before deciding whether it is safe to remove the
        # on-disk agent definition.
        probe = ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
        code, _ = run(probe)
        if code == 0:
            raise ServiceError(f"Service stop failed; kept {path}: {exc}", actions=exc.actions) from exc
        actions.append("service was not loaded")
    path.unlink(missing_ok=True)
    actions.append(f"deleted {path}")
    return actions
