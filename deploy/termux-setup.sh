#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Runs inside Termux on stock Android (typed there by deploy/duo-termux.ps1). Installs Python,
# Termux:API tools and llama.cpp from Termux's own repository, unpacks the pushed OPOS source
# into ~/opos, and starts OPOS on the phone's real battery plus an optional Llama 3.2 server.
# Nothing outside Termux's app storage and /sdcard/Download/opos is touched; uninstalling the
# Termux app removes it all.
set -eu
drop=/sdcard/Download/opos
log="$drop/setup.log"
status() { printf '%s\n' "$1" > "$drop/status"; printf '%s %s\n' "$(date '+%H:%M:%S')" "$1" >> "$log"; }
exec >> "$log" 2>&1
status "installing packages"
export DEBIAN_FRONTEND=noninteractive
yes | pkg upgrade -y -o Dpkg::Options::=--force-confnew
pkg install -y python termux-api llama-cpp
status "unpacking source"
if [ -d "$HOME/opos" ]; then
  mv "$HOME/opos" "$HOME/opos.bak-$(date +%Y%m%d%H%M%S)"
fi
mkdir -p "$HOME/opos" "$HOME/models" "$HOME/bin"
tar -xzf "$drop/opos-src.tar.gz" -C "$HOME/opos"
if [ -f "$drop/model.gguf" ]; then
  # Internal storage, not /sdcard: llama.cpp maps the file and FUSE-backed /sdcard is slow.
  cp -f "$drop/model.gguf" "$HOME/models/model.gguf"
fi
cat > "$HOME/bin/opos-start" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
# Start (or restart) OPOS and, when a model is present, the Llama server it verifies by id.
pkill -f 'opos.web' 2>/dev/null || true
pkill -f 'llama-server' 2>/dev/null || true
termux-wake-lock
if [ -f "$HOME/models/model.gguf" ]; then
  nohup llama-server -m "$HOME/models/model.gguf" --alias "${OPOS_LLAMA_ID:-llama-3.2-1b-instruct}" \
    --host 127.0.0.1 --port 8080 -c 2048 -t "${OPOS_LLAMA_THREADS:-4}" > "$HOME/llama.log" 2>&1 &
fi
cd "$HOME/opos"
OPOS_ON_PHONE=1 OPOS_BATTERY=live PYTHONPATH=src nohup python -m opos.web --port 8765 --live-battery > "$HOME/opos.log" 2>&1 &
EOF
chmod +x "$HOME/bin/opos-start"
status "starting"
"$HOME/bin/opos-start"
i=0
until python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/", timeout=3)' 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    status "failed: opos did not answer on 8765"
    tail -n 30 "$HOME/opos.log" >> "$log" || true
    exit 1
  fi
  sleep 1
done
termux-battery-status > "$drop/battery.json" 2>&1 || true
status "ready"
