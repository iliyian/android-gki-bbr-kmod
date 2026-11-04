#!/usr/bin/env python3
"""Build a staging tree with our own BBRv3 backport for ACK 6.1.138.

Reference: google/bbr branch v3 (90210de, based on Linux 6.13.7).
We port only the pieces BBRv3 needs, and drop PLB/ecn_low (they are
dormant without the RTAX_FEATURE_ECN_LOW route opt-in).
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ACK = os.path.join(BASE, "ack138")
STAGE = os.path.join(BASE, "stage")

FILES = [
    "include/linux/tcp.h",
    "include/net/tcp.h",
    "include/uapi/linux/inet_diag.h",
    "net/ipv4/tcp.c",
    "net/ipv4/tcp_cong.c",
    "net/ipv4/tcp_input.c",
    "net/ipv4/tcp_output.c",
    "net/ipv4/tcp_rate.c",
    "net/ipv4/tcp_timer.c",
]


def readf(p):
    with open(p, newline="") as f:
        return f.read()


def writef(p, s):
    with open(p, "w", newline="") as f:
        f.write(s)


def rep(text, old, new, path, n=1):
    cnt = text.count(old)
    if cnt != n:
        raise SystemExit(
            "ANCHOR MISS in %s: expected %d, found %d:\n%s" % (path, n, cnt, old[:400])
        )
    return text.replace(old, new)


def stage_core():
    for rel in FILES:
        src = os.path.join(ACK, rel)
        dst = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)

    # ---------------- include/linux/tcp.h ----------------
    rel = "include/linux/tcp.h"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """	u8	dup_ack_counter:2,
		tlp_retrans:1,	/* TLP is a retransmission */
		unused:5;""", """	u8	dup_ack_counter:2,
		tlp_retrans:1,	/* TLP is a retransmission */
		fast_ack_mode:1,/* ack ASAP if >1 rcv_mss received? */
		tlp_orig_data_app_limited:1, /* app-limited before TLP rtx? */
		unused:3;""", p)
    writef(p, s)

    # ---------------- include/uapi/linux/inet_diag.h ----------------
    rel = "include/uapi/linux/inet_diag.h"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """	__u32	bbr_cwnd_gain;		/* cwnd gain shifted left 8 bits */
};

union tcp_cc_info {""", """	__u32	bbr_cwnd_gain;		/* cwnd gain shifted left 8 bits */
	__u32	bbr_bw_hi_lsb;		/* lower 32 bits of bw_hi */
	__u32	bbr_bw_hi_msb;		/* upper 32 bits of bw_hi */
	__u32	bbr_bw_lo_lsb;		/* lower 32 bits of bw_lo */
	__u32	bbr_bw_lo_msb;		/* upper 32 bits of bw_lo */
	__u8	bbr_mode;		/* current bbr_mode in state machine */
	__u8	bbr_phase;		/* current state machine phase */
	__u8	unused1;		/* alignment padding; not used yet */
	__u8	bbr_version;		/* BBR algorithm version */
	__u32	bbr_inflight_lo;	/* lower short-term data volume bound */
	__u32	bbr_inflight_hi;	/* higher long-term data volume bound */
	__u32	bbr_extra_acked;	/* max excess packets ACKed in epoch */
};

/* TCP BBR congestion control bbr_phase as reported in netlink/ss stats. */
enum tcp_bbr_phase {
	BBR_PHASE_INVALID		= 0,
	BBR_PHASE_STARTUP		= 1,
	BBR_PHASE_DRAIN			= 2,
	BBR_PHASE_PROBE_RTT		= 3,
	BBR_PHASE_PROBE_BW_UP		= 4,
	BBR_PHASE_PROBE_BW_DOWN		= 5,
	BBR_PHASE_PROBE_BW_CRUISE	= 6,
	BBR_PHASE_PROBE_BW_REFILL	= 7,
};

union tcp_cc_info {""", p)
    writef(p, s)

    # ---------------- include/net/tcp.h ----------------
    rel = "include/net/tcp.h"
    p = os.path.join(STAGE, rel)
    s = readf(p)

    s = rep(s, """#define	TCP_ECN_QUEUE_CWR	2
#define	TCP_ECN_DEMAND_CWR	4
#define	TCP_ECN_SEEN		8
""", """#define	TCP_ECN_QUEUE_CWR	2
#define	TCP_ECN_DEMAND_CWR	4
#define	TCP_ECN_SEEN		8
#define	TCP_ECN_LOW		16
#define	TCP_ECN_ECT_PERMANENT	32
""", p)

    s = rep(s, """static inline u32 tcp_stamp_us_delta(u64 t1, u64 t0)
{
	return max_t(s64, t1 - t0, 0);
}
""", """static inline u32 tcp_stamp_us_delta(u64 t1, u64 t0)
{
	return max_t(s64, t1 - t0, 0);
}

static inline u32 tcp_stamp32_us_delta(u32 t1, u32 t0)
{
	return max_t(s32, t1 - t0, 0);
}
""", p)

    s = rep(s, """			/* pkts S/ACKed so far upon tx of skb, incl retrans: */
			__u32 delivered;
			/* start of send pipeline phase */
			u64 first_tx_mstamp;
			/* when we reached the "delivered" count */
			u64 delivered_mstamp;
		} tx;   /* only used for outgoing skbs */""",
"""			/* pkts S/ACKed so far upon tx of skb, incl retrans: */
			__u32 delivered;
			/* start of send pipeline phase */
			u32 first_tx_mstamp;
			/* when we reached the "delivered" count */
			u32 delivered_mstamp;
