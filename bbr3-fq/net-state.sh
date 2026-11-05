#!/system/bin/sh
# Network snapshots for the bbr3_fq WebUI.
#   net-state.sh          fast snapshot (route, active iface counters, qdisc, TCP)
#   net-state.sh slow     cached-ish slow snapshot (Wi-Fi + cellular signal)
#   net-state.sh latency  three TCP connect attempts to a host:port

MODDIR=/data/adb/modules/bbr3_fq
. "$MODDIR/common.sh"

IP=/system/bin/ip
AWK=/system/bin/awk
SED=/system/bin/sed
GREP=/system/bin/grep
HEAD=/system/bin/head
TC=/system/bin/tc
DATE=/system/bin/date

num_or_null() {
    case "$1" in
        ''|*[!0-9-]*) echo null ;;
        *) echo "$1" ;;
    esac
}

route_json() {
    line=$($IP route get 223.5.5.5 2>/dev/null)
    R_DEV=""
    R_SRC=""
    R_VIA=""
    set -- $line
    while [ $# -gt 0 ]; do
        case "$1" in
            dev) R_DEV=$2; shift 2 ;;
            src) R_SRC=$2; shift 2 ;;
            via) R_VIA=$2; shift 2 ;;
            *) shift ;;
        esac
    done
    # Interface names, IPs and qdisc names are [A-Za-z0-9._:/-]; write them
    # directly instead of spawning escaping subshells every second.
    printf '{"dev":"%s","src":"%s","via":"%s"}' "$R_DEV" "$R_SRC" "$R_VIA"
}

