#!/system/bin/sh
# Shared helpers for the bbr3_fq module. Sourced by post-fs-data.sh,
# service.sh, action.sh and the bbr3-fq CLI.

MODDIR=/data/adb/modules/bbr3_fq
CONFIG=/data/adb/bbr3_fq.conf
LOG=/data/local/tmp/bbr3_fq.log
TC=/system/bin/tc

DEFAULT_CC=bbr3
DEFAULT_QDISC=fq

# Qdiscs that are safe to use as a root qdisc without extra parameters and
# make sense as net.core.default_qdisc. Classful schedulers are intentionally
# excluded: they need classes/filters to pass traffic.
QDISC_CANDIDATES="fq fq_codel codel sfq pfifo_fast pfifo"

log() {
    echo "$(/system/bin/date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG" 2>/dev/null
}

rotate_log() {
    size=$(/system/bin/wc -c <"$LOG" 2>/dev/null)
    [ -n "$size" ] && [ "$size" -gt 65536 ] && /system/bin/mv -f "$LOG" "$LOG.old" 2>/dev/null
}

# Print one argument as a JSON string literal.
json_str() {
    printf '"%s"' "$(printf '%s' "$1" | /system/bin/sed 's/\\/\\\\/g; s/"/\\"/g' | /system/bin/tr -d '\r\n\t')"
}

# Load CC/QDISC from $CONFIG, keeping only safe tokens.
load_config() {
    CFG_CC=$DEFAULT_CC
    CFG_QDISC=$DEFAULT_QDISC
    if [ -f "$CONFIG" ]; then
        while IFS='=' read -r key value; do
            case "$key" in
                CC) CFG_CC=$value ;;
                QDISC) CFG_QDISC=$value ;;
            esac
        done <"$CONFIG"
    fi
    case "$CFG_CC" in
        ''|*[!a-z0-9_]*) CFG_CC=$DEFAULT_CC ;;
    esac
    case "$CFG_QDISC" in
        ''|*[!a-z0-9_]*) CFG_QDISC=$DEFAULT_QDISC ;;
    esac
}

# Write the current CFG_CC/CFG_QDISC atomically with root-only permissions.
save_config() {
    umask 077
    printf 'CC=%s\nQDISC=%s\n' "$CFG_CC" "$CFG_QDISC" >"$CONFIG.tmp" &&
        /system/bin/mv -f "$CONFIG.tmp" "$CONFIG" &&
        /system/bin/chmod 600 "$CONFIG"
}

cc_available() {
    avail=$(/system/bin/cat /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null)
    case " $avail " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# Probe without touching a real network interface: dummy0 exists on this
# device, and a failed probe costs nothing.
qdisc_supported() {
    q=$1
    case " $QDISC_CANDIDATES " in
        *" $q "*) ;;
        *) return 1 ;;
    esac
    if [ -e /sys/class/net/dummy0 ]; then
        if $TC qdisc replace dev dummy0 root "$q" >/dev/null 2>&1; then
            $TC qdisc del dev dummy0 root >/dev/null 2>&1
            return 0
        fi
        return 1
    fi
    # Fallback: net.core.default_qdisc rejects unregistered qdiscs. Restore
    # the previous value immediately so this stays side-effect free in practice.
    prev=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null)
    if echo "$q" >/proc/sys/net/core/default_qdisc 2>/dev/null; then
        echo "$prev" >/proc/sys/net/core/default_qdisc 2>/dev/null
        return 0
    fi
    return 1
}

available_qdiscs() {
    for q in $QDISC_CANDIDATES; do
        qdisc_supported "$q" && echo "$q"
    done
}

# Enforce the configured CC and default_qdisc. Called from post-fs-data,
# the service loop and the CLI.
ensure_sysctls() {
    load_config

    target_cc=$CFG_CC
    if ! cc_available "$target_cc"; then
        log "configured cc '$target_cc' unavailable, falling back"
        for target_cc in "$DEFAULT_CC" cubic reno; do
            cc_available "$target_cc" && break
        done
    fi
    if cc_available "$target_cc"; then
        cur=$(/system/bin/cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null)
        if [ "$cur" != "$target_cc" ]; then
            echo "$target_cc" >/proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null
            log "cc -> $target_cc"
        fi
    fi

    target_qdisc=$CFG_QDISC
    cur=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null)
    if [ "$cur" != "$target_qdisc" ]; then
        if echo "$target_qdisc" >/proc/sys/net/core/default_qdisc 2>/dev/null; then
            log "default_qdisc -> $target_qdisc"
        else
            log "configured qdisc '$target_qdisc' rejected, falling back"
            for target_qdisc in "$DEFAULT_QDISC" pfifo_fast; do
                echo "$target_qdisc" >/proc/sys/net/core/default_qdisc 2>/dev/null && break
            done
            cur=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null)
            [ "$cur" = "$target_qdisc" ] && log "default_qdisc -> $target_qdisc"
        fi
    fi
}

# Attach the configured qdisc to managed interfaces. netd recreates devices
# on network switches, so this runs on every service tick.
apply_qdisc() {
    load_config
    q=$CFG_QDISC
    case " $QDISC_CANDIDATES " in
        *" $q "*) ;;
        *) q=$DEFAULT_QDISC ;;
    esac
    for sysdev in /sys/class/net/wlan* /sys/class/net/rmnet_data* /sys/class/net/utun* /sys/class/net/tun[0-9]*; do
        [ -e "$sysdev" ] || continue
        dev=${sysdev##*/}
        show=$($TC qdisc show dev "$dev" 2>/dev/null | /system/bin/head -n 1)
        case "$show" in
            "qdisc $q "*) continue ;;
        esac
        if $TC qdisc replace dev "$dev" root "$q" 2>/dev/null; then
            log "$q -> $dev"
        else
            log "$q FAILED -> $dev"
        fi
    done
}

service_pid() {
    cat "$MODDIR/service.pid" 2>/dev/null
}

service_alive() {
    pid=$(service_pid)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}
