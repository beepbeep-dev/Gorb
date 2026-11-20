# Idea 4: LoRa data rate (network-wide preset, and per-link adaptive)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| flood, SF8/500k | 9% ±14 | 75.4 ±13.5 | 1% | 267 ±59 | 0.0 ±0.0 | 107 ±23 |
| flood, SF6/500k | 36% ±30 | 54.4 ±10.9 | 22% | 171 ±29 | 0.0 ±0.0 | 68 ±12 |
| flood, SF7/500k | 16% ±23 | 63.6 ±20.2 | 8% | 208 ±45 | 0.0 ±0.0 | 83 ±18 |
| flood, SF9/500k | 6% ±13 | 116.1 ±31.5 | 0% | 363 ±103 | 0.0 ±0.0 | 145 ±41 |
| gradient, SF8/500k | 26% ±29 | 75.9 ±16.5 | 8% | 216 ±31 | 0.0 ±0.0 | 86 ±12 |
| gradient, SF6/500k | 79% ±24 | 37.2 ±7.4 | 79% | 62 ±19 | 0.0 ±0.0 | 25 ±7 |
| gradient, SF7/500k | 64% ±30 | 55.1 ±11.9 | 44% | 122 ±26 | 0.0 ±0.0 | 49 ±11 |
| gradient, SF9/500k | 11% ±20 | 121.7 ±23.8 | 0% | 316 ±62 | 0.0 ±0.0 | 126 ±25 |
| route, SF8 fixed | 19% ±19 | 64.2 ±20.5 | 8% | 186 ±39 | 0.0 ±0.0 | 74 ±16 |
| route, per-hop SF5-8 (multi-SF rx, upper bound) | 49% ±40 | 46.4 ±19.9 | 34% | 159 ±55 | 0.0 ±0.0 | 63 ±22 |
