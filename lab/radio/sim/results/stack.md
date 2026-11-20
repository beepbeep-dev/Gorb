# Best combination vs. today's Gorb

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 75% ±43 | 76.6 ±14.0 | 10% | 197 ±39 | 0.0 ±0.0 | 79 ±16 |
| Smart stack: route + coding + pacing | 100% ±0 | 39.0 ±21.8 | 80% | 35 ±27 | 0.0 ±0.0 | 14 ±11 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 5% ±13 | 81.7 ±9.8 | 0% | 314 ±71 | 0.0 ±0.0 | 126 ±28 |
| Smart stack: route + coding + pacing | 36% ±31 | 59.6 ±19.4 | 18% | 193 ±40 | 0.0 ±0.0 | 77 ±16 |
| Smart stack + page-addressed | 8% ±18 | 29.4 ±7.8 | 8% | 97 ±19 | 0.0 ±0.0 | 39 ±7 |
| Smart stack + 4 LoRa channels | 79% ±23 | 21.4 ±9.7 | 75% | 69 ±32 | 0.0 ±0.0 | 28 ±13 |
| Smart stack + SF6 | 79% ±23 | 30.5 ±6.6 | 79% | 71 ±22 | 0.0 ±0.0 | 28 ±9 |

## suburb40k: 1 load of a 40 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 90% ±30 | 430.1 ±65.3 | 0% | 1310 ±223 | 0.0 ±0.0 | 524 ±89 |
| Smart stack: route + coding + pacing | 100% ±0 | 184.5 ±105.0 | 15% | 257 ±214 | 0.0 ±0.0 | 103 ±86 |

## mixed: mixed town, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 23% ±24 | 43.1 ±29.2 | 13% | 151 ±49 | 4.3 ±1.3 | 64 ±20 |
| Smart stack: route + coding + pacing | 60% ±29 | 32.4 ±18.7 | 53% | 64 ±38 | 1.3 ±0.8 | 26 ±16 |

## popular: 20 loads of one page over 5 min

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb | 1% ±2 | 0.0 ±0.0 | 1% | 1074 ±97 | 0.0 ±0.0 | 430 ±39 |
| Smart stack: route + coding + pacing | 66% ±27 | 0.0 ±0.0 | 64% | 695 ±256 | 0.0 ±0.0 | 278 ±102 |
| Smart stack + page-addressed | 91% ±9 | 0.2 ±0.8 | 89% | 259 ±87 | 0.0 ±0.0 | 103 ±35 |
| Smart stack + SF6 | 93% ±12 | 0.0 ±0.0 | 93% | 134 ±61 | 0.0 ±0.0 | 53 ±24 |
