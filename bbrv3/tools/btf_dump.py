#!/usr/bin/env python3
"""Minimal BTF struct dumper (raw .BTF section files)."""
import struct
import sys

KIND_NAMES = {
    1: "INT", 2: "PTR", 3: "ARRAY", 4: "STRUCT", 5: "UNION", 6: "ENUM",
    7: "FWD", 8: "TYPEDEF", 9: "VOLATILE", 10: "CONST", 11: "RESTRICT",
    12: "FUNC", 13: "FUNC_PROTO", 14: "VAR", 15: "DATASEC", 16: "FLOAT",
    17: "DECL_TAG", 18: "TYPE_TAG", 19: "ENUM64",
}


class BTF:
    def __init__(self, path):
        data = open(path, "rb").read()
        magic, ver, flags, hdr_len, type_off, type_len, str_off, str_len = \
            struct.unpack_from("<HBBIIIII", data, 0)
        if magic != 0xEB9F:
            raise SystemExit("not raw BTF: %#x" % magic)
        self.types_blob = data[hdr_len + type_off: hdr_len + type_off + type_len]
        self.strs = data[hdr_len + str_off: hdr_len + str_off + str_len]
        self.types = [None]
        self._parse()

    def s(self, off):
        if off == 0:
            return ""
        end = self.strs.find(b"\0", off)
        return self.strs[off:end].decode("utf-8", "replace")

    def _parse(self):
        off = 0
        blob = self.types_blob
        while off < len(blob):
            name_off, info, size_type = struct.unpack_from("<III", blob, off)
            off += 12
            vlen = info & 0xFFFF
            kind = (info >> 24) & 0x1F
            kflag = (info >> 31) & 1
            t = {
                "name": self.s(name_off), "kind": kind, "vlen": vlen,
                "size_type": size_type, "kflag": kflag, "members": [],
                "id": len(self.types),
            }
            if kind in (4, 5):
                for _ in range(vlen):
                    m_name, m_type, m_off = struct.unpack_from("<III", blob, off)
                    off += 12
                    if kflag:
                        bit_off = m_off & 0xFFFFFF
                        bit_sz = m_off >> 24
                    else:
                        bit_off = m_off
                        bit_sz = 0
                    t["members"].append(
                        (self.s(m_name), m_type, bit_off, bit_sz))
            elif kind == 6:
                off += 8 * vlen
            elif kind == 19:
                off += 12 * vlen
            elif kind == 3:
                off += 12
            elif kind == 13:
                off += 8 * vlen
            elif kind == 14:
                off += 4
            elif kind == 15:
                off += 12 * vlen
            elif kind == 17:
                off += 8
            self.types.append(t)

    def find(self, name, kind=None):
        out = []
        for t in self.types[1:]:
            if t["name"] == name and (kind is None or t["kind"] == kind):
                out.append(t)
        return out


def type_name(btf, tid, depth=0):
    if tid == 0 or tid >= len(btf.types) or depth > 8:
        return "?"
    t = btf.types[tid]
    k = t["kind"]
    n = t["name"]
    if k in (4, 5):
        return ("struct " if k == 4 else "union ") + n
    if k == 8:
        return n
    if k in (9, 10, 11):
        return type_name(btf, t["size_type"], depth + 1)
    if k == 2:
        return type_name(btf, t["size_type"], depth + 1) + " *"
    if k == 3:
        return "array"
    return n or KIND_NAMES.get(k, "kind%d" % k)


def main():
    path = sys.argv[1]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else []
    btf = BTF(path)
    for name in names:
        for t in btf.find(name):
            if t["kind"] not in (4, 5):
                continue
            print("=" * 70)
            print("%s %s  size=%d  (%s)" % (
                "struct" if t["kind"] == 4 else "union", name,
                t["size_type"], path))
            for mname, mtid, bit_off, bit_sz in t["members"]:
                extra = ""
                if bit_sz:
                    extra = " : %d" % bit_sz
                print("  +%-5d %-34s %s%s" % (
                    bit_off // 8, mname, type_name(btf, mtid), extra))


if __name__ == "__main__":
    main()
