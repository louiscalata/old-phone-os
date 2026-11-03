#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
fw_pick_backend() {
	FW_BACKEND=""
	FW_PROBED="0"
	local want="${OPOS_FW_BACKEND:-auto}"
	case "$want" in
		auto | nft | iptables) ;;
		*) die "bad OPOS_FW_BACKEND: $want" ;;
	esac
	local nft_bin="${OPOS_NFT:-nft}"
	local ipt_bin="${OPOS_IPTABLES_LEGACY:-iptables-legacy}"
	local ip6t_bin="${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}"
	local iptr_bin="${OPOS_IPTABLES_LEGACY_RESTORE:-iptables-legacy-restore}"
	local ip6tr_bin="${OPOS_IP6TABLES_LEGACY_RESTORE:-ip6tables-legacy-restore}"
	local do_probe="0"
	if [ "${TEST:-1}" = "0" ]; then
		do_probe="1"
	elif [ -n "${OPOS_NFT:-}" ] || [ -n "${OPOS_IPTABLES_LEGACY:-}" ] || [ -n "${OPOS_IP6TABLES_LEGACY:-}" ] || [ -n "${OPOS_IPTABLES_LEGACY_RESTORE:-}" ] || [ -n "${OPOS_IP6TABLES_LEGACY_RESTORE:-}" ]; then
		do_probe="1"
	fi
	if [ "$do_probe" = "0" ]; then
		if [ "$want" = "iptables" ]; then
			FW_BACKEND="iptables-legacy"
		else
			FW_BACKEND="nft"
		fi
		say "firewall: backend $FW_BACKEND"
		return 0
	fi
	FW_PROBED="1"
	local nft_ok="0"
	local nft_reason=""
	local ipt_ok="0"
	local ipt_reason=""
	if command -v "$nft_bin" >/dev/null 2>&1; then
		local nft_err=""
		nft_err="$("$nft_bin" list tables 2>&1 1>/dev/null)" || true
		if "$nft_bin" list tables >/dev/null 2>&1; then
			local nft_iface=""
			local nft_ifaces=""
			for nft_iface in $OPOS_USB_IFACES; do
				case "$nft_iface" in
					"" | *[!a-z0-9]* | ????????????????*) nft_ifaces=""; break ;;
				esac
				if [ -z "$nft_ifaces" ]; then
					nft_ifaces="\"$nft_iface\""
				else
					nft_ifaces="$nft_ifaces, \"$nft_iface\""
				fi
			done
			if [ -z "$nft_ifaces" ]; then
				nft_reason="nft ruleset validation failed: bad interface name"
			else
				local nft_tmp=""
				nft_tmp="$(mktemp)" || die "cannot create temp file"
				cat > "$nft_tmp" <<EOF