#define TCPCB_IN_FLIGHT_BITS 20
#define TCPCB_IN_FLIGHT_MAX ((1U << TCPCB_IN_FLIGHT_BITS) - 1)
			u32 in_flight:20,   /* packets in flight at transmit */
			    unused2:12;
			u32 lost;	/* packets lost so far upon tx of skb */
		} tx;   /* only used for outgoing skbs */""", p)

    s = rep(s, """	CA_EVENT_ECN_IS_CE,	/* received CE marked IP packet */
};""", """	CA_EVENT_ECN_IS_CE,	/* received CE marked IP packet */
	CA_EVENT_TLP_RECOVERY,	/* a lost segment was repaired by TLP probe */
};""", p)

    s = rep(s, """/* Requires ECN/ECT set on all packets */
#define TCP_CONG_NEEDS_ECN	0x2
#define TCP_CONG_MASK	(TCP_CONG_NON_RESTRICTED | TCP_CONG_NEEDS_ECN)""",
"""/* Requires ECN/ECT set on all packets */
#define TCP_CONG_NEEDS_ECN	0x2
/* Wants notification of CE events (CA_EVENT_ECN_IS_CE, CA_EVENT_ECN_NO_CE). */
#define TCP_CONG_WANTS_CE_EVENTS	0x4
#define TCP_CONG_MASK	(TCP_CONG_NON_RESTRICTED | \\
			 TCP_CONG_NEEDS_ECN | \\
			 TCP_CONG_WANTS_CE_EVENTS)""", p)

    s = rep(s, """struct rate_sample {
	u64  prior_mstamp; /* starting timestamp for interval */
	u32  prior_delivered;	/* tp->delivered at "prior_mstamp" */
	u32  prior_delivered_ce;/* tp->delivered_ce at "prior_mstamp" */
	s32  delivered;		/* number of packets delivered over interval */
	s32  delivered_ce;	/* number of packets delivered w/ CE marks*/
	long interval_us;	/* time for tp->delivered to incr "delivered" */
	u32 snd_interval_us;	/* snd interval for delivered packets */
	u32 rcv_interval_us;	/* rcv interval for delivered packets */
	long rtt_us;		/* RTT of last (S)ACKed packet (or -1) */
	int  losses;		/* number of packets marked lost upon ACK */
	u32  acked_sacked;	/* number of packets newly (S)ACKed upon ACK */
	u32  prior_in_flight;	/* in flight before this ACK */
	u32  last_end_seq;	/* end_seq of most recently ACKed packet */
	bool is_app_limited;	/* is sample from packet with bubble in pipe? */
	bool is_retrans;	/* is sample from retransmission? */
	bool is_ack_delayed;	/* is this (likely) a delayed ACK? */
};""",
"""struct rate_sample {
	u64  prior_mstamp; /* starting timestamp for interval */
	u32  prior_lost;	/* tp->lost at "prior_mstamp" */
	u32  prior_delivered;	/* tp->delivered at "prior_mstamp" */
	u32  prior_delivered_ce;/* tp->delivered_ce at "prior_mstamp" */
	u32  tx_in_flight;	/* packets in flight at starting timestamp */
	s32  lost;		/* number of packets lost over interval */
	s32  delivered;		/* number of packets delivered over interval */
	s32  delivered_ce;	/* packets delivered w/ CE mark over interval */
	long interval_us;	/* time for tp->delivered to incr "delivered" */
	u32 snd_interval_us;	/* snd interval for delivered packets */
	u32 rcv_interval_us;	/* rcv interval for delivered packets */
	long rtt_us;		/* RTT of last (S)ACKed packet (or -1) */
	int  losses;		/* number of packets marked lost upon ACK */
	u32  acked_sacked;	/* number of packets newly (S)ACKed upon ACK */
	u32  prior_in_flight;	/* in flight before this ACK */
	u32  last_end_seq;	/* end_seq of most recently ACKed packet */
	bool is_app_limited;	/* is sample from packet with bubble in pipe? */
	bool is_retrans;	/* is sample from retransmission? */
	bool is_acking_tlp_retrans_seq;  /* ACKed a TLP retransmit sequence? */
	bool is_ack_delayed;	/* is this (likely) a delayed ACK? */
	bool is_ece;		/* did this ACK have ECN marked? */
};""", p)

    s = rep(s, """	/* override sysctl_tcp_min_tso_segs */
	u32 (*min_tso_segs)(struct sock *sk);
""", """	/* pick target number of segments per TSO/GSO skb (optional): */
	u32 (*tso_segs)(struct sock *sk, unsigned int mss_now);

	/* react to a specific lost skb (optional) */
	void (*skb_marked_lost)(struct sock *sk, const struct sk_buff *skb);
""", p)

    s = rep(s, """static inline bool tcp_ca_needs_ecn(const struct sock *sk)""",
"""static inline bool tcp_ca_wants_ce_events(const struct sock *sk)
{
	const struct inet_connection_sock *icsk = inet_csk(sk);

	return icsk->icsk_ca_ops->flags & (TCP_CONG_NEEDS_ECN |
					   TCP_CONG_WANTS_CE_EVENTS);
}

static inline bool tcp_ca_needs_ecn(const struct sock *sk)""", p)

    s = rep(s, """/* From tcp_rate.c */
void tcp_rate_skb_sent(struct sock *sk, struct sk_buff *skb);""",
"""/* From tcp_rate.c */
void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb);
void tcp_rate_skb_sent(struct sock *sk, struct sk_buff *skb);""", p)

    s = rep(s, """static inline bool tcp_skb_sent_after(u64 t1, u64 t2, u32 seq1, u32 seq2)
{
	return t1 > t2 || (t1 == t2 && after(seq1, seq2));
}
""", """static inline bool tcp_skb_sent_after(u64 t1, u64 t2, u32 seq1, u32 seq2)
{
	return t1 > t2 || (t1 == t2 && after(seq1, seq2));
}

/* If a retransmit failed due to local qdisc congestion or other local issues,
 * then we may have called tcp_set_skb_tso_segs() to increase the number of
 * segments in the skb without increasing the tx.in_flight. In all other cases,
 * the tx.in_flight should be at least as big as the pcount of the sk_buff.  We
 * do not have the state to know whether a retransmit failed due to local qdisc
 * congestion or other local issues, so to avoid spurious warnings we consider
 * that any skb marked lost may have suffered that fate.
 */
static inline bool tcp_skb_tx_in_flight_is_suspicious(u32 skb_pcount,
						      u32 skb_sacked_flags,
						      u32 tx_in_flight)
{
	return (skb_pcount > tx_in_flight) && !(skb_sacked_flags & TCPCB_LOST);
}
""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp_rate.c ----------------
    rel = "net/ipv4/tcp_rate.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """/* Snapshot the current delivery information in the skb, to generate
 * a rate sample later when the skb is (s)acked in tcp_rate_skb_delivered().
 */""", """void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb)
{
	struct tcp_sock *tp = tcp_sk(sk);
	u32 in_flight;

	/* Check, sanitize, and record packets in flight after skb was sent. */
	in_flight = tcp_packets_in_flight(tp) + tcp_skb_pcount(skb);
	if (WARN_ONCE(in_flight > TCPCB_IN_FLIGHT_MAX,
		      "insane in_flight %u cc %s mss %u "
		      "cwnd %u pif %u %u %u %u\\n",
		      in_flight, inet_csk(sk)->icsk_ca_ops->name,
		      tp->mss_cache, tcp_snd_cwnd(tp),
		      tp->packets_out, tp->retrans_out,
		      tp->sacked_out, tp->lost_out))
		in_flight = TCPCB_IN_FLIGHT_MAX;
	TCP_SKB_CB(skb)->tx.in_flight = in_flight;
}

