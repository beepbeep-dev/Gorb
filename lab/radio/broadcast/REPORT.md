# Broadcast lab: does coding make Gorb broadcasting cheaper?

**Short answer: yes, by a lot, when several people want the same page. It barely matters for one person on a good link.**

All numbers come from the last full run of `broadcast_lab.py` (154 s).
* Every table is in [`results.md`](results.md).
* The raw numbers are in `results.json`.
* Charts are `1-…5-*.png`.

```
python lab/radio/broadcast/gorbcode.py               # codec self-test
python lab/radio/broadcast/broadcast_lab.py --quick  # ~45 s
python lab/radio/broadcast/broadcast_lab.py          # ~2.5 min
```

There are two separate wins:

1. **One shared stream per page instead of one copy per person.** This is most of the saving, and it doesn't need coding.
2. **Coding (RLNC) on top of the shared stream.**
   * Each listener only says "send me *n* more".
   * One repair packet fills a different hole for each listener.
   * This is what keeps the shared stream working with 20–100 listeners, a mix of near and far stations, and 30% loss. Without it, the plain "list of missing pieces" approach falls apart.

## 1. The codec (`gorbcode.py`)

The code is **systematic RLNC over GF(256)**:
* The first K packets are the plain chunks, sent as normal DATA, exactly as today.
* After that come repair packets. Each one is a mix of all K chunks.
* The mixing coefficients are worked out from `(seq, K)` on both sides, so a repair packet only adds a **4-byte header** (216-byte frame, +1.7% airtime).
* The decoder accepts packets in any order. It subtracts the chunks it already has and solves a small m×m system for the m missing ones.

| K | RLNC: share of decodes needing any extra packet | LT fountain: mean extra packets |
|---|---|---|
| 35 (6 KB) | 0.2% | 47% |
| 228 (40 KB) | 0.3% | 26% |
| 400 | 1.7% | 22% |

* **RLNC needs about K packets**, essentially perfect.
* **LT/fountain codes are a poor fit** at Gorb's page sizes.
* **Reed-Solomon is out:** it has a 255-packet limit, which a 40 KB page plus repairs exceeds.

**ESP32 cost** (estimated from operation counts, not measured on a board):
* Decoding a 40 KB page at 30% loss takes about 3 M GF multiply-adds, **about 0.15 s**, and about 17 KB of extra RAM.
* Encoding one repair packet takes **about 2 ms**.
* So it's feasible.

## 2. Main result: 20 listeners, US915, 15% loss

**40 KB page:**

| listeners | Gorb today | ARQ, bugs fixed | shared stream + RESEND lists | **shared stream + RLNC** |
|---|---|---|---|---|
| 1 | 40 s air, 100% | (same) | — | 40 s, 100% |
| 5 | **0% got it** | 201 s, 100% | 60 s, 100% | **43 s, 100%, median wait 51 s** |
| 20 | **0%** | 13 min, 100%, median 20 min | 166 s, 96% | **44 s, 100%, median 52 s** |
| 100 | **0%** | 67 min | 336 s, **4%** | **53 s, 100%** |

**6 KB page:** today only **47%** of 5 listeners got it, and **0%** of 20. With RLNC, 100% got it with 9–13 s of airtime.

**Control packets** (20 listeners, 40 KB): ARQ needs 115 and RESEND lists 108; RLNC needs **21**.

**Loss rate** (20 listeners, 40 KB):

| loss | RESEND lists | RLNC |
|---|---|---|
| 5% | 66 s, 100% | 39 s, 100% |
| 15% | 166 s, 96% | 44 s, 100% |
| 30% | 281 s, **1%** | 62 s, 100% |

**Mixed near and far listeners, and bursty loss:** RLNC stays at 100% delivered. With mixed near/far listeners, RESEND lists drop to 50%.

## 3. Where coding does NOT help

* **One listener on a decent link:** the same airtime. It's a bit faster, and at 30% loss more reliable: 100% delivered versus 88%.
* **Listeners who show up at different times:** no gain from coding alone (14 min versus 13 min). It does help when stations **keep partial overheard pages**. "RLNC + Echo, keep partial" costs 429 s instead of 13 min for 40 KB, and 8 of 20 listeners had the page instantly.
* **Relays and duty cycle:** these need pacing and longer timeouts. Coding only makes it more robust.

## 4. Gorb Cast (a looping broadcast for receive-only listeners)

How long a listener who tunes in has to wait, measured in page-lengths of airtime:

| | plain loop | **coded loop** |
|---|---|---|
| 40 KB, 15% loss | 3.28 | **1.18 (2.8× shorter)** |
| 40 KB, 30% loss | 5.04 | **1.43** |

Alternating one plain pass with one coded pass is just as fast, and old receivers still work with it.

## 5. Broken today (independent of coding)

1. **Repeated GETs re-queue the whole page.**
   * A requester only counts packets addressed to *itself* as activity. While the host is busy with someone else, it times out and asks again.
   * Each new GET queues another full copy.
   * Result: with several simultaneous listeners almost nobody gets the page, and queued copies keep going out for minutes to hours.
2. **Nothing cancels queued chunks** for requesters who gave up.
3. **The 120 s total timeout** makes a 40 KB page impossible in EU868 and through a paced relay. Time out on *silence* instead.
4. **Relays overflow their 12-slot queue** when the host sends back-to-back. Even one listener behind one relay got a 40 KB page 0% of the time. The fix is for the host to pace when `hops > 0` (0.5 s was simulated).
5. **Echo needs a perfect overheard copy**, which almost never happens under loss for big pages. Echo answering can also collide.

Small fixes for 1–4 (the "ARQ, bugs fixed" column):
* Don't queue a chunk that's already queued.
* Count "the host is busy" as activity.
* Time out on silence.
* Pace after a relayed request.

## 6. Proposed protocol (backward compatible)

**New packet types:**

| type | value | what it does |
|---|---|---|
| CODED | 7 | Repair packet. `seq` ≥ K is the symbol id and also the seed. Payload: 4-byte code header + 180 B. |
| NEED | 8 | "Send this many more" + "symbols I hold" (4 bytes). |
| OFFER | 9 | "This page is on air as session X". |
| BEACON | 1 | Gains `"fec": 1`. |

* The host runs **one session per page**. Further GETs only get an OFFER.
* NEED is suppressed if someone already asked for at least as many.
* **Keep version=1.** Existing stations reject other versions and don't relay them.
* **Compatibility catch:** today's PC nodes (`Packet.decode`) drop unknown packet types and don't relay them; the firmware does relay them. Add types 7–9 to `PacketType` one release *before* using them.

## 7. Limits

* This is a simulation, not over-the-air measurement. Losses are independent, and there's no capture effect.
* Trial counts are modest (8 runs for 20 listeners, 3 for 100), so treat differences under about 15% as noise.
* The ESP32 CPU figures are estimates.
* The collapse with several simultaneous requesters was modelled from the code and should be confirmed on real radios.