table inet opos_filter
delete table inet opos_filter
table inet opos_filter {
	chain input {
		type filter hook input priority 0; policy drop;
		ct state invalid drop;
		ct state established,related accept;
		iifname "lo" accept;
		iifname { $nft_ifaces } tcp dport 22 accept;
		iifname { $nft_ifaces } ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept;
		udp sport 67 udp dport 68 accept;
		ip protocol icmp icmp type { echo-request, destination-unreachable, time-exceeded } limit rate 5/second accept;
		meta l4proto ipv6-icmp icmpv6 type { destination-unreachable, packet-too-big, time-exceeded, nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept;
	}
	chain forward {
		type filter hook forward priority 0; policy drop;
	}
	chain output {
		type filter hook output priority 0; policy accept;
	}
}
EOF
				local nft_check_err=""
				nft_check_err="$("$nft_bin" -c -f "$nft_tmp" 2>&1 1>/dev/null)" || true
				if "$nft_bin" -c -f "$nft_tmp" >/dev/null 2>&1; then
					nft_ok="1"
				else
					nft_reason="$(printf '%s' "$nft_check_err" | head -n 1)"
					[ -n "$nft_reason" ] || nft_reason="nft ruleset validation failed"
				fi
				rm -f "$nft_tmp"
			fi
			if [ "$nft_ok" != "1" ] && [ "$APPLY" != "1" ] && [ "$(id -u)" != "0" ]; then
				case "$nft_reason" in
					*cannot\ probe*) : ;;
				esac
			fi
		else
			nft_reason="$(printf '%s' "$nft_err" | head -n 1)"
			[ -n "$nft_reason" ] || nft_reason="nft list tables failed"
			if [ "$APPLY" != "1" ] && [ "$(id -u)" != "0" ]; then
				nft_reason="cannot probe (not root)"
			fi
		fi
	else
		nft_reason="nft not installed: install nftables first"
	fi
	if command -v "$ipt_bin" >/dev/null 2>&1 && command -v "$ip6t_bin" >/dev/null 2>&1 && command -v "$iptr_bin" >/dev/null 2>&1 && command -v "$ip6tr_bin" >/dev/null 2>&1; then
		local ipt_err=""
		local ip6t_err=""
		ipt_err="$("$ipt_bin" -S 2>&1 1>/dev/null)" || true
		ip6t_err="$("$ip6t_bin" -S 2>&1 1>/dev/null)" || true
		if "$ipt_bin" -S >/dev/null 2>&1 && "$ip6t_bin" -S >/dev/null 2>&1; then
			ipt_ok="1"
		else
			if ! "$ipt_bin" -S >/dev/null 2>&1; then
				ipt_reason="$(printf '%s' "$ipt_err" | head -n 1)"
			else
				ipt_reason="$(printf '%s' "$ip6t_err" | head -n 1)"
			fi
			[ -n "$ipt_reason" ] || ipt_reason="iptables-legacy probe failed"
			if [ "$APPLY" != "1" ] && [ "$(id -u)" != "0" ]; then
				ipt_reason="cannot probe (not root)"
			fi
		fi
	else
		ipt_reason="iptables-legacy not installed: need iptables-legacy, ip6tables-legacy and both -restore binaries"
		if [ "$APPLY" != "1" ] && [ "$(id -u)" != "0" ]; then
			ipt_reason="cannot probe (not root)"
		fi
	fi
	if [ -r /proc/modules ] && grep -q "^nf_tables" /proc/modules 2>/dev/null; then
		: # hint only, never decides
	fi
	if [ -r /proc/config.gz ] && [ -x "$(command -v zgrep 2>/dev/null || true)" ]; then
		zgrep "^CONFIG_NF_TABLES" /proc/config.gz 2>/dev/null || true
	fi
	if [ -r "/boot/config-$(uname -r 2>/dev/null)" ]; then
		grep "^CONFIG_NF_TABLES" "/boot/config-$(uname -r)" 2>/dev/null || true
	fi
	case "$want" in
		nft)
			if [ "$nft_ok" = "1" ]; then
				FW_BACKEND="nft"
			else
				die "no usable firewall backend: nft: $nft_reason. Refusing to continue without a firewall"
			fi
			;;
		iptables)
			if [ "$ipt_ok" = "1" ]; then
				FW_BACKEND="iptables-legacy"
			else
				die "no usable firewall backend: iptables-legacy: $ipt_reason. Refusing to continue without a firewall"
			fi
			;;
		auto)
			if [ "$nft_ok" = "1" ]; then
				FW_BACKEND="nft"
			elif [ "$ipt_ok" = "1" ]; then
				warn "nftables unusable ($nft_reason); using iptables-legacy"
				FW_BACKEND="iptables-legacy"
			else
				die "no usable firewall backend: nft: $nft_reason; iptables-legacy: $ipt_reason. Refusing to continue without a firewall"
			fi
			;;
	esac
	say "firewall: backend $FW_BACKEND"
}
fw_proven_absent() {
	local cur="$1"
	local err=""
	cur="${cur%/*}"
	[ -n "$cur" ] || cur="/"
	while :; do
		if err="$(LC_ALL=C ls -ld "$cur" 2>&1)"; then
			if ! ls -l "$cur" >/dev/null 2>&1; then
				return 1
			fi
			return 0
		else
			case "$err" in
				*"No such file"*) ;;
				*) return 1 ;;
			esac
		fi
		if [ "$cur" = "/" ]; then
			return 0
		fi
		cur="${cur%/*}"
		[ -n "$cur" ] || cur="/"
	done
}
fw_saved_backend() {
	local state_path=""
	local state_dir=""
	state_path="$(p /etc/opos/firewall.backend)"
	if [ ! -e "$state_path" ]; then
		if [ -L "$state_path" ]; then
			die "cannot read firewall backend state"
		fi
		state_dir="${state_path%/*}"
		[ -n "$state_dir" ] || state_dir="/"
		if [ -L "$state_dir" ]; then
			die "cannot read firewall backend state"
		fi
		if [ -e "$state_dir" ]; then
			if [ ! -d "$state_dir" ]; then
				die "cannot read firewall backend state"
			fi
			if ! ls -ld "$state_dir" >/dev/null 2>&1; then
				die "cannot read firewall backend state"
			fi
			if ! ls -l "$state_dir" >/dev/null 2>&1; then
				die "cannot read firewall backend state"
			fi
			return 0
		fi
		if ! fw_proven_absent "$state_path"; then
			die "cannot read firewall backend state"
		fi
		return 0
	fi
	if [ ! -r "$state_path" ]; then
		die "cannot read firewall backend state"
	fi
	local saved=""
	if ! saved="$(cat "$state_path" 2>/dev/null)"; then
		die "cannot read firewall backend state"
	fi
	case "$saved" in
		nft | iptables-legacy)
			printf '%s' "$saved"
			;;
		*)
			die "invalid firewall backend state"
			;;
	esac
}
fw_activate_backend() {
	local previous="$1"
	local current="$FW_BACKEND"
	case "$current" in
		nft | iptables-legacy) ;;
		*) die "bad firewall backend: $current" ;;
	esac
	case "$previous" in
		"" | nft | iptables-legacy) ;;
		*) die "invalid firewall backend state" ;;
	esac
	if [ -n "$previous" ] && [ "$previous" != "$current" ]; then
		if [ "$current" = "nft" ]; then
			if ! sed -e 's/[[:space:]]comment[[:space:]][[:space:]]*"\(\\.\|[^"\\]\)*"[[:space:]]*;\?[[:space:]]*$//' -e 's/#.*//' -e 's/"[^"]*"//g' "$(p /etc/opos/firewall.nft)" | grep -Evi '(^|[^[:alnum:]_-])ct[[:space:]]+state([^[:alnum:]_-]|$)' | grep -Ei 'tcp.*dport[[:space:]]+22([^0-9]|$)|dport[[:space:]]+22([^0-9]|$).*tcp' | grep -Ei '(^|[[:space:]])accept[[:space:]]*;?[[:space:]]*$' | grep -Evi 'drop|reject' >/dev/null; then
				die "firewall migration refused: new nft ruleset lacks SSH"
			fi
		else
			if ! sed -e 's/#.*//' -e 's/"[^"]*"//g' "$(p /etc/opos/firewall.rules)" | grep -Evi '(^|[^[:alnum:]_-])--(ctstate|state)([[:space:]]|=)' | grep -E -- '^-A[[:space:]]+OPOS_INPUT[[:space:]]+' | grep -Ev -- '(^|[[:space:]])!([[:space:]]|$)' | grep -E -- '--dport[[:space:]]+22([^0-9]|$)' | grep -E -- '-p[[:space:]]+tcp([[:space:]]|$)' | grep -Eq -- '-j[[:space:]]+ACCEPT([[:space:]]|$)'; then
				die "firewall migration refused: new iptables ruleset lacks SSH"
			fi
		fi
		write_file /etc/opos/firewall.backend.prev 0644 <<EOF
