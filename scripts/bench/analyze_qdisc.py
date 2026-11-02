import os, sys, re, glob, statistics as st, math, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A

PFX = sys.argv[1] if len(sys.argv) > 1 else "q"

def tags(prefix):
    ts = sorted({os.path.basename(p).split(".")[0] for p in glob.glob(os.path.join(A.BASE, prefix + "*.result"))})
    return [t for t in ts if re.match(rf"^{re.escape(prefix)}\d+_(A|C)$", t)]

def qdisc_ok(tag, arm):
    for ext in ("qdisc0", "qdisc1"):
        q = A.rd(tag, ext, "") or ""
        if arm == "A":
            if "qdisc mq" not in q or "default_qdisc=pfifo_fast" not in q or q.count("qdisc pfifo_fast") < 31:
                return False
        else:
            if "qdisc fq" not in q or "default_qdisc=fq" not in q:
                return False
    return True

def valid(tag, arm):
    r = A.run_info(tag)
    r["arm"] = arm
    r["qdisc_ok"] = qdisc_ok(tag, arm)
    r["routes"] = ((A.rd(tag, "route0", "") or "") + (A.rd(tag, "route1", "") or ""))
    return r

def load(prefix):
    out = []
    for t in tags(prefix):
        arm = t.rsplit("_", 1)[-1]
        r = valid(t, arm)
        if r.get("direct") is False or not r.get("qdisc_ok"):
            print(f"EXCLUDED {t}: direct={r.get('direct')} qdisc_ok={r.get('qdisc_ok')}")
            continue
        want_cc = "cubic" if arm == "A" else "bbr"
        if r.get("cc_after") != want_cc or any(c != want_cc for c in (r.get("cc_seen") or [want_cc])):
            print(f"EXCLUDED {t}: cc_after={r.get('cc_after')} cc_seen={r.get('cc_seen')}")
            continue
        out.append(r)
    return out

