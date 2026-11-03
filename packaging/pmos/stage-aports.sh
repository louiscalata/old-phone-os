#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
out_dir=$1
root=$(cd "$(dirname "$0")/../.." && pwd)
for pkg in opos postmarketos-ui-opos; do
mkdir -p "$out_dir/main/$pkg"
cp -R "$root/packaging/pmos/$pkg/." "$out_dir/main/$pkg/"
done
cp "$root/deploy/opos-sysusers.conf" "$out_dir/main/opos/"
for f in opos-firstboot.sh opos-kiosk-off.sh; do
if [ -f "$root/packaging/pmos/easy/$f" ]; then
cp "$root/packaging/pmos/easy/$f" "$out_dir/main/opos/"
fi
done
if [ "${OPOS_LOCAL_SOURCE:-}" = "1" ]; then
# Read literal metadata without evaluating the recipe as shell code.
metadata=$(python3 - "$out_dir/main/opos/APKBUILD" <<'PYMETA'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
values = []
for key in ("_source_archive", "_source_root"):
    declarations = re.findall(
        r"(?m)^[ \t]*(?:(?:export|readonly)[ \t]+)?"
        + re.escape(key) + r"[ \t]*(?:\+)?=.*$", text)
    pattern = re.escape(key) + r'="([A-Za-z0-9][A-Za-z0-9._-]*)"'
    match = re.fullmatch(pattern, declarations[0]) if len(declarations) == 1 else None
    if match is None or match[1] in (".", ".."):
        raise SystemExit("stage-aports: invalid literal " + key)
    values.append(match[1])
if not values[0].endswith(".tar.gz"):
    raise SystemExit("stage-aports: source archive must end in .tar.gz")
print(" ".join(values))
PYMETA
)
archive=${metadata%% *}
source_root=${metadata#* }
python3 "$root/packaging/pmos/make-runtime-source.py" --root "$root" --output "$out_dir/main/opos/$archive" --archive-root "$source_root"
# Bind the staged recipe to the actual declared runtime-input bytes.
# The complete corresponding-source handoff is a separate full-source asset.
python3 - "$out_dir/main/opos/APKBUILD" "$out_dir/main/opos/$archive" <<'PYSTAGE'
from pathlib import Path
import hashlib
import re
import sys
recipe = Path(sys.argv[1])
archive = Path(sys.argv[2])
text = recipe.read_text(encoding="utf-8")
text, source_count = re.subn(
    r'(?m)^(source=")\$_source_archive::[^ "\n]+',
    r'\1$_source_archive', text)
digest = hashlib.sha512(archive.read_bytes()).hexdigest()
text, checksum_count = re.subn(
    r'(?m)^[0-9a-f]{128}  \$_source_archive$',
    digest + "  $_source_archive", text)
if source_count != 1 or checksum_count != 1:
    raise SystemExit("stage-aports: invalid source/checksum archive contract")
recipe.write_text(text, encoding="utf-8")
PYSTAGE
fi
printf 'stage-aports: wrote %s\n' "$out_dir"