$previous
EOF
		case "$previous" in
			nft)
				run nft delete table inet opos_filter || die "firewall migration failed: cannot clear nft rules"
				;;
			iptables-legacy)
				if [ -x "$(p /etc/opos/firewall-legacy.sh)" ]; then
					run /etc/opos/firewall-legacy.sh stop || die "firewall migration failed: cannot clear iptables-legacy rules"
				else
					die "firewall migration failed: cannot clear iptables-legacy rules"
				fi
				;;
		esac
	fi
	write_file /etc/opos/firewall.backend 0644 <<EOF
$current
EOF
}
step_firewall() {
	local iface=""
	local ifaces_nft=""
	local prev_backend=""
	fw_pick_backend
	prev_backend="$(fw_saved_backend)" || die "cannot read firewall backend state"
	if [ "$prev_backend" = "iptables-legacy" ] && [ "$FW_BACKEND" = "nft" ]; then
		die "firewall migration refused: legacy-to-nft requires verified inbound SSH"
	fi
	if [ "$prev_backend" = "nft" ] && [ "$FW_BACKEND" = "iptables-legacy" ]; then
		die "firewall migration refused: nft-to-legacy requires verified inbound SSH"
	fi
	for iface in $OPOS_USB_IFACES; do
		case "$iface" in
			"" | *[!a-z0-9]* | ????????????????*) die "bad interface name" ;;
		esac
		if [ -z "$ifaces_nft" ]; then
			ifaces_nft="\"$iface\""
		else
			ifaces_nft="$ifaces_nft, \"$iface\""
		fi
	done
	[ -n "$ifaces_nft" ] || die "bad interface name"
	if [ "$FW_BACKEND" = "iptables-legacy" ]; then
		step_firewall_legacy
		fw_activate_backend "$prev_backend"
		return 0
	fi
	write_file /etc/opos/firewall.nft 0644 <<EOF