/* Snapshot the current delivery information in the skb, to generate
 * a rate sample later when the skb is (s)acked in tcp_rate_skb_delivered().
 */""", p)

    s = rep(s, """	TCP_SKB_CB(skb)->tx.delivered_ce	= tp->delivered_ce;
	TCP_SKB_CB(skb)->tx.is_app_limited	= tp->app_limited ? 1 : 0;
}""", """	TCP_SKB_CB(skb)->tx.delivered_ce	= tp->delivered_ce;
	TCP_SKB_CB(skb)->tx.lost		= tp->lost;
	TCP_SKB_CB(skb)->tx.is_app_limited	= tp->app_limited ? 1 : 0;
	tcp_set_tx_in_flight(sk, skb);
}""", p)

    s = rep(s, """		rs->prior_delivered_ce  = scb->tx.delivered_ce;
		rs->prior_delivered  = scb->tx.delivered;
		rs->prior_mstamp     = scb->tx.delivered_mstamp;
		rs->is_app_limited   = scb->tx.is_app_limited;
		rs->is_retrans	     = scb->sacked & TCPCB_RETRANS;
		rs->last_end_seq     = scb->end_seq;

		/* Record send time of most recently ACKed packet: */
		tp->first_tx_mstamp  = tx_tstamp;
		/* Find the duration of the "send phase" of this window: */
		rs->interval_us = tcp_stamp_us_delta(tp->first_tx_mstamp,
						     scb->tx.first_tx_mstamp);""",
"""		rs->prior_lost	     = scb->tx.lost;
		rs->prior_delivered_ce  = scb->tx.delivered_ce;
		rs->prior_delivered  = scb->tx.delivered;
		rs->prior_mstamp     = scb->tx.delivered_mstamp;
		rs->is_app_limited   = scb->tx.is_app_limited;
		rs->is_retrans	     = scb->sacked & TCPCB_RETRANS;
		rs->tx_in_flight     = scb->tx.in_flight;
		rs->last_end_seq     = scb->end_seq;

		/* Record send time of most recently ACKed packet: */
		tp->first_tx_mstamp  = tx_tstamp;
		/* Find the duration of the "send phase" of this window: */
		rs->interval_us      = tcp_stamp32_us_delta(
						tp->first_tx_mstamp,
						scb->tx.first_tx_mstamp);""", p)

    s = rep(s, """	rs->delivered   = tp->delivered - rs->prior_delivered;
