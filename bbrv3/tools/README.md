# Verification / porting tools

These are the scripts used while porting and validating BBRv3 on zorn.
They are not required to build the kernel; they document how the layout and
module-CRC checks were done.

| Tool | Purpose |
|---|---|
| `btf_dump.py <raw.BTF> [struct,...]` | Dump struct members and bit offsets from a raw BTF file (e.g. `llvm-objcopy --dump-section .BTF=out.btf vmlinux`). |
| `compare_btf.py old.txt new.txt` | Compare two `btf_dump.py` outputs; reports moved/added members. Used to prove `tcp_sock` / `inet_connection_sock` layouts did not change. |
| `crc_query.py <old-vmlinux> <new-vmlinux> [symbols...]` | Read `__crc_*` values from vmlinux ELF files and diff them. |
| `ko_versions.py <module.ko> [symbols...]` | Parse a vendor module's `__versions` section (expected symbol CRCs). |
| `stage_bbrv3.py` | Local transform that produced the adapted `tcp_bbr.c` from Google's `google/bbr` v3 file. Expects the reference `tcp_bbr.c` next to it and an `ack138/` copy of the target tree; included for auditability. |
| `patch_version.py` | Applies the `check_version()` CRC-tolerance change (same as `patches/0003-...`); hardcodes the server path used during development, so prefer the patch for actual use. |

Typical workflow:

```bash
# layout check
llvm-objcopy --dump-section .BTF=stock.btf stock-vmlinux
llvm-objcopy --dump-section .BTF=new.btf new-vmlinux
python3 btf_dump.py stock.btf tcp_sock,tcp_skb_cb,inet_connection_sock > stock.txt
python3 btf_dump.py new.btf   tcp_sock,tcp_skb_cb,inet_connection_sock > new.txt
python3 compare_btf.py stock.txt new.txt

# module CRC check
python3 ko_versions.py vendor/audio_pkt_dlkm.ko skb_put __alloc_skb
python3 crc_query.py old-vmlinux new-vmlinux skb_put __alloc_skb
```

The phone-side conclusions are in `../docs/BBRV3_PHONE_JOURNAL.md`.
