# Idea 1/2: how relays repeat packets (LoRa-only suburb)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline flood | 75% ±43 | 76.6 ±14.0 | 10% | 197 ±39 | 0.0 ±0.0 | 79 ±16 |
| RSSI timer (Meshtastic-like) | 80% ±40 | 67.1 ±7.7 | 5% | 158 ±35 | 0.0 ±0.0 | 63 ±14 |
| gradient (ETX, closest first) | 100% ±0 | 42.0 ±10.9 | 95% | 43 ±19 | 0.0 ±0.0 | 17 ±8 |
| route (unicast reverse path) | 85% ±36 | 45.3 ±19.1 | 55% | 40 ±30 | 0.0 ±0.0 | 16 ±12 |
| CT flood (Glossy-style) | 100% ±0 | 22.0 ±2.3 | 100% | 303 ±3 | 0.0 ±0.0 | 121 ±1 |
| CT flood, pipelined (3 slots) | 100% ±0 | 33.6 ±11.6 | 100% | 298 ±13 | 0.0 ±0.0 | 119 ±5 |
| route + host pacing | 90% ±30 | 40.8 ±20.0 | 65% | 36 ±26 | 0.0 ±0.0 | 14 ±10 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline flood | 5% ±13 | 81.7 ±9.8 | 0% | 314 ±71 | 0.0 ±0.0 | 126 ±28 |
| RSSI timer (Meshtastic-like) | 5% ±10 | 82.2 ±20.4 | 1% | 337 ±70 | 0.0 ±0.0 | 135 ±28 |
| gradient (ETX, closest first) | 18% ±20 | 65.0 ±15.5 | 6% | 227 ±35 | 0.0 ±0.0 | 91 ±14 |
| route (unicast reverse path) | 12% ±17 | 67.0 ±16.5 | 4% | 211 ±41 | 0.0 ±0.0 | 84 ±16 |
| CT flood (Glossy-style) | 42% ±21 | 71.1 ±17.0 | 12% | 1009 ±120 | 0.0 ±0.0 | 404 ±48 |
| CT flood, pipelined (3 slots) | 34% ±23 | 63.2 ±14.1 | 12% | 975 ±95 | 0.0 ±0.0 | 390 ±38 |
| route + host pacing | 22% ±25 | 52.9 ±22.6 | 18% | 205 ±56 | 0.0 ±0.0 | 82 ±23 |

## suburb40k: 1 load of a 40 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline flood | 90% ±30 | 430.1 ±65.3 | 0% | 1310 ±223 | 0.0 ±0.0 | 524 ±89 |
| RSSI timer (Meshtastic-like) | 95% ±22 | 378.8 ±48.8 | 0% | 1054 ±148 | 0.0 ±0.0 | 422 ±59 |
| gradient (ETX, closest first) | 100% ±0 | 209.0 ±57.3 | 0% | 296 ±138 | 0.0 ±0.0 | 118 ±55 |
| route (unicast reverse path) | 90% ±30 | 217.6 ±107.9 | 5% | 302 ±254 | 0.0 ±0.0 | 121 ±102 |
| CT flood (Glossy-style) | 100% ±0 | 147.4 ±7.5 | 0% | 2011 ±32 | 0.0 ±0.0 | 804 ±13 |
| CT flood, pipelined (3 slots) | 100% ±0 | 145.4 ±19.9 | 0% | 1956 ±93 | 0.0 ±0.0 | 782 ±37 |
| route + host pacing | 100% ±0 | 235.6 ±142.7 | 5% | 290 ±230 | 0.0 ±0.0 | 116 ±92 |
