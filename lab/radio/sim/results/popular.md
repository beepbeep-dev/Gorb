# Idea 6: a popular page (20 loads of one page in 5 minutes)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## popular: 20 loads of one page over 5 min

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb: pull + Echo | 3% ±4 | 6.4 ±7.9 | 3% | 776 ±153 | 0.0 ±0.0 | 311 ±61 |
| pull, no Echo | 1% ±2 | 12.5 ±5.4 | 1% | 706 ±87 | 0.0 ±0.0 | 282 ±35 |
| page-addressed + coalescing | 86% ±10 | 20.7 ±22.8 | 64% | 773 ±189 | 0.0 ±0.0 | 309 ±76 |
| page-addressed + coding | 98% ±4 | 0.0 ±0.0 | 91% | 464 ±205 | 0.0 ±0.0 | 186 ±82 |
| push carousel every 60 s | 91% ±16 | 9.8 ±16.2 | 74% | 645 ±124 | 0.0 ±0.0 | 258 ±50 |
| page-addressed + coding + route | 91% ±9 | 0.0 ±0.0 | 89% | 237 ±80 | 0.0 ±0.0 | 95 ±32 |
