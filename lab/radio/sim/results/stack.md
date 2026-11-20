# Best combination vs. today's Gorb

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 95% ±22 | 64.2 ±19.5 | 30% | 94 ±47 | 0.0 ±0.0 | 38 ±19 |
| Smart stack: route + coding + pacing | 100% ±0 | 40.0 ±22.3 | 75% | 38 ±31 | 0.0 ±0.0 | 15 ±12 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 9% ±14 | 75.4 ±13.5 | 1% | 267 ±59 | 0.0 ±0.0 | 107 ±23 |
| Smart stack: route + coding + pacing | 38% ±30 | 65.4 ±20.5 | 19% | 207 ±43 | 0.0 ±0.0 | 83 ±17 |
| Smart stack + page-addressed | 8% ±18 | 29.4 ±7.8 | 8% | 97 ±19 | 0.0 ±0.0 | 39 ±7 |
| Smart stack + 4 LoRa channels | 79% ±23 | 21.1 ±9.7 | 74% | 69 ±32 | 0.0 ±0.0 | 27 ±13 |
| Smart stack + SF6 | 75% ±25 | 31.5 ±7.5 | 75% | 70 ±22 | 0.0 ±0.0 | 28 ±9 |

## suburb40k: 1 load of a 40 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 95% ±22 | 337.4 ±110.6 | 0% | 584 ±264 | 0.0 ±0.0 | 233 ±106 |
| Smart stack: route + coding + pacing | 100% ±0 | 193.1 ±113.2 | 15% | 280 ±230 | 0.0 ±0.0 | 112 ±92 |

## mixed: mixed town, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 28% ±30 | 37.1 ±25.9 | 22% | 126 ±44 | 3.6 ±1.4 | 53 ±18 |
| Smart stack: route + coding + pacing | 63% ±30 | 33.5 ±20.6 | 53% | 64 ±39 | 1.3 ±0.9 | 26 ±16 |

## popular: 20 loads of one page over 5 min

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 3% ±4 | 6.4 ±7.9 | 3% | 776 ±153 | 0.0 ±0.0 | 311 ±61 |
| Smart stack: route + coding + pacing | 65% ±27 | 0.3 ±1.3 | 63% | 683 ±246 | 0.0 ±0.0 | 273 ±98 |
| Smart stack + page-addressed | 91% ±9 | 0.4 ±1.7 | 89% | 259 ±87 | 0.0 ±0.0 | 103 ±35 |
| Smart stack + SF6 | 95% ±8 | 0.0 ±0.0 | 95% | 123 ±46 | 0.0 ±0.0 | 49 ±19 |
