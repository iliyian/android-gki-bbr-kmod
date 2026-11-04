#!/usr/bin/env python3
"""Read __crc_* values from a vmlinux ELF and optionally query symbols."""
import struct
import sys


def load(path):
    d = open(path, "rb").read()
    e_shoff, = struct.unpack_from("<Q", d, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh = struct.unpack_from("<IIQQQQIIQQ", d, off)
        secs.append(dict(name_off=sh[0], type=sh[1], addr=sh[3], off=sh[4],
                         size=sh[5], link=sh[6]))
    shstr = secs[e_shstrndx]

    def sname(s):
        if not s["name_off"]:
            return ""
        st = shstr["off"] + s["name_off"]
        return d[st:d.index(b"\0", st)].decode()

    for s in secs:
        s["name"] = sname(s)
    symtab = next(s for s in secs if s["type"] == 2)
    strtab = secs[symtab["link"]]
    out = {}
    for i in range(symtab["size"] // 24):
        o = symtab["off"] + i * 24
        st_name, st_info, st_other, st_shndx, st_value, st_size = \
            struct.unpack_from("<IBBHQQ", d, o)
        if st_name == 0 or st_shndx >= len(secs):
            continue
        so = strtab["off"] + st_name
        name = d[so:d.index(b"\0", so)].decode()
        if not name.startswith("__crc_"):
            continue
        sec = secs[st_shndx]
        if st_value < sec["addr"]:
            continue
        fo = sec["off"] + (st_value - sec["addr"])
        if fo + 4 > len(d):
            continue
        val, = struct.unpack_from("<I", d, fo)
        out[name[6:]] = val
    return out


def main():
    a = load(sys.argv[1])
    b = load(sys.argv[2])
    names = sys.argv[3:]
    if names:
        for k in names:
            print("%-40s %08x %08x %s" % (
                k, a.get(k, 0), b.get(k, 0),
                "SAME" if a.get(k) == b.get(k) else "DIFF"))
        return
    ch = [k for k in a if k in b and a[k] != b[k]]
    print("crc syms old=%d new=%d changed=%d" % (len(a), len(b), len(ch)))
    for k in sorted(ch):
        print("  %-45s %08x -> %08x" % (k, a[k], b[k]))


if __name__ == "__main__":
    main()
