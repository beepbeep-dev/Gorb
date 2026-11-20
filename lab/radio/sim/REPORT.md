# Gorb virtual radio lab: report

The lab's "inventor" wrote this. Every number comes from `python lab/radio/sim/radio_lab.py`: 20 random neighbourhoods ("seeds") per row, about 9 minutes on 4 cores. The full tables and charts are in [`results/`](results/) (`*.md`, `*.png`, `results.json`).

The baseline, "today's Gorb", is `gorb/node.py` after the crowd fixes:
* replies travel as many hops as the request (TTL = hop count),
* chunks still waiting to go out are not queued again,
* a requester can finish from an Echo copy and send "stop",
* replies through relays are paced.

Gorb v0.1 is shown alongside for reference.

```
python lab/radio/sim/radio_lab.py --check              # self-checks (~10 s)
python lab/radio/sim/radio_lab.py --quick              # all experiments, 3 seeds (~1.5 min)
python lab/radio/sim/radio_lab.py                      # all experiments, 20 seeds (~9 min)
python lab/radio/sim/radio_lab.py --only esp32 --seeds 8
```

## 0. Short version

| idea | verdict | in one sentence |
|---|---|---|
| Smarter relaying: gradient / reverse-path route with ETX-like cost | **Big win** | 2.5× less LoRa airtime than today (8× less than v0.1) and about 1.5× faster loads |
| Several LoRa channels, one per host "cell" | **Big win when busy** | 8 loads at once: delivered goes from 2% to 56–65% |
| Faster network-wide preset (SF6/SF7 at 500 kHz) | **Big win when busy**, costs range | With smart relays: 79% delivered vs 26% at SF8 |
| Shared page-addressed stream + fountain coding for popular pages | **Big win for popular pages** | 98% delivered vs 3% today, mostly instantly |
| Plain ESP32: ESP-NOW v2 big frames + routed unicast with ACK | **Big win, firmware only** | A 6 KB page goes from 19.9 s to 4.1 s |
| Multi-radio: best radio per hop instead of flooding both | **Medium win** | Mixed town: 58% delivered vs 28%, half the airtime |
| Glossy-style concurrent flooding | **Interesting, not now** | Fastest single load (22 s vs 64 s), but 3× the airtime and needs ~150 µs timing |
| Fountain coding on a normal request | **Small win** | A few seconds faster; essential for popular pages |
| Packet combining at the receiver | **No win** | Most losses are collisions, not weak signals |
| Local repair by neighbours | **No win** | The repairs collide with the host's own resends |
| "Fast repair" (ask again after 1.5 s) | **Harmful** | Retry storms under load (up to 3000 s of airtime) |
| Wi-Fi LR mode for dense groups | **No speed win** | About 1.6× the range, but each frame takes 4–8× longer |

**Not new research:** none of the winners is new to radio research (prior work is listed below). What's new is the evidence that **Gorb's real bottleneck is relaying and congestion, not the radio itself.**

## 1. How the lab works

It's a **discrete-event simulator** (`engine.py`): a time-ordered list of transmissions, used to work out who heard what.

* **Map (`world.py`):**
  * Layouts: suburb, city clusters, line or grid.
  * 70% of stations are indoors. Walls cost 10 dB at 915 MHz and 13 dB at 2.4 GHz (ITU-R P.2109).
  * Path loss is log-distance with exponent 3.3, plus ±7 dB of shadowing per link (70% correlated between LoRa and Wi-Fi) and Rician fading per frame.
* **Radios (`phy.py`):**

| mode | sensitivity | rate | airtime per chunk | median range (one end indoors) |
|---|---|---|---|---|
| LoRa SF8/500k (US915 today) | −120 dBm | 12.5 kbit/s | 151 ms | 1262 m |
| LoRa SF7/500k | −117.5 | 21.9 | 85 ms | 1060 m |
| LoRa SF5/500k (SX126x) | −112.5 | 62.5 | 29 ms | 748 m |
| Wi-Fi LR 250k | −104 | 250 | 8.6 ms | 140 m |
| ESP-NOW 1 Mbps | −97 | 1000 | 2.3 ms | 86 m |

* Where the numbers come from:
  * LoRa sensitivity: SX1276/SX1261 datasheets (within about 1 dB of the published tables).
  * Airtime: the Semtech formula.
  * Wi-Fi: the ESP32 datasheet, and Espressif forum posts for −97/−105 dBm.
  * **The LR-mode numbers are the least certain in the lab.**