""", """	rs->delivered   = tp->delivered - rs->prior_delivered;
	rs->lost        = tp->lost - rs->prior_lost;
""", p)

    s = rep(s, """	ack_us = tcp_stamp_us_delta(tp->tcp_mstamp,
				    rs->prior_mstamp); /* ack phase */""",
"""	ack_us = tcp_stamp32_us_delta(tp->tcp_mstamp,
				      rs->prior_mstamp); /* ack phase */""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp_input.c ----------------
    rel = "net/ipv4/tcp_input.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)

    s = rep(s, """	case INET_ECN_CE:
		if (tcp_ca_needs_ecn(sk))
			tcp_ca_event(sk, CA_EVENT_ECN_IS_CE);""",
"""	case INET_ECN_CE:
		if (tcp_ca_wants_ce_events(sk))
			tcp_ca_event(sk, CA_EVENT_ECN_IS_CE);""", p)
    s = rep(s, """	default:
		if (tcp_ca_needs_ecn(sk))
			tcp_ca_event(sk, CA_EVENT_ECN_NO_CE);""",
"""	default:
		if (tcp_ca_wants_ce_events(sk))
			tcp_ca_event(sk, CA_EVENT_ECN_NO_CE);""", p)

    s = rep(s, """static void tcp_notify_skb_loss_event(struct tcp_sock *tp, const struct sk_buff *skb)
{
	tp->lost += tcp_skb_pcount(skb);
}""", """static void tcp_notify_skb_loss_event(struct tcp_sock *tp, const struct sk_buff *skb)
{
	struct sock *sk = (struct sock *)tp;
	const struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;

	tp->lost += tcp_skb_pcount(skb);
	if (ca_ops->skb_marked_lost)
		ca_ops->skb_marked_lost(sk, skb);
}""", p)

    s = rep(s, """	tcp_skb_pcount_add(prev, pcount);
	WARN_ON_ONCE(tcp_skb_pcount(skb) < pcount);
	tcp_skb_pcount_add(skb, -pcount);
""", """	tcp_skb_pcount_add(prev, pcount);
	WARN_ON_ONCE(tcp_skb_pcount(skb) < pcount);
	tcp_skb_pcount_add(skb, -pcount);

	/* Adjust tx.in_flight as pcount is shifted from skb to prev. */
	if (WARN_ONCE(TCP_SKB_CB(skb)->tx.in_flight < pcount,
		      "prev in_flight: %u skb in_flight: %u pcount: %u",
		      TCP_SKB_CB(prev)->tx.in_flight,
		      TCP_SKB_CB(skb)->tx.in_flight,
		      pcount))
		TCP_SKB_CB(skb)->tx.in_flight = 0;
	else
		TCP_SKB_CB(skb)->tx.in_flight -= pcount;
	TCP_SKB_CB(prev)->tx.in_flight += pcount;
""", p)

    s = rep(s, """static void tcp_process_tlp_ack(struct sock *sk, u32 ack, int flag)
{""", """static void tcp_process_tlp_ack(struct sock *sk, u32 ack, int flag,
				struct rate_sample *rs)
{""", p)

    s = rep(s, """		/* ACK advances: there was a loss, so reduce cwnd. Reset
		 * tlp_high_seq in tcp_init_cwnd_reduction()
		 */
		tcp_init_cwnd_reduction(sk);""",
"""		/* ACK advances: there was a loss, so reduce cwnd. Reset
		 * tlp_high_seq in tcp_init_cwnd_reduction()
		 */
		tcp_ca_event(sk, CA_EVENT_TLP_RECOVERY);
		tcp_init_cwnd_reduction(sk);""", p)

    s = rep(s, """		/* Pure dupack: original and TLP probe arrived; no loss */
		tp->tlp_high_seq = 0;
	}
}""", """		/* Pure dupack: original and TLP probe arrived; no loss */
		tp->tlp_high_seq = 0;
	} else {
		/* This ACK matches a TLP retransmit. We cannot yet tell if
		 * this ACK is for the original or the TLP retransmit.
		 */
		rs->is_acking_tlp_retrans_seq = 1;
	}
}""", p)

    s = rep(s, """	prior_fack = tcp_is_sack(tp) ? tcp_highest_sack_seq(tp) : tp->snd_una;
	rs.prior_in_flight = tcp_packets_in_flight(tp);
""", """	prior_fack = tcp_is_sack(tp) ? tcp_highest_sack_seq(tp) : tp->snd_una;
	rs.prior_in_flight = tcp_packets_in_flight(tp);
	tcp_rate_check_app_limited(sk);
""", p)

    s = rep(s, """	if (tp->tlp_high_seq)
		tcp_process_tlp_ack(sk, ack, flag);
""", """	if (tp->tlp_high_seq)
		tcp_process_tlp_ack(sk, ack, flag, &rs);
""", p, n=2)

    s = rep(s, """	rs.is_ack_delayed = !!(flag & FLAG_ACK_MAYBE_DELAYED);
	tcp_rate_gen(sk, delivered, lost, is_sack_reneg, sack_state.rate);""",
"""	rs.is_ack_delayed = !!(flag & FLAG_ACK_MAYBE_DELAYED);
	rs.is_ece = !!(flag & FLAG_ECE);
	tcp_rate_gen(sk, delivered, lost, is_sack_reneg, sack_state.rate);""", p)

    s = rep(s, """	if (((tp->rcv_nxt - tp->rcv_wup) > inet_csk(sk)->icsk_ack.rcv_mss &&
	     /* ... and right edge of window advances far enough.
	      * (tcp_recvmsg() will send ACK otherwise).
	      * If application uses SO_RCVLOWAT, we want send ack now if
	      * we have not received enough bytes to satisfy the condition.
	      */
	    (tp->rcv_nxt - tp->copied_seq < sk->sk_rcvlowat ||
	     __tcp_select_window(sk) >= tp->rcv_wnd)) ||""",
"""	if (((tp->rcv_nxt - tp->rcv_wup) > inet_csk(sk)->icsk_ack.rcv_mss &&
	     (tp->fast_ack_mode == 1 ||
	     /* ... and right edge of window advances far enough.
	      * (tcp_recvmsg() will send ACK otherwise).
	      * If application uses SO_RCVLOWAT, we want send ack now if
	      * we have not received enough bytes to satisfy the condition.
	      */
	      (tp->rcv_nxt - tp->copied_seq < sk->sk_rcvlowat ||
	       __tcp_select_window(sk) >= tp->rcv_wnd))) ||""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp_output.c ----------------
    rel = "net/ipv4/tcp_output.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)

    s = rep(s, """		} else if (!tcp_ca_needs_ecn(sk)) {
			/* ACK or retransmitted segment: clear ECT|CE */""",
"""		} else if (!(tp->ecn_flags & TCP_ECN_ECT_PERMANENT) &&
			   !tcp_ca_needs_ecn(sk)) {
			/* ACK or retransmitted segment: clear ECT|CE */""", p)

    s = rep(s, """	int nsize, old_factor;
""", """	int nsize, old_factor, inflight_prev;
""", p)

    s = rep(s, """		if (diff)
			tcp_adjust_pcount(sk, skb, diff);
	}