# managed by opos harden-device.sh
table inet opos_filter
delete table inet opos_filter
table inet opos_filter {
	chain input {
		type filter hook input priority 0; policy drop;
		ct state invalid drop;
		ct state established,related accept;
		iifname "lo" accept;
		# SSH is reachable only over the USB network, not Wi-Fi.
		iifname { $ifaces_nft } tcp dport 22 accept;
		iifname { $ifaces_nft } ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept;
		udp sport 67 udp dport 68 accept;
		iifname { $ifaces_nft } udp sport 68 udp dport 67 accept;
		ip protocol icmp icmp type { echo-request, destination-unreachable, time-exceeded } limit rate 5/second accept;
		meta l4proto ipv6-icmp icmpv6 type { destination-unreachable, packet-too-big, time-exceeded, nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept;
	}
	chain forward {
		type filter hook forward priority 0; policy drop;
	}
	chain output {
		type filter hook output priority 0; policy accept;
	}
}
EOF
	write_file /etc/systemd/system/opos-firewall.service 0644 <<EOF
[Unit]
Description=Old Phone OS firewall
Before=network-pre.target
Wants=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/opos/firewall.nft
ExecStop=/usr/sbin/nft delete table inet opos_filter
[Install]
WantedBy=multi-user.target
RequiredBy=network-pre.target
EOF
	if [ "$FW_PROBED" = "1" ] || have_cmd nft; then
		run nft -c -f "$(p /etc/opos/firewall.nft)" || die "ruleset does not parse, nothing loaded"
		run nft -f "$(p /etc/opos/firewall.nft)"
		run systemctl daemon-reload
		run systemctl enable opos-firewall.service
		if [ "$APPLY" = "1" ]; then
			printf 'disabled opos-firewall.service\n' >> "$MANIFEST"
		fi
	else
		warn "nft not installed; install nftables first (needs approval)"
		return 0
	fi
	fw_activate_backend "$prev_backend"
	say "firewall: default deny inbound, SSH only on: $OPOS_USB_IFACES"
}
fw_require_recovery_ssh() {
	local iface=""
	local sysnet="${OPOS_SYS_CLASS_NET:-/sys/class/net}"
	local ss_bin="${OPOS_SS:-ss}"
	local ss_out=""
	local found="0"
	for iface in $OPOS_USB_IFACES; do
		case "$iface" in
			"" | *[!a-z0-9]* | ????????????????*) return 1 ;;
		esac
		if [ -d "$sysnet/$iface" ]; then
			found="1"
			break
		fi
	done
	[ "$found" = "1" ] || return 1
	if ! ss_out="$("$ss_bin" -ltn 2>/dev/null)"; then
		return 1
	fi
	case "$ss_out" in
		*":22"*) return 0 ;;
		*) return 1 ;;
	esac
}
fw_confirm_ssh() {
	local ssh_bin="${OPOS_SSH:-ssh}"
	local ssh_host="${OPOS_SSH_HOST:-127.0.0.1}"
	"$ssh_bin" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$ssh_host" true >/dev/null 2>&1
}
fw_legacy_claim_chain() {
	local fw_bin="$1"
	local fw_tag="$2"
	local fw_chain="$3"
	local fw_dump=""
	local fw_tail=""
	local fw_prev=""
	fw_prev="$(p "/etc/opos/fw-prev-$fw_tag-$fw_chain")" || die "firewall backup failed: $fw_chain"
	: > "$fw_prev" || die "firewall backup failed: $fw_chain"
	if fw_dump="$("$fw_bin" -S "$fw_chain" 2>/dev/null)"; then
		case "$fw_dump" in
			*"-N $fw_chain"*|*"-A $fw_chain "*) ;;
			*) return 0 ;;
		esac
		fw_tail="$(printf '%s\n' "$fw_dump" | tail -n 1)" || die "firewall backup failed: $fw_chain"
		[ "$fw_tail" = "-A $fw_chain -j DROP" ] || die "firewall refused: unowned chain $fw_chain exists"
		printf '%s\n' "$fw_dump" > "$fw_prev" || die "firewall backup failed: $fw_chain"
	fi
}
fw_legacy_restore_chain() {
	local fw_bin="$1"
	local fw_restore="$2"
	local fw_tag="$3"
	local fw_chain="$4"
	local fw_hook="$5"
	local fw_prev=""
	local fw_tmp=""
	fw_prev="$(p "/etc/opos/fw-prev-$fw_tag-$fw_chain")" || die "firewall restore failed: $fw_chain"
	[ -s "$fw_prev" ] || return 0
	fw_tmp="$(mktemp)" || die "firewall restore failed: $fw_chain"
	{
		printf '*filter\n:%s - [0:0]\n' "$fw_chain"
		grep -F -- "-A $fw_chain " "$fw_prev" || true
		printf 'COMMIT\n'
	} > "$fw_tmp" || { rm -f "$fw_tmp"; die "firewall restore failed: $fw_chain"; }
	run "$fw_restore" --noflush "$fw_tmp" || { rm -f "$fw_tmp"; die "firewall restore failed: $fw_chain"; }
	rm -f "$fw_tmp"
	if ! "$fw_bin" -C "$fw_hook" -j "$fw_chain" >/dev/null 2>&1; then
		run "$fw_bin" -I "$fw_hook" 1 -j "$fw_chain" || die "firewall restore failed: $fw_chain"
	fi
}
step_firewall_legacy() {
	local iface=""
	local ssh4=""
	local ssh6=""
	local dhcp6=""
	for iface in $OPOS_USB_IFACES; do
		case "$iface" in
			"" | *[!a-z0-9]* | ????????????????*) die "bad interface name" ;;
		esac
		ssh4="$ssh4-A OPOS_INPUT -i $iface -p tcp --dport 22 -j ACCEPT