* **Reception:**
  * Radios are half duplex (they can't hear while sending).
  * Capture effect: a frame survives if it's 6 dB stronger than the others on LoRa, 10 dB on Wi-Fi (Bor et al. 2016). LoRa spreading factors aren't perfectly separate (Croce et al. 2018).
  * Background interference: busy home Wi-Fi at 2.4 GHz, other LoRa users at 915 MHz.
* **Channel access:** listen before talk (CAD / CCA), the duty-cycle token bucket as in the firmware, and on Wi-Fi unicast an ACK with up to 7 retries.
* **Protocol (`proto.py`):** a simplified Gorb. Each idea is a switch in `Cfg`.
* **Self-checks (`results/checks.md`):**
  * LoRa airtimes match the Semtech calculator exactly.
  * Pure ALOHA gives 0.186 throughput against the theoretical 0.184.

**Limits:**
* It's a model, so trust it for *A vs B* comparisons, not exact seconds.
* Walls and shadowing are deliberately harsh, which is why success rates are low in the busy LoRa scenarios.
* Not modelled: real antennas, frequency offsets, driver rate control, beacons, or WAIT and media layers.
* The "route" and "gradient" relaying are the lab's own designs.

## 2. Scenarios

| name | what | stations / area | radios |
|---|---|---|---|
| suburb1 | 1 load of a 6 KB page, 1–3 hops | 60 / 3×3 km | LoRa |
| suburb4 | 4 different pages loaded within 20 s | 60 / 3×3 km | LoRa |
| suburb40k | 1 load of a 40 KB page | 60 / 3×3 km | LoRa |
| busy8 | 8 hosts, 8 loads within 10 s | 80 / 3×3 km | LoRa |
| popular | 20 loads of the same page over 5 minutes | 60 / 3×3 km | LoRa |
| mixed | 30% LoRa boards, 70% plain ESP32 | 80 / 2×2 km | LoRa + ESP-NOW |
| esp32 | plain ESP32s only (the user's case) | 50 / 0.7×0.7 km | ESP-NOW |

## 3. Results

### Smarter relaying (suburb1, 6 KB)

| variant | delivered | load time | within 60 s | LoRa airtime |
|---|---|---|---|---|
| today's Gorb | 95% | 64 s | 30% | 94 s |
| Gorb v0.1 | 75% | 77 s | 10% | 197 s |
| RSSI timer (Meshtastic-like) | 95% | 61 s | 25% | 79 s |
| gradient | 95% | 46 s | 80% | 38 s |
| route | 95% | 43 s | 65% | 34 s |

* **40 KB page:** today 337 s load and 584 s airtime; gradient 228 s and 228 s, delivered 100%.
* Today's flooding is still a "broadcast storm" (Ni et al. 1999): many stations repeat every chunk.
* The crowd fixes already halved airtime compared with v0.1 (197 → 94 s). Targeted relaying halves it again.

### Concurrent flooding (Glossy)

* Fastest and most predictable single load: 22 s every time.
* But it uses 303 s of airtime against 94 s today, and with 1 ms of timing jitter it falls to 55% delivered.
* **Not now.** Bench-test relay timing on real boards first.

### Multi-radio (mixed town)

| variant | delivered | LoRa airtime |
|---|---|---|
| today: flood on every radio | 28% | 126 s |
| LoRa only | 27% | 56 s |
| **route, best radio per hop** | **58%** | 64 s |

Plain ESP32s more than 100 m apart are islands: ESP-NOW reaches about 35–90 m through walls.

### LoRa presets (suburb4, 4 loads at once)

| variant | delivered | airtime |
|---|---|---|
| flood SF8 (today) | 9% | 267 s |
| flood SF6 | 36% | 171 s |
| gradient SF7 | 64% | 122 s |
| **gradient SF6** | **79%** | 62 s |

SF5 and SF6 need SX126x chips (the Heltec V3 has one). Range at SF6 is about 3/4 of SF8.

### Several channels (busy8)

* Flood on 1 channel: 2% delivered. Flood on 8 channels: 59%. Gradient on 4 channels: 65%.
* Each host gets a channel, announced in its beacon. This is mainly for the US and AU: the EU has one useful sub-band.

### Popular page (20 people, same page)

| variant | delivered | airtime |
|---|---|---|
| today: pull + Echo | **3%** | 776 s |
| page-addressed + coalescing | 86% | 773 s |
| **page-addressed + fountain coding** | **98%**, mostly instant | 464 s |
| page-addressed + coding + route | 91% | 237 s |

**Why today fails:**
* Each request queues its own copy, and the queue grows faster than it drains.
* Copies for people who gave up are still sent.
* Overhearers catch only 25–33 of 35 chunks, so **Echo rarely completes**.

The broadcast lab found the same independently.

**Caveat:** the lab's page-addressing broke loads of *different* pages (8% in suburb4). Use it only for popular pages until that's fixed.

### Your case: plain ESP32s (esp32 scenario)

| variant | delivered | load time | Wi-Fi airtime |
|---|---|---|---|
| today: ESP-NOW flood, 180-byte chunks | 93% | 19.9 s | 4.2 s |
| Wi-Fi LR 500k flood | 98% | 16.9 s | 7.2 s |
| **ESP-NOW v2 1400-byte chunks** | 87% | **8.1 s** | 3.3 s |
| **route + per-hop rate + 1400-byte chunks** | **98%** | **4.1 s** | 1.5 s |

* On ESP-NOW, per-frame overheads matter more than airtime, so **fewer, bigger frames win**.
* LR mode is about **reach**, not speed.
* node.py's old 0.3 s minimum pacing added about 10 s per page on ESP-NOW. **Fixed:** pacing now scales with airtime.

### Best combination vs today

"Smart stack" = route relaying + fountain coding + pacing.

| scenario | today | smart stack | + 4 LoRa channels |
|---|---|---|---|
| suburb1, 6 KB | 95%, 64 s, 94 s air | **100%, 40 s, 38 s air** | – |
| suburb4, 4 loads | 9%, 75 s, 267 s air | 38%, 65 s, 207 s air | **79%, 21 s, 69 s air** |
| suburb40k | 95%, 337 s, 584 s air | **100%, 193 s, 280 s air** | – |
| mixed town | 28%, 126 s air | **63%, 64 s air** | – |
| popular page | 3% | 65% | + coding + page-addressed + route: **95%**, 123 s air |

## 4. What to build first

1. **Targeted relaying (gradient/route with ETX-like cost).**
   * Firmware and node.py keep a per-request table of cost and previous hop, built from GET copies and their signal strength.
   * Only stations that are closer to the destination repeat a packet, closest first.
   * If a round fails, fall back to flooding.
   * Needs 1–2 extra header bytes. Works on LoRa and ESP-NOW, including a plain ESP32.
2. **Plain ESP32:**
   * ESP-NOW v2 1400-byte frames (ESP-IDF ≥ 5.4 / Arduino-ESP32 ≥ 3.2), falling back to 250 bytes for older boards.
   * Unicast to the next hop with ACK.
   * A data rate chosen per peer.
   * LR mode only for long single links.
3. **Popular pages:**
   * address DATA to the page instead of the person;
   * fountain coding, reusing `lab/radio/broadcast/gorbcode.py`;
   * hosts drop queued chunks for requests that were superseded or timed out.
4. **Busy US/AU networks:** a faster "dense" preset (SF7/500k on SX127x, SF6/500k on SX126x) and/or per-host channels.
5. **Later or never:** concurrent flooding, packet combining, local repair, fast repair.

## Sources

**Radio chips and Espressif docs:**
* Semtech SX1276/77/78/79 datasheet rev. 7; SX1261/2 datasheet; AN1200.13.
* Espressif ESP32 datasheet; ESP-IDF Wi-Fi guide (LR mode) and ESP-NOW docs.
* https://esp32.com/viewtopic.php?p=14475
* https://github.com/ldab/esp-gateway-long-range

**Radio behaviour:**
* Bor et al., MSWiM 2016 (LoRa capture).
* Croce et al., IEEE Comm. Letters 2018 (imperfect SF orthogonality).
* Ganu 2005 and Lee 2007 (802.11 capture).

**Flooding and routing:**
* Ferrari et al., Glossy, IPSN 2011.
* Liao et al., IEEE Access 2017 (concurrent LoRa).
* Biswas & Morris, ExOR, SIGCOMM 2005.
* De Couto et al., ETX, MobiCom 2003.
* AODV, RFC 3561.
* Ye et al., GRAB, 2005.
* Ni et al., MobiCom 1999 (broadcast storm).
* Draves et al., MobiCom 2004 (multi-radio routing).

**Coding and content addressing:**
* Chachulski et al., MORE, SIGCOMM 2007.
* Jacobson et al., CoNEXT 2009 (named content).
* RFC 6726 (FLUTE).

**Propagation and LoRa networks:**
* ITU-R P.2109.
* Rappaport, *Wireless Communications*.
* LoRaWAN Regional Parameters / ADR.
* Meshtastic documentation.