""", """		if (diff)
			tcp_adjust_pcount(sk, skb, diff);

		inflight_prev = TCP_SKB_CB(skb)->tx.in_flight - old_factor;
		if (inflight_prev < 0) {
			WARN_ONCE(tcp_skb_tx_in_flight_is_suspicious(
					  old_factor,
					  TCP_SKB_CB(skb)->sacked,
					  TCP_SKB_CB(skb)->tx.in_flight),
				  "inconsistent: tx.in_flight: %u "
				  "old_factor: %d mss: %u sacked: %u "
				  "1st pcount: %d 2nd pcount: %d "
				  "1st len: %u 2nd len: %u ",
				  TCP_SKB_CB(skb)->tx.in_flight, old_factor,
				  mss_now, TCP_SKB_CB(skb)->sacked,
				  tcp_skb_pcount(skb), tcp_skb_pcount(buff),
				  skb->len, buff->len);
			inflight_prev = 0;
		}
		/* Set 1st tx.in_flight as if 1st were sent by itself: */
		TCP_SKB_CB(skb)->tx.in_flight = inflight_prev +
						 tcp_skb_pcount(skb);
		/* Set 2nd tx.in_flight with new 1st and 2nd pcounts: */
		TCP_SKB_CB(buff)->tx.in_flight = inflight_prev +
						 tcp_skb_pcount(skb) +
						 tcp_skb_pcount(buff);
	}
