# Idea 6: a popular page (20 loads of one page in 5 minutes)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## popular: 20 loads of one page over 5 min

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| pull, no Echo | 0% ±0 | - | 0% | 1046 ±70 | 0.0 ±0.0 | 418 ±28 |
| baseline: pull + Echo | 1% ±2 | 0.0 ±0.0 | 1% | 1074 ±97 | 0.0 ±0.0 | 430 ±39 |
| page-addressed + coalescing | 72% ±15 | 19.5 ±18.5 | 55% | 933 ±155 | 0.0 ±0.0 | 373 ±62 |
| page-addressed + coding | 95% ±5 | 1.1 ±4.1 | 85% | 611 ±186 | 0.0 ±0.0 | 244 ±74 |
| push carousel every 60 s | 77% ±12 | 9.5 ±15.6 | 63% | 960 ±148 | 0.0 ±0.0 | 384 ±59 |
| page-addr + gradient + fast | 36% ±33 | 12.1 ±30.9 | 33% | 1284 ±384 | 0.0 ±0.0 | 514 ±154 |
