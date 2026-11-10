# SPDX-License-Identifier: GPL-3.0-or-later
"""Hash check for stock-build and bootloader strings. The plaintext is not stored here."""
import hashlib
import re
from pathlib import Path

# Length -> sha256 hex of the strings that must not return. Plaintext stays out of the tree.
BANNED = {
    11: frozenset({"1108c4f05db60c0febd22ce6c0ef53c91e72d33ca73a23617febb928fcc12fcd"}),
    12: frozenset({"0d92293a046c124bb0971dc0ee92b88afb68d6e808b48a48d8340a215ade9b7c"}),
    32: frozenset({"3e5d108abd3a7cb4652255338b47ea6b1114b961ef0020bc1caabeb507a1012d"}),
    36: frozenset({"01cccced2e99a10fa4c61150bae94b401253485e126a13e09a9bc07bf4fba517"}),
}
TOKEN = re.compile(r"[A-Za-z0-9_./:=-]{8,}")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def hits(text):
    """sha256 hexes of banned strings found in text. Empty when the text is clean."""
    found = []
    seen = set()
    for token in TOKEN.findall(text):
        raw = token.encode("utf-8")
        for length, digests in BANNED.items():
            if len(raw) < length:
                continue
            end = len(raw) - length + 1
            for index in range(end):
                digest = hashlib.sha256(raw[index:index + length]).hexdigest()
                if digest in digests and digest not in seen:
                    seen.add(digest)
                    found.append(digest)
    return found


def tracked_texts(root):
    """Text files under root, skipping VCS and bytecode."""
    root = Path(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".pyc", ".wav"}:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        yield path, data.decode("utf-8", "replace")
