# SPDX-License-Identifier: GPL-3.0-or-later
import re
from pathlib import Path


def apkbuild_vars(path: str) -> dict[str, str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        opener = re.match(r'^([a-z_][a-z0-9_]*)="([^"]*)$', lines[i])
        if opener:
            pieces = [opener.group(2)]
            i += 1
            while i < len(lines):
                pieces.append(lines[i])
                if '"' in lines[i]:
                    break
                i += 1
            stripped = [p.strip() for p in pieces]
            stripped[-1] = stripped[-1].replace('"', '')
            result[opener.group(1)] = " ".join(p for p in stripped if p)
        else:
            single = re.match(r'^([a-z_][a-z0-9_]*)="?([^"\n]*)"?$', lines[i])
            if single:
                result[single.group(1)] = single.group(2)
        i += 1
    return result


def function_body(path: str, name: str) -> str:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    start = None
    pattern = "^" + re.escape(name) + r"\(\) \{$"
    for idx, line in enumerate(lines):
        if re.match(pattern, line):
            start = idx
            break
    if start is None:
        raise ValueError(name)
    for idx in range(start + 1, len(lines)):
        if lines[idx] == "}":
            return "\n".join(lines[start:idx + 1])
    raise ValueError(name)
