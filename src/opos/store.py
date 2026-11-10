# SPDX-License-Identifier: GPL-3.0-or-later
"""Local files for everyday apps. JSON under the state directory, mode 0600, no network.

The directory is $XDG_STATE_HOME/opos, or the default state directory when that variable is unset.
A file that cannot be read is moved aside to <name>.bad-<time> and the caller starts empty.
"""
import json
import os
import re
import time
from pathlib import Path

SCHEMA = 1
MAX_JSON = 256 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\.jsonl?$")


def default_root():
    """The OPOS state directory. Tests pass their own directory and never use this."""
    base = os.environ.get("XDG_STATE_HOME")
    if not base:
        base = str(Path.home() / ".local" / "state")
    return Path(base) / "opos"


def _check_name(name):
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValueError("bad name")
    return name


class Store:
    """One directory of small JSON files. Writes are a temp file plus os.replace."""

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_root()
        self._said = set()

    def _path(self, name):
        return self.root / _check_name(name)

    def _mkdir(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def _quarantine(self, path):
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        dest = path.with_name(path.name + ".bad-" + stamp)
        n = 0
        while dest.exists():
            n += 1
            dest = path.with_name(path.name + ".bad-" + stamp + "-" + str(n))
        try:
            os.replace(path, dest)
        except OSError:
            return
        return dest

    def note_once(self, name):
        """A recovery sentence the first time name was unreadable in this process, else ''."""
        if name in self._said:
            return ""
        self._said.add(name)
        return "The saved file could not be read. Started empty."

    def read_json(self, name, default):
        """(value, note). note is empty when the file was fine or absent."""
        path = self._path(name)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return default, ""
        except OSError:
            self._quarantine(path)
            return default, self.note_once(name)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            self._quarantine(path)
            return default, self.note_once(name)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            self._quarantine(path)
            return default, self.note_once(name)
        return data, ""

    def write_json(self, name, payload, limit=MAX_JSON):
        """Write payload with the schema field. Raises ValueError when the file would be over limit."""
        if not isinstance(payload, dict):
            raise ValueError("bad payload")
        body = dict(payload)
        body["schema"] = SCHEMA
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > limit:
            raise ValueError("too big")
        self._mkdir()
        path = self._path(name)
        tmp = path.with_name("." + path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return path

    def read_jsonl(self, name, limit=500):
        """A list of objects, oldest first. A bad line is skipped. A bad file is moved aside."""
        path = self._path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], ""
        except (OSError, UnicodeError):
            self._quarantine(path)
            return [], self.note_once(name)
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        if len(rows) > limit:
            rows = rows[-limit:]
        return rows, ""

    def write_jsonl(self, name, rows, limit=500, line_limit=4000):
        """Replace the file with the last limit rows. Each row must be a dict."""
        if not isinstance(rows, list):
            raise ValueError("bad rows")
        kept = rows[-limit:]
        lines = []
        for row in kept:
            if not isinstance(row, dict):
                raise ValueError("bad row")
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            if len(line.encode("utf-8")) > line_limit:
                raise ValueError("too big")
            lines.append(line)
        raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        self._mkdir()
        path = self._path(name)
        tmp = path.with_name("." + path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return path

    def write_bytes(self, name, raw, limit):
        """Atomic write of raw bytes under a safe name. name may be notes/<id>.txt via a flat name only.

        This method accepts the same flat names as JSON, plus .txt. Callers that need a directory
        of notes use one file per note with a flat name such as note-abc.txt.
        """
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}\.(txt|wav|ics|vcf)", name):
            raise ValueError("bad name")
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > limit:
            raise ValueError("too big")
        self._mkdir()
        path = self.root / name
        tmp = path.with_name("." + path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, bytes(raw))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return path
