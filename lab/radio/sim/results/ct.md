# Idea 2: how sensitive concurrent-transmission flooding is to timing

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| CT, 30 us jitter | 100% ±0 | 22.0 ±2.3 | 100% | 303 ±3 | 0.0 ±0.0 | 121 ±1 |
| CT, 150 us jitter | 100% ±0 | 24.7 ±7.8 | 100% | 301 ±7 | 0.0 ±0.0 | 120 ±3 |
| CT, 1 ms jitter (> 1 symbol) | 55% ±50 | 59.6 ±16.1 | 30% | 288 ±127 | 0.0 ±0.0 | 115 ±51 |
| CT, 30 us, 30% beating loss | 100% ±0 | 28.7 ±6.3 | 100% | 292 ±14 | 0.0 ±0.0 | 117 ±6 |
| CT, tolerance 0.25 symbol | 95% ±22 | 22.5 ±3.7 | 95% | 303 ±7 | 0.0 ±0.0 | 121 ±3 |
