#!/usr/bin/env python3
"""Compare struct member offsets between two btf_dump.py outputs.

For layout compatibility we require: every member that exists in the old
struct with a non-empty name must exist in the new struct at the same bit
offset with the same bit width.  New members are reported separately.
"""
import re
import sys


def parse(path):
    out = {}
    cur = None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(struct|union) (\S+)\s+size=(\d+)", line)
        if m:
            cur = (m.group(1), m.group(2))
            out[cur] = {"size": int(m.group(3)), "members": []}
            continue
        if cur is None:
            continue
        m = re.match(r"^  \+(\d+)\s+(.*)$", line.rstrip())
        if not m:
            continue
        off = int(m.group(1))
        rest = m.group(2)
        bits = 0
        bm = re.search(r": (\d+)$", rest)
        if bm:
            bits = int(bm.group(1))
            rest = rest[:bm.start()].rstrip()
        name = rest.split()[0] if rest.split() else ""
        out[cur]["members"].append((off, name, bits))
    return out


def main():
    a = parse(sys.argv[1])
    b = parse(sys.argv[2])
    bad = 0
    for key in sorted(a):
        if key not in b:
            print("MISSING struct in new:", key)
            bad += 1
            continue
        sa, sb = a[key], b[key]
        old = {m[1]: (m[0], m[2]) for m in sa["members"] if m[1]}
        new = {m[1]: (m[0], m[2]) for m in sb["members"] if m[1]}
        shifts = []
        for name, pos in old.items():
            if name not in new:
                shifts.append("REMOVED %s" % name)
            elif new[name] != pos:
                shifts.append("MOVED %s %s -> %s" % (name, pos, new[name]))
        added = [n for n in new if n not in old]
        print("== %s %s: size %d -> %d, members %d -> %d" % (
            key[0], key[1], sa["size"], sb["size"], len(old), len(new)))
        for s in shifts:
            print("   !! " + s)
        if added:
            print("   added: " + ", ".join("%s@%d" % (n, new[n][0]) for n in added))
        bad += len(shifts)
    print("RESULT:", "EXISTING LAYOUT IDENTICAL" if bad == 0 else
          "%d LAYOUT CHANGES" % bad)


if __name__ == "__main__":
    main()