""", p)

    s = rep(s, """static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)
{
	const struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;
	u32 min_tso, tso_segs;

	min_tso = ca_ops->min_tso_segs ?
			ca_ops->min_tso_segs(sk) :
			READ_ONCE(sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);

	tso_segs = tcp_tso_autosize(sk, mss_now, min_tso);
	return min_t(u32, tso_segs, sk->sk_gso_max_segs);
}""", """static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)
{
	const struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;
	u32 tso_segs;

	tso_segs = ca_ops->tso_segs ?
		ca_ops->tso_segs(sk, mss_now) :
		tcp_tso_autosize(sk, mss_now,
				 sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);
	return min_t(u32, tso_segs, sk->sk_gso_max_segs);
}""", p)

    s = rep(s, """			tcp_init_tso_segs(skb, mss_now);
			goto repair; /* Skip network transmission */""",
"""			tcp_init_tso_segs(skb, mss_now);
			tcp_set_tx_in_flight(sk, skb);
			goto repair; /* Skip network transmission */""", p)

    s = rep(s, """	if (WARN_ON(!skb || !tcp_skb_pcount(skb)))
		goto rearm_timer;

	if (__tcp_retransmit_skb(sk, skb, 1))
		goto rearm_timer;
""", """	if (WARN_ON(!skb || !tcp_skb_pcount(skb)))
		goto rearm_timer;

	tp->tlp_orig_data_app_limited = TCP_SKB_CB(skb)->tx.is_app_limited;
	if (__tcp_retransmit_skb(sk, skb, 1))
		goto rearm_timer;
""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp_cong.c ----------------
    rel = "net/ipv4/tcp_cong.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """	tcp_sk(sk)->prior_ssthresh = 0;
	if (icsk->icsk_ca_ops->init)""",
"""	tcp_sk(sk)->prior_ssthresh = 0;
	tcp_sk(sk)->fast_ack_mode = 0;
	if (icsk->icsk_ca_ops->init)""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp.c ----------------
    rel = "net/ipv4/tcp.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """	tp->rcv_ooopack = 0;
""", """	tp->rcv_ooopack = 0;
	tp->fast_ack_mode = 0;
""", p)
    writef(p, s)

    # ---------------- net/ipv4/tcp_timer.c ----------------
    rel = "net/ipv4/tcp_timer.c"
    p = os.path.join(STAGE, rel)
    s = readf(p)
    s = rep(s, """	tcp_mstamp_refresh(tcp_sk(sk));
	event = icsk->icsk_pending;""",
"""	tcp_rate_check_app_limited(sk);
	tcp_mstamp_refresh(tcp_sk(sk));
	event = icsk->icsk_pending;""", p)
    writef(p, s)


PRIV_CODE = """
/* BBRv3 keeps its state in a separately allocated object, because struct bbr
 * is larger than ICSK_CA_PRIV_SIZE on this kernel.  icsk_ca_priv stores a
 * small header with the pointer, the owning socket, and a magic tag.  The
 * owner check matters for passively opened children: sk_clone_lock() copies
 * the listener's icsk_ca_priv, and a child must never share (or free) the
 * listener's state.
 */
#define BBR_PRIV_MAGIC 0x42425276334d4147ULL

struct bbr_sock_priv {
	struct bbr *state;
	struct sock *owner;
	u64 magic;
};

static struct bbr *bbr_priv(const struct sock *sk)
{
	struct bbr_sock_priv *p =
		(struct bbr_sock_priv *)inet_csk(sk)->icsk_ca_priv;

	if (p->magic == BBR_PRIV_MAGIC && p->owner == sk)
		return p->state;
	return NULL;
}
"""


def stage_bbr():
    src = os.path.join(BASE, "tcp_bbr.c")
    dst = os.path.join(STAGE, "net/ipv4/tcp_bbr.c")
    s = readf(src)

    def r(old, new, n=1):
        nonlocal s
        cnt = s.count(old)
        if cnt != n:
            raise SystemExit("BBR ANCHOR MISS: expected %d found %d:\n%s" % (n, cnt, old[:300]))
        s = s.replace(old, new)

    # drop BPF plumbing
    r("#include <linux/btf.h>\n", "")
    r("#include <linux/btf_ids.h>\n", "")
    s = s.replace("__bpf_kfunc ", "")

    # 6.1 cong_control signature: (sk, rs)
    r("""static void bbr_main(struct sock *sk, u32 ack, int flag,
				 const struct rate_sample *rs)""",
      """static void bbr_main(struct sock *sk, const struct rate_sample *rs)""")

    # drop PLB
    r("""	u8	unused_4;		/* to preserve alignment */
	struct tcp_plb_state plb;
};""", """	u8	unused_4;		/* to preserve alignment */
};""")
    r("""/* Protective Load Balancing (PLB). PLB rehashes outgoing data (to a new IPv6
 * flow label) if it encounters sustained congestion in the form of ECN marks.
 */
static void bbr_plb(struct sock *sk, const struct rate_sample *rs, int ce_ratio)
{
	struct bbr *bbr = inet_csk_ca(sk);

	if (bbr->round_start && ce_ratio >= 0)
		tcp_plb_update_state(sk, &bbr->plb, ce_ratio);

	tcp_plb_check_rehash(sk, &bbr->plb);
}