iface_json() {
    dev=$1
    if [ -n "$dev" ] && [ -e "/sys/class/net/$dev" ]; then
        state=""
        carrier=""
        read state <"/sys/class/net/$dev/operstate" 2>/dev/null
        read carrier <"/sys/class/net/$dev/carrier" 2>/dev/null
        [ "$carrier" = "1" ] && state="up"
        read rx tx <<EOF
$($AWK -v d="$dev:" '$1 == d { print $2, $10 }' /proc/net/dev 2>/dev/null)
EOF
        [ -n "$rx" ] || rx=0
        [ -n "$tx" ] || tx=0
        qstat=$($TC -s qdisc show dev "$dev" 2>/dev/null)
        read qdisc drops requeues backlog_b backlog_p <<EOF
$(printf '%s\n' "$qstat" | $AWK '
    NR == 1 { q = $2 }
    /dropped/ && !have_d {
        if (match($0, /dropped [0-9]+/)) {
            d = substr($0, RSTART + 8, RLENGTH - 8)
            have_d = 1
        }
    }
    /^ *backlog/ {
        b = $2
        p = $3
        r = $4
        sub(/b$/, "", b)
        sub(/p$/, "", p)
    }
    END { print q, d + 0, r + 0, b + 0, p + 0 }
')
EOF
    else
        state=""
        rx=0
        tx=0
        qdisc=""
        drops=0
        requeues=0
        backlog_b=0
        backlog_p=0
    fi

    printf '{"name":"%s","state":"%s","rx":%s,"tx":%s,"qdisc":"%s","drops":%s,"backlog_b":%s,"backlog_p":%s,"requeues":%s}' \
        "$dev" "$state" "$rx" "$tx" "$qdisc" \
        "${drops:-0}" "${backlog_b:-0}" "${backlog_p:-0}" "${requeues:-0}"
}

tcp_json() {
    # Counting /proc/net/tcp{,6} is far cheaper than `ss -s`, which matters
    # because the WebUI samples this once per second.
    read total estab timewait <<EOF
$($AWK '
    FNR > 1 {
        total++
        if ($4 == "01") estab++
        if ($4 == "06") timewait++
    }
    END { print total + 0, estab + 0, timewait + 0 }
' /proc/net/tcp /proc/net/tcp6 2>/dev/null)
EOF
    read retrans outsegs <<EOF
$($AWK '
    /^Tcp:/ {
        if (!header) {
            for (i = 1; i <= NF; i++) idx[$i] = i
            header = 1
        } else {
            print $idx["RetransSegs"], $idx["OutSegs"]
        }
    }
' /proc/net/snmp 2>/dev/null)
EOF
    printf '{"total":%s,"estab":%s,"timewait":%s,"retrans":%s,"outsegs":%s}' \
        "${total:-0}" "${estab:-0}" "${timewait:-0}" "${retrans:-0}" "${outsegs:-0}"
}

# Telephony dumps can exceed the per-argument exec limit (128 KiB) on Android,
# so never pass the whole dump through printf; keep it in a temp file.
cell_json() {
    tel=/data/local/tmp/bbr3_fq_tel.$$
    /system/bin/dumpsys telephony.registry >"$tel" 2>/dev/null

    state_line=$($GREP 'mServiceState=' "$tel" | $GREP -m 1 'mDataRegState=0')
    [ -n "$state_line" ] || state_line=$($GREP -m 1 'mServiceState=' "$tel")
    tech=$(printf '%s' "$state_line" | $SED -n 's/.*getRilDataRadioTechnology=[0-9]*(\([^)]*\)).*/\1/p')
    operator=$(printf '%s' "$state_line" | $SED -n 's/.*mOperatorAlphaLong=\([^,]*\).*/\1/p')
    data_state=$(printf '%s' "$state_line" | $SED -n 's/.*mDataRegState=[0-9]*(\([^)]*\)).*/\1/p')

    sig_line=$($GREP 'mSignalStrength=' "$tel" | $GREP -m 1 -E 'ssRsrp = -?[0-9]| rsrp = -?[0-9]')
    [ -n "$sig_line" ] || sig_line=$($GREP -m 1 'mSignalStrength=' "$tel")
    rsrp=$(printf '%s' "$sig_line" | $SED -n 's/.*ssRsrp = \(-*[0-9][0-9]*\).*/\1/p')
    [ -n "$rsrp" ] || rsrp=$(printf '%s' "$sig_line" | $SED -n 's/.*[ ,]rsrp = \(-*[0-9][0-9]*\).*/\1/p')
    rsrq=$(printf '%s' "$sig_line" | $SED -n 's/.*ssRsrq = \(-*[0-9][0-9]*\).*/\1/p')
    sinr=$(printf '%s' "$sig_line" | $SED -n 's/.*ssSinr = \(-*[0-9][0-9]*\).*/\1/p')
    level=$(printf '%s' "$sig_line" | $GREP -o ' level = [0-9]*' | $HEAD -n 1 | $GREP -o '[0-9]*')

    rm -f "$tel"
    printf '{"state":%s,"tech":%s,"operator":%s,"rsrp":%s,"rsrq":%s,"sinr":%s,"level":%s}' \
        "$(json_str "$data_state")" "$(json_str "$tech")" "$(json_str "$operator")" \
        "$(num_or_null "$rsrp")" "$(num_or_null "$rsrq")" "$(num_or_null "$sinr")" "$(num_or_null "$level")"
}

fast_state() {
    printf '{"ts":%s,"route":' "$($DATE +%s)"
    route_json
    printf ',"iface":'
    iface_json "$R_DEV"
    printf ',"tcp":'
    tcp_json
    printf '}\n'
}

wifi_json() {
    out=$(/system/bin/cmd wifi status 2>/dev/null)
    case "$out" in
        *"Wifi is enabled"*) enabled=true ;;
        *) enabled=false ;;
    esac
    ssid=$(printf '%s\n' "$out" | $SED -n 's/.*SSID: *\([^,]*\).*/\1/p' | $HEAD -n 1)
    rssi=$(printf '%s\n' "$out" | $SED -n 's/.*RSSI: *\(-*[0-9]*\).*/\1/p' | $HEAD -n 1)
    link=$(printf '%s\n' "$out" | $SED -n 's/.*Link speed: *\([0-9]*\)Mbps.*/\1/p' | $HEAD -n 1)
    printf '{"enabled":%s,"ssid":%s,"rssi":%s,"link":%s}' \
        "$enabled" "$(json_str "$ssid")" "$(num_or_null "$rssi")" "$(num_or_null "$link")"
}

slow_state() {
    printf '{"wifi":'
    wifi_json
    printf ',"cell":'
    cell_json
    printf '}\n'
}

latency() {
    host=${1:-www.baidu.com}
    port=${2:-443}
    case "$host" in
        ''|*[!A-Za-z0-9.-]*)
            echo '{"ok":false,"host":"","ms":null,"avg":null,"loss":3}'
            return 1
            ;;
    esac
    case "$port" in
        ''|*[!0-9]*)
            echo '{"ok":false,"host":"'"$host"'","ms":null,"avg":null,"loss":3}'
            return 1
            ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo '{"ok":false,"host":"'"$host"'","ms":null,"avg":null,"loss":3}'
        return 1
    fi

    best=999999
    sum=0
    ok=0
    i=0
    while [ "$i" -lt 3 ]; do
        i=$((i + 1))
        start=$($DATE +%s%N)
        if /system/bin/nc -w 2 -z "$host" "$port" >/dev/null 2>&1; then
            end=$($DATE +%s%N)
            ms=$(( (end - start) / 1000000 ))
            [ "$ms" -lt 0 ] && ms=0
            sum=$((sum + ms))
            ok=$((ok + 1))
            [ "$ms" -lt "$best" ] && best=$ms
        fi
    done

    if [ "$ok" -gt 0 ]; then
        avg=$((sum / ok))
        printf '{"ok":true,"host":%s,"port":%s,"ms":%s,"avg":%s,"loss":%s}\n' \
            "$(json_str "$host")" "$port" "$best" "$avg" "$((3 - ok))"
        return 0
    fi
    printf '{"ok":false,"host":%s,"port":%s,"ms":null,"avg":null,"loss":3}\n' \
        "$(json_str "$host")" "$port"
    return 1
}

case "$1" in
    ""|fast)
        fast_state
        ;;
    slow)
        slow_state
        ;;
    latency)
        shift
        latency "$@"
        ;;
    *)
        echo "usage: net-state.sh [fast|slow|latency [host] [port]]" >&2
        exit 1
        ;;
esac
