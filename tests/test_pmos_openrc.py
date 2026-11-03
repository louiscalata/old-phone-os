# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path
class TestOpenrc(unittest.TestCase):
    def test_initd_contract(self):
        r = Path(__file__).resolve().parents[1]
        t = (r / "packaging/pmos/opos/opos-server.initd").read_text(encoding="utf-8")
        e = ["#!/sbin/openrc-run", "# SPDX-License-Identifier: GPL-3.0-or-later", 'name="opos-server"', 'description="Old Phone OS server"']
        e += ['command="/usr/bin/python3"', 'command_args="-m opos.web --port 8765"', 'command_user="opos:opos"', 'export HOME="/var/lib/opos"', 'export OPOS_CONFIG_DIR="/var/lib/opos/.config/opos"', "command_background=true"]
        e += ['pidfile="/run/opos-server.pid"', 'output_log="/var/log/opos-server.log"', 'error_log="/var/log/opos-server.log"', "depend() {"]
        e += ["    need dbus", "    use modemmanager net", "}", '_opos_state_directory() {', '    if [ -L "$1" ] || { [ -e "$1" ] && [ ! -d "$1" ]; }; then', '        eerror "$1 must be a real directory"', '        return 1', '    fi', '    checkpath -d -o "$2" -m "$3" "$1"', '}', "start_pre() {"]
        e += ["    # Keep the first-boot marker's parent root-owned; only settings are writable.", '    _opos_state_directory "$HOME" root:root 0755 || return 1', '    _opos_state_directory "$HOME/.config" root:root 0755 || return 1', '    _opos_state_directory "$OPOS_CONFIG_DIR" opos:opos 0750 || return 1']
        e += ["    # Preserve existing choices rather than treating unreadable prefs as empty.", '    if [ -e "$OPOS_CONFIG_DIR/prefs.json" ] || [ -L "$OPOS_CONFIG_DIR/prefs.json" ]; then', '        if [ -L "$OPOS_CONFIG_DIR/prefs.json" ] || [ ! -f "$OPOS_CONFIG_DIR/prefs.json" ] ||', '''            ! /usr/bin/python3 -c 'import os,pwd,sys; u=pwd.getpwnam("opos"); os.setgroups([u.pw_gid]); os.setgid(u.pw_gid); os.setuid(u.pw_uid); sys.exit(not os.access(sys.argv[1], os.R_OK))' "$OPOS_CONFIG_DIR/prefs.json"; then''', '            eerror "Existing OPOS preferences are not a regular file readable by opos; preserve and repair them before starting"', '            return 1', '        fi', '    fi']
        e += ['    checkpath -f -o opos:opos -m 0600 "$output_log"', "}"]
        self.assertEqual(t.splitlines(), e)
if __name__ == "__main__":
    unittest.main()