""", "")
    r("""	struct net *net = sock_net(sk);
	struct bbr *bbr = inet_csk_ca(sk);
	s32 delivered, delivered_ce;""",
"""	struct bbr *bbr = inet_csk_ca(sk);
	s32 delivered, delivered_ce;""")
    r("""	/* Skip updating alpha only if not ECN-eligible and PLB is disabled. */
	want_ecn_alpha = (bbr->ecn_eligible ||
			  (bbr_can_use_ecn(sk) &&
			   READ_ONCE(net->ipv4.sysctl_tcp_plb_enabled)));
	if (!want_ecn_alpha)""",
"""	want_ecn_alpha = bbr->ecn_eligible;
	if (!want_ecn_alpha)""")
    r("""	BUILD_BUG_ON(BBR_SCALE != TCP_PLB_SCALE);
	ce_ratio = (u64)delivered_ce << BBR_SCALE;""",
"""	ce_ratio = (u64)delivered_ce << BBR_SCALE;""")
    r("""		bbr->prev_ca_state = TCP_CA_Loss;
		tcp_plb_update_state_upon_rto(sk, &bbr->plb);
""", """		bbr->prev_ca_state = TCP_CA_Loss;
""")

    # bbr_main: no PLB, keep alpha update side effects
    r("""	u32 bw, round_delivered;
	int ce_ratio = -1;

	round_delivered = bbr_update_round_start(sk, rs, &ctx);
	if (bbr->round_start) {
		bbr->rounds_since_probe =
			min_t(s32, bbr->rounds_since_probe + 1, 0xFF);
		ce_ratio = bbr_update_ecn_alpha(sk);
	}
	bbr_plb(sk, rs, ce_ratio);
""", """	u32 bw, round_delivered;

	round_delivered = bbr_update_round_start(sk, rs, &ctx);
	if (bbr->round_start) {
		bbr->rounds_since_probe =
			min_t(s32, bbr->rounds_since_probe + 1, 0xFF);
		bbr_update_ecn_alpha(sk);
	}
