#!/bin/bash
# A/B: A = cubic + pfifo_fast (vendor-like), C = bbr + fq (current deployment)
# 6 download blocks (32MB) + 4 upload blocks (8MB), ABBA order, watchdog paused.
# Required: SPEEDTEST_URL (base URL) and SERVER_IP (for route check/ping).
# Optional: ADB, DEV, IFACE, LOG.
ADB="${ADB:-adb}"
DEV="${DEV:-/data/local/tmp/cc_bench.sh}"
PFX="${1:-}"
LOG="${LOG:-suite_qdisc${PFX:+_$PFX}.log}"
IFACE="${IFACE:-rmnet_data3}"
URL="${SPEEDTEST_URL:?set SPEEDTEST_URL}"
SERVER_IP="${SERVER_IP:?set SERVER_IP}"

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

WDPID=$("$ADB" shell "su -c \"ps -A -o PID,ARGS | grep '[s]ervice.sh' | head -1\"" | awk '{print $1}' | tr -d '\r')
ORIG_CC=$("$ADB" shell "su -c 'sysctl -n net.ipv4.tcp_congestion_control'" | tr -d '\r')

restore() {
    say "restoring CC=$ORIG_CC, default_qdisc=fq, root fq, watchdog CONT (pid=$WDPID)"
    "$ADB" shell "su -c 'sysctl -w net.core.default_qdisc=fq'" >/dev/null 2>&1
    "$ADB" shell "su -c 'tc qdisc replace dev $IFACE root fq'" >/dev/null 2>&1
    "$ADB" shell "su -c 'sysctl -w net.ipv4.tcp_congestion_control=$ORIG_CC'" >/dev/null 2>&1
    [ -n "$WDPID" ] && "$ADB" shell "su -c 'kill -CONT $WDPID'" >/dev/null 2>&1
    "$ADB" shell "su -c 'sysctl -n net.ipv4.tcp_congestion_control; sysctl -n net.core.default_qdisc; tc qdisc show dev $IFACE | head -1'" 2>/dev/null | tr '\n' ' ' | tee -a "$LOG"
    echo | tee -a "$LOG"
}

trap restore EXIT INT TERM

: > "$LOG"
say "qdisc suite start; watchdog pid=$WDPID orig_cc=$ORIG_CC"
if [ -z "$WDPID" ]; then say "WARN: watchdog pid not found"; else
    "$ADB" shell "su -c 'kill -STOP $WDPID'" >/dev/null 2>&1
    say "watchdog state: $("$ADB" shell "su -c \"awk '{print \\$3}' /proc/$WDPID/stat 2>/dev/null\"" | tr -d '\r')"
fi

arm_cc()  { [ "$1" = "A" ] && echo cubic || echo bbr; }
arm_qd()  { [ "$1" = "A" ] && echo mq_pfifo || echo fq; }

run_one() {
    local arm=$1 tag=$2 mode=$3 bytes=$4 attempt cc qd ok qok
    cc=$(arm_cc "$arm"); qd=$(arm_qd "$arm")
    for attempt in 1 2; do
        say "RUN $tag arm=$arm cc=$cc qdisc=$qd mode=$mode bytes=$bytes (attempt $attempt)"
        "$ADB" shell "su -c '$DEV $cc $tag $mode $bytes $qd $URL $SERVER_IP'" >/dev/null 2>&1
        ok=$("$ADB" shell "su -c 'cat /data/local/tmp/ccbench/$tag.result 2>/dev/null; cat /data/local/tmp/ccbench/$tag.qdisc0 2>/dev/null; cat /data/local/tmp/ccbench/$tag.qdisc1 2>/dev/null; cat /data/local/tmp/ccbench/$tag.cc 2>/dev/null; cat /data/local/tmp/ccbench/$tag.route0 2>/dev/null; cat /data/local/tmp/ccbench/$tag.route1 2>/dev/null'" | tr -d '\r')
        qok=0
        if [ "$qd" = "mq_pfifo" ]; then
            [ "$(echo "$ok" | grep -c 'qdisc mq .*root')" -ge 2 ] && \
            [ "$(echo "$ok" | grep -c 'qdisc pfifo_fast')" -ge 40 ] && \
            [ "$(echo "$ok" | grep -c 'default_qdisc=pfifo_fast')" -ge 2 ] && qok=1
        else
            [ "$(echo "$ok" | grep -c 'qdisc fq .*root')" -ge 2 ] && \
            [ "$(echo "$ok" | grep -c 'default_qdisc=fq')" -ge 2 ] && qok=1
        fi
        if echo "$ok" | grep -q 'DONE' && [ "$qok" = 1 ] && echo "$ok" | grep -qx "$cc" && ! echo "$ok" | grep -q 'dev tun'; then
            say "  ok: $(echo "$ok" | head -1 | cut -c1-140)"
            return 0
        fi
        say "  FAILED/invalid (qdisc or cc mismatch), retrying"
        sleep 2
    done
    return 1
}

# warm-up to validate qdisc switching (discarded)
run_one A "${PFX}warm_A" dl 8388608 || { say "warmup failed, abort"; exit 1; }

for p in 1 2 3 4 5 6; do
    if [ $((p % 2)) -eq 1 ]; then order="A C"; else order="C A"; fi
    for arm in $order; do
        run_one "$arm" "${PFX}bulk${p}_${arm}" dl 33554432 || exit 1
        sleep 3
    done
done

for p in 1 2 3 4; do
    if [ $((p % 2)) -eq 1 ]; then order="A C"; else order="C A"; fi
    for arm in $order; do
        run_one "$arm" "${PFX}ul${p}_${arm}" ul 0 || exit 1
        sleep 3
    done
done

say "qdisc suite done"
