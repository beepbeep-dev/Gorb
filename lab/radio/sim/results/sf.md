# Idea 4: LoRa data rate (network-wide preset, and per-link adaptive)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| flood, SF6/500k | 26% ±26 | 47.9 ±10.1 | 25% | 219 ±38 | 0.0 ±0.0 | 88 ±15 |
| flood, SF7/500k | 11% ±18 | 65.6 ±10.4 | 4% | 271 ±51 | 0.0 ±0.0 | 108 ±20 |
| flood, SF8/500k | 5% ±13 | 81.7 ±9.8 | 0% | 314 ±71 | 0.0 ±0.0 | 126 ±28 |
| flood, SF9/500k | 0% ±0 | - | 0% | 436 ±104 | 0.0 ±0.0 | 174 ±42 |
| gradient+fast, SF6/500k | 62% ±39 | 60.9 ±27.6 | 30% | 366 ±119 | 0.0 ±0.0 | 147 ±47 |
| gradient+fast, SF7/500k | 19% ±17 | 65.3 ±28.1 | 6% | 508 ±46 | 0.0 ±0.0 | 203 ±18 |
| gradient+fast, SF8/500k | 9% ±12 | 80.5 ±14.9 | 0% | 531 ±36 | 0.0 ±0.0 | 213 ±15 |
| gradient+fast, SF9/500k | 2% ±8 | 115.0 ±4.8 | 0% | 525 ±52 | 0.0 ±0.0 | 210 ±21 |
| route+fast, SF8 fixed | 9% ±12 | 67.9 ±24.0 | 1% | 492 ±51 | 0.0 ±0.0 | 197 ±20 |
| route+fast, per-hop SF5-8 (multi-SF rx, upper bound) | 24% ±20 | 35.9 ±23.8 | 16% | 491 ±48 | 0.0 ±0.0 | 196 ±19 |
