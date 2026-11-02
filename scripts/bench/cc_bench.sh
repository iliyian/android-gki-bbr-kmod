#!/system/bin/sh
# cc_bench.sh <cc> <tag> <mode=dl|ul|multi> [bytes] [qdisc] <url> <server_ip>
# Runs one measurement run and writes raw artifacts under /data/local/tmp/ccbench/<tag>.*
# Requires root. <url> is the speedtest base URL, e.g. https://host:3333/<token>.
# TLS uses -k because test servers often use IP/self-signed certs; remove it if you have a valid cert.
CC="$1"
TAG="$2"
MODE="${3:-dl}"
BYTES="${4:-33554432}"
QDISC="$5"
URL="$6"
SERVER_IP="$7"
[ -n "$URL" ] || { echo "missing speedtest url" >&2; exit 2; }
[ -n "$SERVER_IP" ] || { echo "missing server ip" >&2; exit 2; }
BASE=/data/local/tmp/ccbench
OUT="$BASE/$TAG"

mkdir -p "$BASE"
rm -f "$OUT.stop"

sysctl -w net.ipv4.tcp_congestion_control="$CC" >/dev/null 2>&1

IFACE=$(ip route get "$SERVER_IP" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
[ -z "$IFACE" ] && IFACE=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
[ -z "$IFACE" ] && IFACE=rmnet_data3

# optional qdisc override (5th arg); tests run with the module watchdog paused
#   fq       -> net.core.default_qdisc=fq, root fq (current deployment)
#   mq_pfifo -> default_qdisc=pfifo_fast, root mq (vendor-like: mq + pfifo_fast leaves)
case "$QDISC" in
    mq_pfifo)
        sysctl -w net.core.default_qdisc=pfifo_fast >/dev/null 2>&1
        tc qdisc replace dev "$IFACE" root mq >/dev/null 2>&1
        ;;
    fq)
        sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1
        tc qdisc replace dev "$IFACE" root fq >/dev/null 2>&1
        ;;
    *)
        [ -n "$QDISC" ] && tc qdisc replace dev "$IFACE" root "$QDISC" >/dev/null 2>&1
        ;;
esac
{ tc qdisc show dev "$IFACE" 2>/dev/null; echo "default_qdisc=$(sysctl -n net.core.default_qdisc 2>/dev/null)"; } > "$OUT.qdisc0"

# interface byte counters before
cat "/sys/class/net/$IFACE/statistics/rx_bytes" > "$OUT.rx0" 2>/dev/null
cat "/sys/class/net/$IFACE/statistics/tx_bytes" > "$OUT.tx0" 2>/dev/null
cp /proc/net/snmp "$OUT.snmp0"
cp /proc/net/netstat "$OUT.netstat0"

# signal snapshot (best effort)
dumpsys telephony.registry 2>/dev/null | grep -m1 'mSignalStrength=' > "$OUT.signal" 2>/dev/null

# route snapshot: detect VPN/tun interception
ip route get "$SERVER_IP" 2>/dev/null | head -1 > "$OUT.route0"
date +%s.%N > "$OUT.t0"

# per-socket sampler in background
(
    : > "$OUT.ss"
    while [ ! -f "$OUT.stop" ]; do
        echo "=== $(date +%s.%N)" >> "$OUT.ss"
        ss -tin 2>/dev/null >> "$OUT.ss"
        sleep 0.25
    done
) &
SAMPID=$!

# ping under load
ping -i 0.2 -W 2 "$SERVER_IP" > "$OUT.ping" 2>&1 &
PINGPID=$!

W='HTTP=%{http_code}|BYTES=%{size_download}|SPEED=%{speed_download}|TOTAL=%{time_total}|CONNECT=%{time_connect}|TLS=%{time_appconnect}|TTFB=%{time_starttransfer}|UP=%{size_upload}|UPSPEED=%{speed_upload}|VER=%{http_version}'

case "$MODE" in
    ul)
        UFILE=$BASE/up.bin
        [ -s "$UFILE" ] || dd if=/dev/zero of="$UFILE" bs=1048576 count=16 2>/dev/null
        curl -k -sS -X POST --data-binary @"$UFILE" -H 'Content-Type: application/octet-stream' \
            --max-time 600 -o "$OUT.body" -w "MODE=ul|$W" \
            "$URL/upload" > "$OUT.result" 2> "$OUT.err"
        ;;
    multi)
        : > "$OUT.result"
        PIDS=""
        for i in 1 2 3 4; do
            curl -k -sS -o /dev/null --max-time 600 -w "S$i|$W" \
                "$URL/download?bytes=$BYTES" > "$OUT.result$i" 2> "$OUT.err$i" &
            PIDS="$PIDS $!"
        done
        for p in $PIDS; do wait "$p"; done
        ;;
    *)
        curl -k -sS -o /dev/null --max-time 600 -w "MODE=dl|$W" \
            "$URL/download?bytes=$BYTES" > "$OUT.result" 2> "$OUT.err"
        ;;
esac

date +%s.%N > "$OUT.t1"
ip route get "$SERVER_IP" 2>/dev/null | head -1 > "$OUT.route1"

kill "$PINGPID" 2>/dev/null
wait "$PINGPID" 2>/dev/null

touch "$OUT.stop"
wait "$SAMPID" 2>/dev/null

cat "/sys/class/net/$IFACE/statistics/rx_bytes" > "$OUT.rx1" 2>/dev/null
cat "/sys/class/net/$IFACE/statistics/tx_bytes" > "$OUT.tx1" 2>/dev/null
cp /proc/net/snmp "$OUT.snmp1"
cp /proc/net/netstat "$OUT.netstat1"
{ tc qdisc show dev "$IFACE" 2>/dev/null; echo "default_qdisc=$(sysctl -n net.core.default_qdisc 2>/dev/null)"; } > "$OUT.qdisc1"
sysctl -n net.ipv4.tcp_congestion_control > "$OUT.cc"
echo "$IFACE" > "$OUT.iface"
echo "DONE $TAG" >> "$OUT.result"
