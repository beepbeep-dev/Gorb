# Ideas 7/8: repairing lost chunks (combining, coding, local repair, fast NACK)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline flood | 75% ±43 | 76.6 ±14.0 | 10% | 197 ±39 | 0.0 ±0.0 | 79 ±16 |
| + packet combining | 90% ±30 | 73.8 ±12.8 | 10% | 192 ±37 | 0.0 ±0.0 | 77 ±15 |
| + fountain coding 10% | 100% ±0 | 76.9 ±9.3 | 5% | 201 ±25 | 0.0 ±0.0 | 80 ±10 |
| + local repair | 80% ±40 | 75.0 ±12.5 | 5% | 205 ±55 | 0.0 ±0.0 | 82 ±22 |
| + fast repair | 40% ±49 | 87.0 ±16.8 | 5% | 363 ±80 | 0.0 ±0.0 | 145 ±32 |
| gradient + fast repair | 95% ±22 | 59.2 ±21.7 | 45% | 139 ±80 | 0.0 ±0.0 | 56 ±32 |
| gradient + fast + coding | 100% ±0 | 41.2 ±17.1 | 90% | 83 ±49 | 0.0 ±0.0 | 33 ±19 |
| gradient + fast + combining | 90% ±30 | 62.3 ±28.6 | 40% | 160 ±100 | 0.0 ±0.0 | 64 ±40 |
| route + coding | 95% ±22 | 41.7 ±20.7 | 75% | 42 ±30 | 0.0 ±0.0 | 17 ±12 |
| route + coding + pacing | 100% ±0 | 39.0 ±21.8 | 80% | 35 ±27 | 0.0 ±0.0 | 14 ±11 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline flood | 5% ±13 | 81.7 ±9.8 | 0% | 314 ±71 | 0.0 ±0.0 | 126 ±28 |
| + packet combining | 2% ±8 | 86.1 ±25.0 | 0% | 325 ±66 | 0.0 ±0.0 | 130 ±26 |
| + fountain coding 10% | 10% ±17 | 85.4 ±16.1 | 2% | 336 ±79 | 0.0 ±0.0 | 135 ±31 |
| + local repair | 2% ±8 | 63.0 ±8.2 | 1% | 339 ±72 | 0.0 ±0.0 | 136 ±29 |
| + fast repair | 8% ±11 | 93.7 ±17.4 | 0% | 619 ±40 | 0.0 ±0.0 | 248 ±16 |
| gradient + fast repair | 9% ±12 | 80.5 ±14.9 | 0% | 531 ±36 | 0.0 ±0.0 | 213 ±15 |
| gradient + fast + coding | 14% ±20 | 75.9 ±15.0 | 4% | 501 ±40 | 0.0 ±0.0 | 200 ±16 |
| gradient + fast + combining | 8% ±11 | 89.0 ±13.8 | 0% | 529 ±43 | 0.0 ±0.0 | 212 ±17 |
| route + coding | 25% ±24 | 65.5 ±18.8 | 11% | 211 ±29 | 0.0 ±0.0 | 84 ±12 |
| route + coding + pacing | 36% ±31 | 59.6 ±19.4 | 18% | 193 ±40 | 0.0 ±0.0 | 77 ±16 |
