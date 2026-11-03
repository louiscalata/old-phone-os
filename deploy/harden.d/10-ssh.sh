#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
ssh_key_ok() {
  local typ b64 len
  typ="$(printf '%s' "$1" | awk '{print $1}')"
  b64="$(printf '%s' "$1" | awk '{print $2}')"
  case "$typ" in ssh-ed25519|ssh-rsa|ecdsa-sha2-*|sk-*) : ;; *) return 1 ;; esac
  [ -n "$b64" ] || return 1
  printf '%s' "$b64" | grep -qE '^[A-Za-z0-9+/]+=*$' || return 1
  len="$(printf '%s' "$b64" | wc -c)"
  len="$(printf '%s' "$len" | tr -d '[:space:]')"
  if [ "$typ" = "ssh-ed25519" ]; then [ "$len" -ge 60 ] || return 1; else [ "$len" -ge 100 ] || return 1; fi
}
step_ssh() {
  local ssh_dir auth conf key_line count
  ssh_dir="$(p "/home/$OPOS_USER/.ssh")"
  auth="$(p "/home/$OPOS_USER/.ssh/authorized_keys")"
  conf="/etc/ssh/sshd_config.d/10-opos-hardening.conf"
  key_line=""
  if [ -n "${ADD_KEY:-}" ]; then
    key_line="$(grep -m 1 '[^[:space:]]' "$ADD_KEY" 2>/dev/null || true)"
    ssh_key_ok "$key_line" || die "not a public key"
    if [ "$TEST" != "1" ] && command -v ssh-keygen >/dev/null 2>&1; then
      ssh-keygen -l -f "$ADD_KEY" >/dev/null 2>&1 || die "not a public key"
    fi
    if [ -L "$ssh_dir" ] || [ -L "$auth" ]; then
      die "refusing symlink"
    fi
    if [ -e "$ssh_dir" ] && [ ! -d "$ssh_dir" ]; then
      die "ssh dir is not a directory"
    fi
    if [ "$APPLY" != "1" ]; then
      say "would append key to authorized_keys"
    else
      umask 077
      mkdir -p "$ssh_dir"
      chmod 0700 "$ssh_dir"
      touch "$auth"
      if ! grep -qxF -- "$key_line" "$auth" 2>/dev/null; then
        printf '%s\n' "$key_line" >> "$auth"
      fi
      chmod 0600 "$auth"
      if [ "$TEST" != "1" ]; then
        chown "$OPOS_USER:$OPOS_USER" "$ssh_dir"
        chown "$OPOS_USER:$OPOS_USER" "$auth"
      fi
    fi
  fi
  count="0"
  if [ -f "$auth" ] && [ ! -L "$auth" ]; then
    while IFS= read -r _line || [ -n "$_line" ]; do
      if ssh_key_ok "$_line"; then
        count="$((count + 1))"
      fi
    done < "$auth"
  fi
  if [ "$count" = "0" ] && [ -n "${ADD_KEY:-}" ] && ssh_key_ok "$key_line"; then
    count="1"
  fi
  if [ "$count" = "0" ]; then
    if [ "$APPLY" = "1" ]; then
      die "no authorized key: run again with --add-key FILE"
    fi
    warn "no authorized key for $OPOS_USER: password login stays enabled; run again with --add-key FILE"
    return 0
  fi
  write_file "$conf" "0644" <<EOF
# managed by opos harden-device.sh
# Wi-Fi exposure is blocked by the firewall step, not by sshd
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
PermitEmptyPasswords no
AllowUsers $OPOS_USER
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
ClientAliveInterval 60
ClientAliveCountMax 3
UseDNS no
EOF
  if [ "$APPLY" = "1" ] && [ "$TEST" != "1" ] && ! have_cmd sshd; then
    if [ -e "$BACKUP_DIR$conf" ]; then
      cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
    else
      rm -f "$(p "$conf")"
    fi
    die "sshd not found, change reverted"
  fi
  if have_cmd sshd; then
    if ! run sshd -t; then
      if [ -e "$BACKUP_DIR$conf" ]; then
        cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
      else
        rm -f "$(p "$conf")"
      fi
      die "sshd config test failed, change reverted"
    fi
    if [ "$APPLY" = "1" ] && [ "$TEST" != "1" ]; then
      local caddr cport saddr sport extra eff ok addr port o1 o2 o3 o4 orest o pa kia pub am dns_eff dns_count dns_ok
      caddr=""; cport=""; saddr=""; sport=""; extra=""; eff=""; ok="1"
      IFS=' ' read -r caddr cport saddr sport extra <<< "${SSH_CONNECTION:-}" || true
      if [ -z "$caddr" ] || [ -z "$cport" ] || [ -z "$saddr" ] || [ -z "$sport" ] || [ -n "$extra" ]; then
        ok="0"
      fi
      for addr in "$caddr" "$saddr"; do
        case "$addr" in
          *:*)
            printf '%s' "$addr" | grep -qE '^[0-9A-Fa-f:.]+$' || ok="0"
            printf '%s' "$addr" | grep -qE '[0-9A-Fa-f]' || ok="0"
            ;;
          *)
            if printf '%s' "$addr" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
              o1=""; o2=""; o3=""; o4=""; orest=""
              IFS='.' read -r o1 o2 o3 o4 orest <<< "$addr" || true
              if [ -z "$o1" ] || [ -z "$o2" ] || [ -z "$o3" ] || [ -z "$o4" ] || [ -n "$orest" ]; then
                ok="0"
              else
                for o in "$o1" "$o2" "$o3" "$o4"; do
                  case "$o" in
                    ""|*[!0-9]*) ok="0" ;;
                    *) if [ "$o" -gt 255 ]; then ok="0"; fi ;;
                  esac
                done
              fi
            else
              ok="0"
            fi
            ;;
        esac
      done
      for port in "$cport" "$sport"; do
        case "$port" in
          ""|*[!0-9]*|??????*) ok="0" ;;
          *) if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then ok="0"; fi ;;
        esac
      done
      if [ "$ok" != "1" ]; then
        if [ -e "$BACKUP_DIR$conf" ]; then
          cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
        else
          rm -f "$(p "$conf")"
        fi
        die "sshd effective config test failed, change reverted"
      fi
      if ! dns_eff="$(run sshd -T)"; then
        if [ -e "$BACKUP_DIR$conf" ]; then
          cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
        else
          rm -f "$(p "$conf")"
        fi
        die "sshd UseDNS is not explicitly no, change reverted"
      fi
      dns_count="$(printf '%s\n' "$dns_eff" | awk 'tolower($1)=="usedns" {c+=1} END {print c+0}')"
      dns_ok="$(printf '%s\n' "$dns_eff" | awk 'tolower($1)=="usedns" && NF==2 && tolower($2)=="no" {c+=1} END {print c+0}')"
      if [ "$dns_count" != "1" ] || [ "$dns_ok" != "1" ]; then
        if [ -e "$BACKUP_DIR$conf" ]; then
          cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
        else
          rm -f "$(p "$conf")"
        fi
        die "sshd UseDNS is not explicitly no, change reverted"
      fi
      if ! eff="$(run sshd -T -C "user=$OPOS_USER,host=$caddr,addr=$caddr,laddr=$saddr,lport=$sport")"; then
        if [ -e "$BACKUP_DIR$conf" ]; then
          cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
        else
          rm -f "$(p "$conf")"
        fi
        die "sshd effective config test failed, change reverted"
      fi
      pa="$(printf '%s\n' "$eff" | grep -i '^passwordauthentication[[:space:]]' | awk '{print $2}' | tr '[:upper:]' '[:lower:]' | head -n 1 || true)"
      kia="$(printf '%s\n' "$eff" | grep -i '^kbdinteractiveauthentication[[:space:]]' | awk '{print $2}' | tr '[:upper:]' '[:lower:]' | head -n 1 || true)"
      pub="$(printf '%s\n' "$eff" | grep -i '^pubkeyauthentication[[:space:]]' | awk '{print $2}' | tr '[:upper:]' '[:lower:]' | head -n 1 || true)"
      am="$(printf '%s\n' "$eff" | grep -i '^authenticationmethods[[:space:]]' | awk '{print $2}' | tr '[:upper:]' '[:lower:]' | head -n 1 || true)"
      if [ "$pa" != "no" ] || [ "$kia" != "no" ] || [ "$pub" != "yes" ] || [ "$am" != "publickey" ]; then
        if [ -e "$BACKUP_DIR$conf" ]; then
          cp -p "$BACKUP_DIR$conf" "$(p "$conf")"
        else
          rm -f "$(p "$conf")"
        fi
        die "sshd effective config test failed, change reverted"
      fi
    fi
    if have_cmd systemctl; then
      if systemctl cat ssh >/dev/null 2>&1; then
        run systemctl reload ssh
      else
        run systemctl reload sshd
      fi
    fi
  fi
  if [ "$APPLY" = "1" ]; then
    run passwd "$OPOS_USER" || die "passwd failed"
  else
    say "would ask you to set a new password for $OPOS_USER (the default is public)"
  fi
  say "ssh: key-only login, password login off"
}
