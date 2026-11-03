#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
step_updates() {
  write_file /etc/apt/apt.conf.d/20auto-upgrades 0644 <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  write_file /etc/apt/apt.conf.d/52opos-unattended-upgrades 0644 <<'EOF'
// Only Debian security updates are applied automatically; Droidian and kernel packages are not.
Unattended-Upgrade::Origins-Pattern { "origin=Debian,codename=${distro_codename}-security,label=Debian-Security"; };
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "false";
Unattended-Upgrade::Mail "";
EOF
  write_file /etc/sysctl.d/90-opos-hardening.conf 0644 <<'EOF'
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.tcp_syncookies = 1
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
EOF
  run sysctl --system
  if have_cmd dpkg && ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
    if [ "$INSTALL_PKGS" = "1" ]; then
      run apt-get install -y unattended-upgrades
    else
      warn "unattended-upgrades is not installed; rerun with --install-packages after the owner approves"
    fi
  fi
  say "updates: unattended security updates and sysctl hardening configured"
}
