#!/usr/bin/env python3
"""Dump __versions entries (expected symbol CRCs) from a kernel module."""
import struct
import sys


def parse(path):
    d = open(path, "rb").read()
    e_shoff, = struct.unpack_from("<Q", d, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh = struct.unpack_from("<IIQQQQIIQQ", d, off)
        secs.append(dict(name_off=sh[0], type=sh[1], addr=sh[3], off=sh[4],
                         size=sh[5], link=sh[6], entsize=sh[9]))
    shstr = secs[e_shstrndx]

    def sname(s):
        st = shstr["off"] + s["name_off"]
        return d[st:d.index(b"\0", st)].decode() if s["name_off"] else ""

    for s in secs:
        s["name"] = sname(s)
    ver = next((s for s in secs if s["name"] == "__versions"), None)
    if not ver:
        raise SystemExit("no __versions")
    blob = d[ver["off"]:ver["off"] + ver["size"]]
    # struct modversion_info { unsigned long crc; char name[MODULE_NAME_LEN]; }
    # MODULE_NAME_LEN is 56 on 5.x/6.x -> 64 byte entries.
    entsize = 64
    if len(blob) % entsize:
        raise SystemExit("unexpected __versions size %d" % len(blob))
    out = {}
    for i in range(len(blob) // entsize):
        crc, = struct.unpack_from("<Q", blob, i * entsize)
        name = blob[i * entsize + 8:i * entsize + 64].split(b"\0")[0].decode()
        out[name] = crc
    return out


def main():
    v = parse(sys.argv[1])
    names = sys.argv[2:]
    if names:
        for n in names:
            if n in v:
                print("%-45s %08x" % (n, v[n] & 0xFFFFFFFF))
            else:
                print("%-45s (not imported)" % n)
    else:
        print("entries=%d" % len(v))
        for n in sorted(v)[:40]:
            print("  %-45s %08x" % (n, v[n] & 0xFFFFFFFF))


if __name__ == "__main__":
    main()
