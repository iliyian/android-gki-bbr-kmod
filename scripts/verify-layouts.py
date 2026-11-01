#!/usr/bin/env python3
"""Compare struct layouts between device BTF (raw dump) and module BTF (raw dump)."""
import re
import sys

LINE_RE = re.compile(r"^\[(\d+)\] (STRUCT|UNION) '([^']*)' size=(\d+) vlen=(\d+)")
MEM_RE = re.compile(
    r"^\s+'([^']*)' type_id=(\d+) bits_offset=(\d+)(?: bitfield_size=(\d+))?"
)


def parse(path):
    structs = {}
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line)
            if m:
                cur = m.group(3)
                structs[cur] = {"size": int(m.group(4)), "members": {}}
                continue
            if cur is None:
                continue
            mm = MEM_RE.match(line)
            if mm:
                structs[cur]["members"][mm.group(1)] = (
                    int(mm.group(3)),
                    int(mm.group(4)) if mm.group(4) else 0,
                )
            elif line.startswith("["):
                cur = None
    return structs


def main():
    dev = parse(sys.argv[1])
    mod = parse(sys.argv[2])
    names = sys.argv[3:]
    rc = 0
    for name in names:
        d = dev.get(name)
        m = mod.get(name)
        if not d or not m:
            print(f"[SKIP] {name}: dev={bool(d)} mod={bool(m)}")
            rc = 1
            continue
        print(f"=== {name}: dev size={d['size']} mod size={m['size']} "
              f"{'OK' if d['size'] == m['size'] else 'SIZE MISMATCH'}")
        if d["size"] != m["size"]:
            rc = 1
        all_members = list(d["members"].keys())
        for k in m["members"]:
            if k not in d["members"]:
                all_members.append(k)
        for member in all_members:
            dv = d["members"].get(member)
            mv = m["members"].get(member)
            if dv is None:
                print(f"  [only-in-module] {member}: mod={mv}")
                rc = 1
            elif mv is None:
                print(f"  [only-in-device] {member}: dev={dv}")
                rc = 1
            elif dv != mv:
                print(f"  [MISMATCH] {member}: dev={dv} mod={mv}")
                rc = 1
        print(f"  members={len(d['members'])} compared, differences above (if any)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