"
		ssh6="$ssh6-A OPOS_INPUT -i $iface -p tcp --dport 22 -j ACCEPT
"
		dhcp6="$dhcp6-A OPOS_INPUT -i $iface -p udp -s fe80::/10 --sport 547 --dport 546 -j ACCEPT
"
	done
	[ -n "$ssh4" ] || die "bad interface name"
	if [ "$APPLY" = "1" ] || [ "$TEST" = "1" ]; then
		fw_require_recovery_ssh || die "firewall refused: no recovery SSH route with listener on port 22"
	fi
	write_file /etc/opos/firewall.rules 0644 <<EOF
*filter
:OPOS_INPUT - [0:0]
:OPOS_FORWARD - [0:0]
-A OPOS_INPUT -m conntrack --ctstate INVALID -j DROP
-A OPOS_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A OPOS_INPUT -i lo -j ACCEPT
${ssh4}-A OPOS_INPUT -p udp --sport 67 --dport 68 -j ACCEPT
-A OPOS_INPUT -p icmp --icmp-type echo-request -m limit --limit 5/second -j ACCEPT
-A OPOS_INPUT -p icmp --icmp-type destination-unreachable -m limit --limit 5/second -j ACCEPT
-A OPOS_INPUT -p icmp --icmp-type time-exceeded -m limit --limit 5/second -j ACCEPT
-A OPOS_INPUT -j DROP
-A OPOS_FORWARD -j DROP
COMMIT
EOF
	write_file /etc/opos/firewall6.rules 0644 <<EOF
*filter
:OPOS_INPUT - [0:0]
:OPOS_FORWARD - [0:0]
-A OPOS_INPUT -m conntrack --ctstate INVALID -j DROP
-A OPOS_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A OPOS_INPUT -i lo -j ACCEPT
${ssh6}${dhcp6}
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type destination-unreachable -j ACCEPT
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type packet-too-big -j ACCEPT
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type time-exceeded -j ACCEPT
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type neighbour-solicitation -j ACCEPT
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type neighbour-advertisement -j ACCEPT
-A OPOS_INPUT -p ipv6-icmp --icmpv6-type router-advertisement -j ACCEPT
-A OPOS_INPUT -j DROP
-A OPOS_FORWARD -j DROP
COMMIT
EOF
	write_file /etc/opos/firewall-legacy.sh 0755 <<EOF
