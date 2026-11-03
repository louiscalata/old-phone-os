#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare fixed package state at install time; check it as the service user."""
import argparse
import os
import pwd
import stat
import sys
from pathlib import Path

HOME = Path("/var/lib/opos")


class StateError(RuntimeError):
    """Unsafe or inaccessible package state must be repaired explicitly."""


def _metadata(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _directories(home, owner, root_owner):
    return ((home, root_owner, 0o755),
            (home / ".config", root_owner, 0o755),
            (home / ".config/opos", owner, 0o750))


def _check_directories(home, owner, root_owner, missing_ok):
    for path, expected_owner, mode in _directories(home, owner, root_owner):
        metadata = _metadata(path)
        if metadata is None:
            if missing_ok:
                continue
            raise StateError(f"Missing package state directory: {path}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise StateError(f"Package state must be a real directory: {path}")
        if ((metadata.st_uid, metadata.st_gid) != expected_owner
                or stat.S_IMODE(metadata.st_mode) != mode):
            raise StateError(f"Unexpected package state owner or mode: {path}")


def _readable_as_owner(path, owner):
    uid, gid = owner
    if os.geteuid() == uid and os.getegid() == gid:
        return os.access(path, os.R_OK, follow_symlinks=False)
    if os.geteuid() != 0:
        raise StateError("Only the package installer may check another identity")
    # The root package hook never reads preference bytes. Check access in a
    # short-lived child only after dropping supplementary groups, gid and uid.
    child = os.fork()
    if child == 0:
        try:
            os.setgroups([gid])
            os.setgid(gid)
            os.setuid(uid)
            os._exit(0 if os.access(path, os.R_OK, follow_symlinks=False) else 1)
        finally:
            # A failed credential drop/access check must never return into the
            # root installer in this forked child, including on interruption.
            os._exit(1)
    _, result = os.waitpid(child, 0)
    return os.WIFEXITED(result) and os.WEXITSTATUS(result) == 0


def _check_files(home, owner, root_owner, readable):
    prefs = home / ".config/opos/prefs.json"
    metadata = _metadata(prefs)
    if metadata is not None:
        if not stat.S_ISREG(metadata.st_mode):
            raise StateError(f"Existing preferences must be a regular file: {prefs}")
        if not readable(prefs):
            raise StateError(f"Existing preferences are unreadable by opos: {prefs}")
    marker = home / "firstboot-done"
    metadata = _metadata(marker)
    if metadata is not None and (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_uid, metadata.st_gid) != root_owner):
        raise StateError(f"First-boot marker must remain a root-owned regular file: {marker}")


def check_state(home, owner, *, root_owner=(0, 0), readable=None):
    """Read-only validation; never creates or repairs files/directories."""
    _check_directories(home, owner, root_owner, missing_ok=False)
    _check_files(home, owner, root_owner,
                 readable or (lambda path: _readable_as_owner(path, owner)))


def prepare_state(home, owner, *, root_owner=(0, 0), readable=None):
    """Create only missing fixed directories; preserve existing state exactly."""
    read_check = readable or (lambda path: _readable_as_owner(path, owner))
    # Check every existing component before the first write. Do not recursively
    # chown state, adopt unsafe directories, or alter unreadable preferences.
    _check_directories(home, owner, root_owner, missing_ok=True)
    _check_files(home, owner, root_owner, read_check)
    for path, expected_owner, mode in _directories(home, owner, root_owner):
        if _metadata(path) is None:
            path.mkdir(mode=mode)
            if (path.stat().st_uid, path.stat().st_gid) != expected_owner:
                os.chown(path, *expected_owner)
            path.chmod(mode)
    check_state(home, owner, root_owner=root_owner, readable=read_check)


def main(argv=None):
    # These are explicit options rather than caller-selected paths/identities.
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        user = pwd.getpwnam("opos")
        owner = (user.pw_uid, user.pw_gid)
        if args.prepare:
            if os.geteuid() != 0:
                raise StateError("Package state preparation requires the root package installer")
            prepare_state(HOME, owner)
        else:
            if (os.geteuid(), os.getegid()) != owner:
                raise StateError("Package startup validation must run as opos:opos")
            check_state(HOME, owner)
    except (OSError, KeyError, StateError) as error:
        print(f"opos-systemd-state: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
