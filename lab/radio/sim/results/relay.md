# Idea 1/2: how relays repeat packets (LoRa-only suburb)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb (flood, node.py fixes) | 95% ±22 | 64.2 ±19.5 | 30% | 94 ±47 | 0.0 ±0.0 | 38 ±19 |
| Gorb v0.1 flood (before fixes) | 75% ±43 | 76.6 ±14.0 | 10% | 197 ±39 | 0.0 ±0.0 | 79 ±16 |
| RSSI timer (Meshtastic-like) | 95% ±22 | 60.8 ±17.7 | 25% | 79 ±34 | 0.0 ±0.0 | 31 ±13 |
| gradient (ETX, closest first) | 95% ±22 | 46.4 ±10.9 | 80% | 38 ±16 | 0.0 ±0.0 | 15 ±6 |
| route (unicast reverse path) | 95% ±22 | 42.6 ±19.5 | 65% | 34 ±26 | 0.0 ±0.0 | 14 ±10 |
| CT flood (Glossy-style) | 100% ±0 | 22.0 ±2.3 | 100% | 303 ±3 | 0.0 ±0.0 | 121 ±1 |
| CT flood, pipelined (3 slots) | 100% ±0 | 33.6 ±11.6 | 100% | 298 ±13 | 0.0 ±0.0 | 119 ±5 |
| route + host pacing | 90% ±30 | 41.9 ±20.8 | 60% | 38 ±28 | 0.0 ±0.0 | 15 ±11 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb (flood, node.py fixes) | 9% ±14 | 75.4 ±13.5 | 1% | 267 ±59 | 0.0 ±0.0 | 107 ±23 |
| Gorb v0.1 flood (before fixes) | 5% ±13 | 81.7 ±9.8 | 0% | 314 ±71 | 0.0 ±0.0 | 126 ±28 |
| RSSI timer (Meshtastic-like) | 5% ±13 | 76.4 ±18.6 | 1% | 273 ±54 | 0.0 ±0.0 | 109 ±21 |
| gradient (ETX, closest first) | 26% ±29 | 75.9 ±16.5 | 8% | 216 ±31 | 0.0 ±0.0 | 86 ±12 |
| route (unicast reverse path) | 19% ±19 | 64.2 ±20.5 | 8% | 186 ±39 | 0.0 ±0.0 | 74 ±16 |
| CT flood (Glossy-style) | 42% ±21 | 71.1 ±17.0 | 12% | 1009 ±120 | 0.0 ±0.0 | 404 ±48 |
| CT flood, pipelined (3 slots) | 34% ±23 | 63.2 ±14.1 | 12% | 975 ±95 | 0.0 ±0.0 | 390 ±38 |
| route + host pacing | 22% ±22 | 51.3 ±16.7 | 16% | 172 ±38 | 0.0 ±0.0 | 69 ±15 |

## suburb40k: 1 load of a 40 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb (flood, node.py fixes) | 95% ±22 | 337.4 ±110.6 | 0% | 584 ±264 | 0.0 ±0.0 | 233 ±106 |
| Gorb v0.1 flood (before fixes) | 90% ±30 | 430.1 ±65.3 | 0% | 1310 ±223 | 0.0 ±0.0 | 524 ±89 |
| RSSI timer (Meshtastic-like) | 95% ±22 | 308.7 ±97.1 | 0% | 501 ±215 | 0.0 ±0.0 | 200 ±86 |
| gradient (ETX, closest first) | 100% ±0 | 227.5 ±60.7 | 0% | 228 ±112 | 0.0 ±0.0 | 91 ±45 |
| route (unicast reverse path) | 95% ±22 | 209.0 ±102.7 | 5% | 211 ±153 | 0.0 ±0.0 | 84 ±61 |
| CT flood (Glossy-style) | 100% ±0 | 147.4 ±7.5 | 0% | 2011 ±32 | 0.0 ±0.0 | 804 ±13 |
| CT flood, pipelined (3 slots) | 100% ±0 | 145.4 ±19.9 | 0% | 1956 ±93 | 0.0 ±0.0 | 782 ±37 |
| route + host pacing | 100% ±0 | 211.2 ±111.9 | 5% | 246 ±170 | 0.0 ±0.0 | 99 ±68 |