""")

    # bbr_init: dynamic state allocation
    r("""	bbr->plb.pause_until = 0;

	tp->fast_ack_mode = bbr_fast_ack_mode ? 1 : 0;""",
"""	tp->fast_ack_mode = bbr_fast_ack_mode ? 1 : 0;""")
    r("""static void bbr_init(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = inet_csk_ca(sk);

	bbr->initialized = 1;""",
"""static void bbr_init(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = bbr_priv(sk);

	if (!bbr) {
		struct bbr_sock_priv *p =
			(struct bbr_sock_priv *)inet_csk(sk)->icsk_ca_priv;
		gfp_t gfp = in_softirq() ? GFP_ATOMIC : GFP_KERNEL;

		bbr = kzalloc(sizeof(*bbr), gfp);
		if (!bbr) {
			pr_warn_ratelimited("bbr3: cannot allocate per-socket state\\n");
			return;
		}
		p->state = bbr;
		p->owner = sk;
		p->magic = BBR_PRIV_MAGIC;
	}

	bbr->mode = BBR_STARTUP;""")

    # all state accesses go through the priv header
    s = s.replace("inet_csk_ca(sk)", "bbr_priv(sk)")

    # insert priv helpers after struct bbr { ... };
    anchor = """	u8	unused_4;		/* to preserve alignment */
};
"""
    r(anchor + "\nstruct bbr_context {", anchor + "\n" + PRIV_CODE + "\nstruct bbr_context {")

    # NULL guards for entry points
    r("""static u32 bbr_tso_segs(struct sock *sk, unsigned int mss_now)
{
	return bbr_tso_segs_generic(sk, mss_now, sk->sk_gso_max_size);
}""", """static u32 bbr_tso_segs(struct sock *sk, unsigned int mss_now)
{
	if (!bbr_priv(sk))
		return READ_ONCE(sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);
	return bbr_tso_segs_generic(sk, mss_now, sk->sk_gso_max_size);
}""")

    r("""static void bbr_cwnd_event(struct sock *sk, enum tcp_ca_event event)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = bbr_priv(sk);
""", """static void bbr_cwnd_event(struct sock *sk, enum tcp_ca_event event)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = bbr_priv(sk);

	if (!bbr)
		return;
""")

    r("""	struct bbr_context ctx = { 0 };
	bool update_model = true;
	u32 bw, round_delivered;
""", """	struct bbr_context ctx = { 0 };
	bool update_model = true;
	u32 bw, round_delivered;

	if (!bbr)
		return;
""")

    r("""	struct tcp_skb_cb *scb = TCP_SKB_CB(skb);
	struct rate_sample rs = {};

	bbr_note_loss(sk);""", """	struct tcp_skb_cb *scb = TCP_SKB_CB(skb);
	struct rate_sample rs = {};

	if (!bbr)
		return;

	bbr_note_loss(sk);""")

    r("""static u32 bbr_undo_cwnd(struct sock *sk)
{
	struct bbr *bbr = bbr_priv(sk);
""", """static u32 bbr_undo_cwnd(struct sock *sk)
{
	struct bbr *bbr = bbr_priv(sk);

	if (!bbr)
		return tcp_snd_cwnd(tcp_sk(sk));
""")

    r("""static u32 bbr_ssthresh(struct sock *sk)
{
	struct bbr *bbr = bbr_priv(sk);
""", """static u32 bbr_ssthresh(struct sock *sk)
{
	struct bbr *bbr = bbr_priv(sk);

	if (!bbr)
		return tcp_sk(sk)->snd_ssthresh;
""")

    r("""	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = bbr_priv(sk);

	if (new_state == TCP_CA_Loss) {""", """	struct tcp_sock *tp = tcp_sk(sk);
	struct bbr *bbr = bbr_priv(sk);

	if (!bbr)
		return;

	if (new_state == TCP_CA_Loss) {""")

    r("""		struct bbr *bbr = bbr_priv(sk);
		u64 bw = bbr_bw_bytes_per_sec(sk, bbr_bw(sk));
		u64 bw_hi = bbr_bw_bytes_per_sec(sk, bbr_max_bw(sk));
		u64 bw_lo = bbr->bw_lo == ~0U ?
			~0ULL : bbr_bw_bytes_per_sec(sk, bbr->bw_lo);
		struct tcp_bbr_info *bbr_info = &info->bbr;
""", """		struct bbr *bbr = bbr_priv(sk);
		struct tcp_bbr_info *bbr_info = &info->bbr;
		u64 bw, bw_hi, bw_lo;

		if (!bbr)
			return 0;

		bw = bbr_bw_bytes_per_sec(sk, bbr_bw(sk));
		bw_hi = bbr_bw_bytes_per_sec(sk, bbr_max_bw(sk));
		bw_lo = bbr->bw_lo == ~0U ?
			~0ULL : bbr_bw_bytes_per_sec(sk, bbr->bw_lo);
""")

    # release + registration name
    r("""static struct tcp_congestion_ops tcp_bbr_cong_ops __read_mostly = {
	.flags		= TCP_CONG_NON_RESTRICTED | TCP_CONG_WANTS_CE_EVENTS,
	.name		= "bbr",""",
"""static void bbr_release(struct sock *sk)
{
	struct bbr_sock_priv *p =
		(struct bbr_sock_priv *)inet_csk(sk)->icsk_ca_priv;

	if (p->magic == BBR_PRIV_MAGIC && p->owner == sk) {
		kfree(p->state);
		p->state = NULL;
		p->owner = NULL;
		p->magic = 0;
	}
}

static struct tcp_congestion_ops tcp_bbr_cong_ops __read_mostly = {
	.flags		= TCP_CONG_NON_RESTRICTED | TCP_CONG_WANTS_CE_EVENTS,
	.name		= "bbr3",""")
    r("""	.set_state	= bbr_set_state,
};""", """	.set_state	= bbr_set_state,
	.release	= bbr_release,
};""")

    # registration: no BTF kfunc set
    r("""BTF_KFUNCS_START(tcp_bbr_check_kfunc_ids)
BTF_ID_FLAGS(func, bbr_init)
BTF_ID_FLAGS(func, bbr_main)
BTF_ID_FLAGS(func, bbr_sndbuf_expand)
BTF_ID_FLAGS(func, bbr_skb_marked_lost)
BTF_ID_FLAGS(func, bbr_undo_cwnd)
BTF_ID_FLAGS(func, bbr_cwnd_event)
BTF_ID_FLAGS(func, bbr_ssthresh)
BTF_ID_FLAGS(func, bbr_tso_segs)
BTF_ID_FLAGS(func, bbr_set_state)
BTF_KFUNCS_END(tcp_bbr_check_kfunc_ids)

static const struct btf_kfunc_id_set tcp_bbr_kfunc_set = {
	.owner = THIS_MODULE,
	.set   = &tcp_bbr_check_kfunc_ids,
};

static int __init bbr_register(void)
{
	int ret;

	BUILD_BUG_ON(sizeof(struct bbr) > ICSK_CA_PRIV_SIZE);

	ret = register_btf_kfunc_id_set(BPF_PROG_TYPE_STRUCT_OPS, &tcp_bbr_kfunc_set);
	if (ret < 0)
		return ret;
	return tcp_register_congestion_control(&tcp_bbr_cong_ops);
}""", """static int __init bbr_register(void)
{
	BUILD_BUG_ON(sizeof(struct bbr_sock_priv) > ICSK_CA_PRIV_SIZE);

	return tcp_register_congestion_control(&tcp_bbr_cong_ops);
}""")

    writef(dst, s)


if __name__ == "__main__":
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)
    stage_core()
    stage_bbr()
    print("staged OK")
    for root, _, names in os.walk(STAGE):
        for n in sorted(names):
            fp = os.path.join(root, n)
            print("  %8d  %s" % (os.path.getsize(fp), os.path.relpath(fp, STAGE)))
