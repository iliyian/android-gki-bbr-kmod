"""Export per-sample ss/ping data for the 2026-11-02 runs into two CSVs.

Set BENCH_DATA to the raw artifact directory, RUNS_CSV to the per-run CSV,
and OUT_DIR to the output directory. No addresses are written.
"""
import csv, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A

BASE = os.environ.get("BENCH_DATA", "./data")
RUNS = os.environ.get("RUNS_CSV", "docs/bench/data/2026-11-02-runs.csv")
OUTDIR = os.environ.get("OUT_DIR", "docs/bench/data")
A.BASE = BASE


def clean_rate(v):
    return "" if v is None else round(v / 1e6, 2)


def clean_num(v, nd=1):
    return "" if v is None else round(v, nd)


def main():
    runs = list(csv.DictReader(open(RUNS, encoding="utf-8")))
    os.makedirs(OUTDIR, exist_ok=True)

    ss_fields = ["server", "scenario", "phase", "direction", "tag", "arm", "cc", "qdisc",
                 "used", "note", "elapsed_s", "interval_mbps", "total_bytes",
                 "cc_seen", "cwnd_segs", "rtt_ms", "minrtt_ms", "delivery_mbps",
                 "pacing_mbps", "retrans_total", "lost"]
    ping_fields = ["server", "scenario", "phase", "tag", "arm", "cc", "qdisc",
                   "used", "note", "icmp_seq", "approx_elapsed_s", "rtt_ms"]

    ss_path = os.path.join(OUTDIR, "2026-11-02-samples-ss.csv")
    ping_path = os.path.join(OUTDIR, "2026-11-02-samples-ping.csv")

    ss_n = ping_n = 0
    with open(ss_path, "w", newline="", encoding="utf-8") as fs, \
         open(ping_path, "w", newline="", encoding="utf-8") as fp:
        ws = csv.DictWriter(fs, fieldnames=ss_fields)
        wp = csv.DictWriter(fp, fieldnames=ping_fields)
        ws.writeheader()
        wp.writeheader()

        for r in runs:
            tag = r["tag"]
            direction = "dl" if r["phase"].startswith("download") else "ul"
            meta = {k: r.get(k, "") for k in
                    ("server", "scenario", "phase", "tag", "arm", "cc", "qdisc", "used", "note")}

            samples = A.parse_ss_file(tag)
            prev_ts = prev_total = None
            first_ts = None
            for s in samples:
                try:
                    ts = float(str(s["ts"]).split()[0])
                except Exception:
                    continue
                socks = s.get("socks") or []
                if not socks:
                    continue
                # one test connection per run; if several, take the one with the most bytes
                def total_of(sk):
                    return max(sk.get("bytes_received") or 0, sk.get("bytes_acked") or 0)
                sk = max(socks, key=total_of)
                total = (sk.get("bytes_acked") or 0) if direction == "ul" else (sk.get("bytes_received") or 0)
                if first_ts is None:
                    first_ts = ts
                if prev_ts is not None and 0.12 < (ts - prev_ts) < 3.0:
                    interval = max(0, total - prev_total) * 8 / (ts - prev_ts) / 1e6
                else:
                    interval = None
                prev_ts, prev_total = ts, total

                row = dict(meta)
                row.update({
                    "direction": direction,
                    "elapsed_s": round(ts - first_ts, 2),
                    "interval_mbps": clean_num(interval, 2),
                    "total_bytes": int(total),
                    "cc_seen": sk.get("cc") or "",
                })
                if direction == "ul":
                    row.update({
                        "cwnd_segs": clean_num(sk.get("cwnd"), 0),
                        "rtt_ms": clean_num(sk.get("rtt"), 1),
                        "minrtt_ms": clean_num(sk.get("minrtt"), 1),
                        "delivery_mbps": clean_rate(sk.get("delivery_rate")),
                        "pacing_mbps": clean_rate(sk.get("pacing_rate")),
                        "retrans_total": sk.get("retrans_total", ""),
                        "lost": sk.get("lost", ""),
                    })
                else:
                    # receiver-side sender fields are frozen; leave them out rather than mislead
                    row.update({k: "" for k in ("cwnd_segs", "rtt_ms", "minrtt_ms",
                                                "delivery_mbps", "pacing_mbps",
                                                "retrans_total", "lost")})
                ws.writerow(row)
                ss_n += 1

            p = os.path.join(BASE, tag + ".ping")
            if not os.path.exists(p):
                continue
            for line in open(p, encoding="utf-8", errors="replace"):
                m = re.search(r"icmp_seq=(\d+).*time[=<]([0-9.]+)\s*ms", line)
                if not m:
                    continue
                seq = int(m.group(1))
                row = dict(meta)
                row.update({
                    "icmp_seq": seq,
                    "approx_elapsed_s": round((seq - 1) * 0.2, 1),
                    "rtt_ms": float(m.group(2)),
                })
                wp.writerow(row)
                ping_n += 1

    print(f"wrote {ss_path}: {ss_n} rows")
    print(f"wrote {ping_path}: {ping_n} rows")


if __name__ == "__main__":
    main()
