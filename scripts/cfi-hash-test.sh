#!/bin/bash
# Compare kCFI type hashes emitted by clang 17 (r487747c) and clang 18
# for the same prototypes used by struct tcp_congestion_ops.
cat > /tmp/kcfi_test.c <<'EOF'
struct sock;
enum tcp_ca_event { CA_EVENT_TX_START, CA_EVENT_CWND_RESTART, CA_EVENT_COMPLETE_CWR,
		    CA_EVENT_LOSS, CA_EVENT_ECN_NO_CE, CA_EVENT_ECN_IS_CE };
unsigned int t_ssthresh(struct sock *sk) { return 0; }
void t_cong_avoid(struct sock *sk, unsigned int ack, unsigned int cwnd) { }
void t_set_state(struct sock *sk, unsigned char new_state) { }
void t_cwnd_event(struct sock *sk, enum tcp_ca_event ev) { }
unsigned int t_undo_cwnd(struct sock *sk) { return 0; }
unsigned int t_sndbuf_expand(struct sock *sk) { return 0; }
void t_cong_control(struct sock *sk, const void *rs) { }
unsigned int t_min_tso_segs(struct sock *sk) { return 1; }
unsigned int t_get_info(struct sock *sk, unsigned int ext, unsigned int *data, void *attr) { return 0; }
void t_init(struct sock *sk) { }
void t_release(struct sock *sk) { }
EOF
CC1="${CC1:-/root/clangpre/clang-r487747c/bin/clang}"
CC2="${CC2:-$(command -v clang || true)}"

for cc in "$CC1" "$CC2"; do
  if [ ! -x "$cc" ]; then
    echo "skip missing compiler: $cc"
    continue
  fi
  echo "===== $cc ====="
  "$cc" --version | head -1
  "$cc" --target=aarch64-linux-gnu -fsanitize=kcfi -O2 -S -emit-llvm /tmp/kcfi_test.c -o /tmp/kcfi.ll
  grep -oE '@t_[a-z_]+\([^)]*\)[^{]*!kcfi_type !![0-9]+' /tmp/kcfi.ll | sed 's/ noundef//g; s/ nofree//g' | sort
  echo "-- hashes --"
  grep -E '^![0-9]+ = !\{i32 -?[0-9]+\}' /tmp/kcfi.ll | head -12
done