#!/bin/sh
set -eu
cmd="\${1:-}"
opos_failsafe() {
	ipt="\${OPOS_IPTABLES_LEGACY:-iptables-legacy}"
	ip6t="\${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}"
	iface=""
	"\$ipt" -N OPOS_INPUT 2>/dev/null || true
	"\$ipt" -N OPOS_FORWARD 2>/dev/null || true
	"\$ip6t" -N OPOS_INPUT 2>/dev/null || true
	"\$ip6t" -N OPOS_FORWARD 2>/dev/null || true
	ssh4=""
	ssh6=""
	dhcp6=""
	for iface in $OPOS_USB_IFACES; do
		ssh4="\$ssh4-A OPOS_INPUT -i \$iface -p tcp --dport 22 -j ACCEPT
"
		ssh6="\$ssh6-A OPOS_INPUT -i \$iface -p tcp --dport 22 -j ACCEPT
"
		dhcp6="\$dhcp6-A OPOS_INPUT -i \$iface -p udp -s fe80::/10 --sport 547 --dport 546 -j ACCEPT
"
	done
	v4_fallback="0"
	tmp=""
	if tmp="\$(mktemp)"; then
		if [ -z "\$tmp" ]; then
			v4_fallback="1"
		elif {
			printf '%s\n' '*filter' ':OPOS_INPUT - [0:0]' ':OPOS_FORWARD - [0:0]' '-A OPOS_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT' '-A OPOS_INPUT -i lo -j ACCEPT'
			printf '%s' "\$ssh4"
			printf '%s\n' '-A OPOS_INPUT -p udp --sport 67 --dport 68 -j ACCEPT' '-A OPOS_INPUT -p icmp --icmp-type echo-request -m limit --limit 5/second -j ACCEPT' '-A OPOS_INPUT -p icmp --icmp-type destination-unreachable -m limit --limit 5/second -j ACCEPT' '-A OPOS_INPUT -p icmp --icmp-type time-exceeded -m limit --limit 5/second -j ACCEPT' '-A OPOS_INPUT -j DROP' '-A OPOS_FORWARD -j DROP' 'COMMIT'
		} > "\$tmp"; then
			if "\${OPOS_IPTABLES_LEGACY_RESTORE:-iptables-legacy-restore}" --noflush "\$tmp"; then
				rm -f "\$tmp" || true
			else
				v4_fallback="1"
				rm -f "\$tmp" || true
			fi
		else
			v4_fallback="1"
			rm -f "\$tmp" || true
		fi
	else
		v4_fallback="1"
	fi
	tmp="\$(mktemp)" || true
	if [ -n "\$tmp" ]; then
		{
			printf '%s\n' '*filter' ':OPOS_INPUT - [0:0]' ':OPOS_FORWARD - [0:0]' '-A OPOS_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT' '-A OPOS_INPUT -i lo -j ACCEPT'
			printf '%s' "\$ssh6"
			printf '%s' "\$dhcp6"
			printf '%s\n' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type destination-unreachable -j ACCEPT' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type packet-too-big -j ACCEPT' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type time-exceeded -j ACCEPT' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type neighbour-solicitation -j ACCEPT' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type neighbour-advertisement -j ACCEPT' '-A OPOS_INPUT -p ipv6-icmp --icmpv6-type router-advertisement -j ACCEPT' '-A OPOS_INPUT -j DROP' '-A OPOS_FORWARD -j DROP' 'COMMIT'
		} > "\$tmp" || true
		"\${OPOS_IP6TABLES_LEGACY_RESTORE:-ip6tables-legacy-restore}" --noflush "\$tmp" || true
		rm -f "\$tmp" || true
	fi
	if ! "\$ipt" -C INPUT -j OPOS_INPUT 2>/dev/null; then
		"\$ipt" -I INPUT 1 -j OPOS_INPUT 2>/dev/null || true
	fi
	if ! "\$ipt" -C FORWARD -j OPOS_FORWARD 2>/dev/null; then
		"\$ipt" -I FORWARD 1 -j OPOS_FORWARD 2>/dev/null || true
	fi
	if ! "\$ip6t" -C INPUT -j OPOS_INPUT 2>/dev/null; then
		"\$ip6t" -I INPUT 1 -j OPOS_INPUT 2>/dev/null || true
	fi
	if ! "\$ip6t" -C FORWARD -j OPOS_FORWARD 2>/dev/null; then
		"\$ip6t" -I FORWARD 1 -j OPOS_FORWARD 2>/dev/null || true
	fi
	if [ "\$v4_fallback" = "1" ]; then
		fb_ok="1"
		for fb_iface in \$OPOS_USB_IFACES; do
			if ! "\$ipt" -I INPUT 1 -i "\$fb_iface" -p tcp --dport 22 -j ACCEPT; then
				fb_ok="0"
				continue
			fi
			if ! "\$ipt" -C INPUT -i "\$fb_iface" -p tcp --dport 22 -j ACCEPT; then
				fb_ok="0"
			fi
		done
		if [ "\$fb_ok" != "1" ]; then
			echo "IPv4 SSH recovery failed" >&2
			return 1
		fi
	fi
	return 0
}
case "\$cmd" in
	start)
		"${OPOS_IPTABLES_LEGACY_RESTORE:-iptables-legacy-restore}" --noflush /etc/opos/firewall.rules || { opos_failsafe; exit 1; }
		"${OPOS_IP6TABLES_LEGACY_RESTORE:-ip6tables-legacy-restore}" --noflush /etc/opos/firewall6.rules || { opos_failsafe; exit 1; }
		while "\${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; do
			"\${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -D INPUT -j OPOS_INPUT >/dev/null 2>&1 || { opos_failsafe; exit 1; }
		done
		"\${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -I INPUT 1 -j OPOS_INPUT || { opos_failsafe; exit 1; }
		if ! fw_rules="\$( "\${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S INPUT 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv4 INPUT rules: \$fw_rules" >&2
			opos_failsafe; exit 1
		fi
		fw_first="\$(printf '%s\n' "\$fw_rules" | awk '\$1=="-A" && \$2=="INPUT" {print; exit}')" || { opos_failsafe; exit 1; }
		if [ "\$fw_first" != "-A INPUT -j OPOS_INPUT" ]; then
			echo "firewall-legacy.sh: IPv4 INPUT jump not first" >&2
			opos_failsafe; exit 1
		fi
		if ! "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C FORWARD -j OPOS_FORWARD 2>/dev/null; then
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -I FORWARD 1 -j OPOS_FORWARD
		fi
		while "\${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; do
			"\${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -D INPUT -j OPOS_INPUT >/dev/null 2>&1 || { opos_failsafe; exit 1; }
		done
		"\${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -I INPUT 1 -j OPOS_INPUT || { opos_failsafe; exit 1; }
		if ! fw_rules="\$( "\${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S INPUT 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv6 INPUT rules: \$fw_rules" >&2
			opos_failsafe; exit 1
		fi
		fw_first="\$(printf '%s\n' "\$fw_rules" | awk '\$1=="-A" && \$2=="INPUT" {print; exit}')" || { opos_failsafe; exit 1; }
		if [ "\$fw_first" != "-A INPUT -j OPOS_INPUT" ]; then
			echo "firewall-legacy.sh: IPv6 INPUT jump not first" >&2
			opos_failsafe; exit 1
		fi
		if ! "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C FORWARD -j OPOS_FORWARD 2>/dev/null; then
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -I FORWARD 1 -j OPOS_FORWARD || { opos_failsafe; exit 1; }
		fi
		;;
	stop)
		if "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; then
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -D INPUT -j OPOS_INPUT
		elif ! "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv4 INPUT rules" >&2
			exit 1
		fi
		if "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C FORWARD -j OPOS_FORWARD >/dev/null 2>&1; then
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -D FORWARD -j OPOS_FORWARD
		elif ! "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv4 FORWARD rules" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv4 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_INPUT([[:space:]]|\$)"; then
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -F OPOS_INPUT
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -X OPOS_INPUT
		fi
		if ! fw_rules="\$( "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv4 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_FORWARD([[:space:]]|\$)"; then
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -F OPOS_FORWARD
			"${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -X OPOS_FORWARD
		fi
		if "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; then
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -D INPUT -j OPOS_INPUT
		elif ! "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv6 INPUT rules" >&2
			exit 1
		fi
		if "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C FORWARD -j OPOS_FORWARD >/dev/null 2>&1; then
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -D FORWARD -j OPOS_FORWARD
		elif ! "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv6 FORWARD rules" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv6 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_INPUT([[:space:]]|\$)"; then
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -F OPOS_INPUT
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -X OPOS_INPUT
		fi
		if ! fw_rules="\$( "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv6 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_FORWARD([[:space:]]|\$)"; then
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -F OPOS_FORWARD
			"${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -X OPOS_FORWARD
		fi
		if "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: INPUT jump still present" >&2
			exit 1
		elif ! "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv4 INPUT rules" >&2
			exit 1
		fi
		if "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -C FORWARD -j OPOS_FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: FORWARD jump still present" >&2
			exit 1
		elif ! "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv4 FORWARD rules" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv4 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_INPUT([[:space:]]|\$)"; then
			echo "firewall-legacy.sh: OPOS_INPUT chain still present" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IPTABLES_LEGACY:-iptables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv4 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_FORWARD([[:space:]]|\$)"; then
			echo "firewall-legacy.sh: OPOS_FORWARD chain still present" >&2
			exit 1
		fi
		if "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C INPUT -j OPOS_INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: INPUT jump still present" >&2
			exit 1
		elif ! "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S INPUT >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv6 INPUT rules" >&2
			exit 1
		fi
		if "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -C FORWARD -j OPOS_FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: FORWARD jump still present" >&2
			exit 1
		elif ! "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S FORWARD >/dev/null 2>&1; then
			echo "firewall-legacy.sh: cannot inspect IPv6 FORWARD rules" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv6 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_INPUT([[:space:]]|\$)"; then
			echo "firewall-legacy.sh: OPOS_INPUT chain still present" >&2
			exit 1
		fi
		if ! fw_rules="\$( "${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}" -S 2>&1)"; then
			echo "firewall-legacy.sh: cannot inspect IPv6 rules: \$fw_rules" >&2
			exit 1
		fi
		if printf '%s\n' "\$fw_rules" | grep -q -E "^-[NA] OPOS_FORWARD([[:space:]]|\$)"; then
			echo "firewall-legacy.sh: OPOS_FORWARD chain still present" >&2
			exit 1
		fi
		;;
	*) echo "usage: firewall-legacy.sh start|stop" >&2; exit 1 ;;
