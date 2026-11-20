# Idea 3: LoRa + Wi-Fi together (mixed town, 30% LoRa boards)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## mixed: mixed town, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline: flood on every radio (firmware) | 28% ±30 | 37.1 ±25.9 | 22% | 126 ±44 | 3.6 ±1.4 | 53 ±18 |
| LoRa only (Wi-Fi off) | 27% ±23 | 45.1 ±22.5 | 17% | 56 ±56 | 0.0 ±0.0 | 22 ±23 |
| ESP-NOW only (LoRa off) | 10% ±19 | 14.2 ±12.7 | 10% | 0 ±0 | 0.3 ±0.3 | 0 ±0 |
| flood, Wi-Fi in LR 250k | 37% ±31 | 38.5 ±20.6 | 25% | 138 ±48 | 21.0 ±8.3 | 72 ±25 |
| route, best radio per hop | 58% ±36 | 35.1 ±21.5 | 48% | 64 ±40 | 1.2 ±0.8 | 27 ±17 |
| route, best radio + Wi-Fi rate per hop | 57% ±33 | 44.1 ±24.2 | 45% | 59 ±33 | 1.0 ±0.7 | 24 ±14 |
| gradient (both radios) | 53% ±39 | 43.9 ±23.9 | 38% | 78 ±34 | 1.8 ±0.7 | 33 ±14 |
