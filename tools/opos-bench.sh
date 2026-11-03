# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh

# Keep decimal output and numeric parsing stable in the guest's locale.
LC_ALL=C
export LC_ALL

usage() { printf '%s\n' 'usage: opos-bench.sh [--help]'; }
if [ "$#" -eq 1 ] && [ "$1" = --help ]; then
    usage
    exit 0
fi
if [ "$#" -ne 0 ]; then
    usage >&2
    exit 2
fi
sample=${OPOS_BENCH_SAMPLE-5}
case "$sample" in
    ''|*[!0-9]*) usage >&2; exit 2 ;;
esac
if ! awk -v n="$sample" 'BEGIN { exit !(n >= 0 && n <= 60) }'; then
    usage >&2
    exit 2
fi
sample=$(awk -v n="$sample" 'BEGIN { printf "%d", n }')
root=${OPOS_BENCH_ROOT-}
hz=${OPOS_BENCH_HZ-$(getconf CLK_TCK 2>/dev/null)}
hz=$(awk -v n="$hz" 'BEGIN { if (n ~ /^[0-9]+$/ && n > 0) print n; else print 100 }')
boot=NA
source=none
for name in cage agetty getty login; do
    matched=no
    ticks=$(
        for dir in "$root"/proc/[0-9]*; do
            [ -d "$dir" ] || continue
            pid=${dir##*/}
            case "$pid" in (*[!0-9]*) continue ;; esac
            comm=$(cat "$dir/comm" 2>/dev/null)
            [ "$comm" = "$name" ] || continue
            # A marker preserves name priority even if a process exits before stat.
            printf '%s\n' match
            sed 's/.*) //' "$dir/stat" 2>/dev/null |
                awk '$20 ~ /^[0-9]+$/ { print $20 }'
        done
    )
    case "$ticks" in
        *match*) matched=yes ;;
    esac
    if [ "$matched" = yes ]; then
        source=$name
        boot=$(printf '%s\n' "$ticks" | awk -v hz="$hz" '
            /^[0-9]+$/ { if (!seen || $1 < smallest) smallest=$1; seen=1 }
            END { if (seen) printf "%.1f", smallest/hz; else print "NA" }')
        break
    fi
done
ram=$(awk '
    $1 == "MemTotal:" { total=$2; t=1 }
    $1 == "MemAvailable:" { available=$2; a=1 }
    END { if (t && a) printf "%.0f", int((total-available)/1024); else print "NA" }
' "$root/proc/meminfo" 2>/dev/null) || ram=NA
cpu_sample() {
    awk '/^cpu / {
        total=0
        for (i=2; i<=NF; i++) total+=$i
        print total, $5+$6
        exit
    }' "$1" 2>/dev/null
}
first=$(cpu_sample "$root/proc/stat")
sleep "$sample"
second=$(cpu_sample "${OPOS_BENCH_STAT2-$root/proc/stat}")
cpu=$(awk -v first="$first" -v second="$second" 'BEGIN {
    if (split(first,a) != 2 || split(second,b) != 2 || b[1]-a[1] <= 0) print "NA"
    else printf "%.1f", 100*(1-(b[2]-a[2])/(b[1]-a[1]))
}')
# Capture before counting so a failed query reports NA rather than 0.
count_lines() { printf '%s' "$1" | awk 'END { print NR }'; }
packages=NA
manager=none
if command -v apk >/dev/null 2>&1; then
    if out=$(apk info 2>/dev/null); then packages=$(count_lines "$out"); fi
    manager=apk
elif command -v dpkg-query >/dev/null 2>&1; then
    if out=$(dpkg-query -W -f '.\n' 2>/dev/null); then packages=$(count_lines "$out"); fi
    manager=dpkg
fi
services=NA
init=none
if [ -d "$root/etc/runlevels" ]; then
    services=$(
        for level in "$root"/etc/runlevels/*/; do
            [ -d "$level" ] || continue
            ls -A1 "$level" 2>/dev/null
        done | sort -u | wc -l | awk '{print $1}'
    )
    init=openrc
elif [ -z "$root" ] && command -v systemctl >/dev/null 2>&1; then
    if out=$(systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null); then
        services=$(count_lines "$out")
    fi
    init=systemd
fi
printf '%s\n' 'bench_version=1' "boot_to_login_s=$boot" \
    "boot_to_login_src=$source" "idle_ram_mb=$ram" "idle_cpu_pct=$cpu" \
    "pkg_count=$packages" "pkg_manager=$manager" "enabled_services=$services" "init=$init"