def summarize(runs, phase):
    print(f"\n=== {phase} ===")
    s = {}
    for arm, name in (("A", "cubic+mq/pfifo"), ("C", "bbr+fq")):
        rr = [r for r in runs if r["arm"] == arm]
        if not rr:
            continue
        sp = [r["mbps"] for r in rr if r.get("mbps") is not None]
        d = {
            "n": len(rr),
            "mbps_med": st.median(sp), "mbps_mean": st.mean(sp),
            "mbps_sd": st.stdev(sp) if len(sp) > 1 else None,
            "mbps_min": min(sp), "mbps_max": max(sp),
            "ttfb_med": st.median([r["TTFB"] for r in rr if r.get("TTFB") is not None]),
            "ping_p50": st.median([r["ping_load"].get("p50") for r in rr if r["ping_load"].get("p50") is not None]),
            "ping_p95": st.median([r["ping_load"].get("p95") for r in rr if r["ping_load"].get("p95") is not None]),
            "retr": st.median([r["tcp"].get("retrans_pct") for r in rr if r["tcp"].get("retrans_pct") is not None]) if any(r["tcp"].get("retrans_pct") is not None for r in rr) else None,
            "cwnd": st.median([r["cwnd_segs_med"] for r in rr if r.get("cwnd_segs_med") is not None]) if any(r.get("cwnd_segs_med") is not None for r in rr) else None,
            "rtt": st.median([r["rtt_ms_med"] for r in rr if r.get("rtt_ms_med") is not None]) if any(r.get("rtt_ms_med") is not None for r in rr) else None,
            "deliv": st.median([r["delivery_mbps_med"] for r in rr if r.get("delivery_mbps_med") is not None]) if any(r.get("delivery_mbps_med") is not None for r in rr) else None,
            "rxoh": st.median([r["rx_overhead_pct"] for r in rr if r.get("rx_overhead_pct") is not None]) if any(r.get("rx_overhead_pct") is not None for r in rr) else None,
            "txoh": st.median([r["tx_overhead_pct"] for r in rr if r.get("tx_overhead_pct") is not None]) if any(r.get("tx_overhead_pct") is not None for r in rr) else None,
            "rsrp": sorted({r["signal"].get("ssRsrp") for r in rr if r.get("signal", {}).get("ssRsrp") is not None}),
            "sinr": sorted({r["signal"].get("ssSinr") for r in rr if r.get("signal", {}).get("ssSinr") is not None}),
        }
        s[arm] = d
        print(f"{arm} {name:16s} n={d['n']} median={d['mbps_med']:.2f} mean={d['mbps_mean']:.2f} "
              f"sd={d['mbps_sd']:.2f} range=[{d['mbps_min']:.2f}-{d['mbps_max']:.2f}] Mbps "
              f"ping50={d['ping_p50']:.0f} p95={d['ping_p95']:.0f} retr%={d['retr'] if d['retr'] is None else round(d['retr'],2)}")
        if phase.startswith("upload"):
            print(f"    cwnd={d['cwnd']:.0f} segs rtt={d['rtt']:.0f} ms delivery={d['deliv']:.2f} Mbps "
                  f"txOH={d['txoh']:.1f}%")
        else:
            print(f"    rxOH={d['rxoh']:.1f}%")
        print(f"    RSRP={d['rsrp']} SINR={d['sinr']}")

    pairs = {}
    for r in runs:
        m = re.search(r"(?:bulk|ul)(\d+)_(A|C)$", r["tag"])
        if m:
            pairs.setdefault(int(m.group(1)), {})[m.group(2)] = r
    rows = []
    for p in sorted(pairs):
        if "A" in pairs[p] and "C" in pairs[p]:
            a, c = pairs[p]["A"]["mbps"], pairs[p]["C"]["mbps"]
            rows.append({"pair": p, "A": a, "C": c, "delta": 100 * (c - a) / a})
    if rows:
        print(f"paired (C vs A) median={st.median([x['delta'] for x in rows]):+.1f}% "
              f"mean={st.mean([x['delta'] for x in rows]):+.1f}% "
              f"geomean={math.exp(st.mean([math.log(x['C']/x['A']) for x in rows])):.3f} "
              f"C wins {sum(1 for x in rows if x['delta']>0)}/{len(rows)}")
        for x in rows:
            print(f"  pair{x['pair']}: A {x['A']:.2f} vs C {x['C']:.2f} Mbps -> {x['delta']:+.1f}%")
    return {"summary": s, "pairs": rows}

dn = load(f"{PFX}bulk")
up = load(f"{PFX}ul")
res = {"download": summarize(dn, "download 32MB"), "upload": summarize(up, "upload 8MB")}

print("\n--- per-run ---")
for r in dn + up:
    print(f"{r['tag']:10s} arm={r['arm']} {r['mbps']:6.2f} Mbps ttfb={r.get('TTFB'):.2f} "
          f"cwnd={A.fmt(r.get('cwnd_segs_med'),0)} deliv={A.fmt(r.get('delivery_mbps_med'))} "
          f"rtt={A.fmt(r.get('rtt_ms_med'),0)} ping50={A.fmt(r['ping_load'].get('p50'),0)} "
          f"p95={A.fmt(r['ping_load'].get('p95'),0)} retr%={A.fmt(r['tcp'].get('retrans_pct'),2)} "
          f"rxOH%={A.fmt(r.get('rx_overhead_pct'),1)} txOH%={A.fmt(r.get('tx_overhead_pct'),1)} "
          f"rsrp={r['signal'].get('ssRsrp')} direct={r.get('direct')} qdisc_ok={r['qdisc_ok']} cc={r.get('cc_seen')}")

outjson = os.environ.get("BENCH_OUT", f"results_qdisc_{PFX}.json")
with open(outjson, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2, default=str)
print(f"\nwrote {outjson}")