esac
EOF
	write_file /etc/systemd/system/opos-firewall.service 0644 <<EOF
[Unit]
Description=Old Phone OS firewall
Before=network-pre.target
Wants=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/opos/firewall-legacy.sh start
ExecStop=/etc/opos/firewall-legacy.sh stop
[Install]
WantedBy=multi-user.target
RequiredBy=network-pre.target
EOF
	local iptr="${OPOS_IPTABLES_LEGACY_RESTORE:-iptables-legacy-restore}"
	local ip6tr="${OPOS_IP6TABLES_LEGACY_RESTORE:-ip6tables-legacy-restore}"
	local ipt="${OPOS_IPTABLES_LEGACY:-iptables-legacy}"
	local ip6t="${OPOS_IP6TABLES_LEGACY:-ip6tables-legacy}"
	run "$iptr" --test --noflush "$(p /etc/opos/firewall.rules)" || die "ruleset does not parse, nothing loaded"
	run "$ip6tr" --test --noflush "$(p /etc/opos/firewall6.rules)" || die "ruleset does not parse, nothing loaded"
	if [ "$APPLY" = "1" ] || [ "$TEST" = "1" ]; then
		fw_legacy_claim_chain "$ipt" v4 OPOS_INPUT
		fw_legacy_claim_chain "$ipt" v4 OPOS_FORWARD
		fw_legacy_claim_chain "$ip6t" v6 OPOS_INPUT
		fw_legacy_claim_chain "$ip6t" v6 OPOS_FORWARD
	fi
	run /etc/opos/firewall-legacy.sh start
	if [ "$APPLY" = "1" ] || [ "$TEST" = "1" ]; then
		if ! fw_confirm_ssh; then
			run /etc/opos/firewall-legacy.sh stop >/dev/null 2>&1 || true
			fw_legacy_restore_chain "$ipt" "$iptr" v4 OPOS_INPUT INPUT
			fw_legacy_restore_chain "$ipt" "$iptr" v4 OPOS_FORWARD FORWARD
			fw_legacy_restore_chain "$ip6t" "$ip6tr" v6 OPOS_INPUT INPUT
			fw_legacy_restore_chain "$ip6t" "$ip6tr" v6 OPOS_FORWARD FORWARD
			die "firewall rolled back: SSH reconnection failed"
		fi
	fi
	run systemctl daemon-reload
	run systemctl enable opos-firewall.service
	if [ "$APPLY" = "1" ]; then
		printf 'disabled opos-firewall.service\n' >> "$MANIFEST"
	fi
	if [ "${APPLY:-0}" = "1" ] && [ "${TEST:-1}" = "0" ]; then
		local tail=""
		local rules=""
		local first=""
		tail="$("$ipt" -S OPOS_INPUT 2>/dev/null | tail -n 1)" || die "firewall verification failed: cannot read OPOS_INPUT"
		[ "$tail" = "-A OPOS_INPUT -j DROP" ] || die "firewall verification failed: OPOS_INPUT does not end with DROP"
		tail="$("$ip6t" -S OPOS_INPUT 2>/dev/null | tail -n 1)" || die "firewall verification failed: cannot read OPOS_INPUT"
		[ "$tail" = "-A OPOS_INPUT -j DROP" ] || die "firewall verification failed: OPOS_INPUT does not end with DROP"
		rules="$("$ipt" -S INPUT 2>/dev/null)" || die "firewall verification failed: cannot read INPUT"
		first="$(printf '%s\n' "$rules" | awk '$1=="-A" && $2=="INPUT" {print; exit}' || true)"
		[ "$first" = "-A INPUT -j OPOS_INPUT" ] || die "firewall verification failed: INPUT jump missing"
		rules="$("$ip6t" -S INPUT 2>/dev/null)" || die "firewall verification failed: cannot read INPUT"
		first="$(printf '%s\n' "$rules" | awk '$1=="-A" && $2=="INPUT" {print; exit}' || true)"
		[ "$first" = "-A INPUT -j OPOS_INPUT" ] || die "firewall verification failed: INPUT jump missing"
	fi
	say "firewall: default deny inbound, SSH only on: $OPOS_USB_IFACES"
}
