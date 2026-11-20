# Ideas 7/8: repairing lost chunks (combining, coding, local repair, fast NACK)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## suburb1: 1 load of a 6 KB page

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb (flood) | 95% ±22 | 64.2 ±19.5 | 30% | 94 ±47 | 0.0 ±0.0 | 38 ±19 |
| + packet combining | 100% ±0 | 63.6 ±16.4 | 30% | 85 ±35 | 0.0 ±0.0 | 34 ±14 |
| + fountain coding 10% | 100% ±0 | 57.2 ±18.2 | 45% | 83 ±39 | 0.0 ±0.0 | 33 ±15 |
| + local repair | 90% ±30 | 59.8 ±12.5 | 35% | 107 ±40 | 0.0 ±0.0 | 43 ±16 |
| + fast repair | 100% ±0 | 135.5 ±60.7 | 10% | 358 ±178 | 0.0 ±0.0 | 143 ±71 |
| gradient + fast repair | 100% ±0 | 73.4 ±39.2 | 45% | 130 ±91 | 0.0 ±0.0 | 52 ±36 |
| gradient + fast + coding | 100% ±0 | 44.8 ±23.8 | 80% | 61 ±40 | 0.0 ±0.0 | 24 ±16 |
| gradient + fast + combining | 100% ±0 | 74.8 ±40.4 | 40% | 136 ±97 | 0.0 ±0.0 | 54 ±39 |
| route + coding | 100% ±0 | 40.9 ±21.7 | 75% | 34 ±26 | 0.0 ±0.0 | 14 ±10 |
| route + coding + pacing | 100% ±0 | 40.0 ±22.3 | 75% | 38 ±31 | 0.0 ±0.0 | 15 ±12 |

## suburb4: 4 loads of 6 KB pages within 20 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| today's Gorb (flood) | 9% ±14 | 75.4 ±13.5 | 1% | 267 ±59 | 0.0 ±0.0 | 107 ±23 |
| + packet combining | 5% ±17 | 68.6 ±6.1 | 1% | 255 ±42 | 0.0 ±0.0 | 102 ±17 |
| + fountain coding 10% | 11% ±17 | 70.1 ±16.1 | 4% | 259 ±54 | 0.0 ±0.0 | 103 ±22 |
| + local repair | 4% ±12 | 75.2 ±4.8 | 0% | 298 ±56 | 0.0 ±0.0 | 119 ±23 |
| + fast repair | 48% ±41 | 499.9 ±158.8 | 1% | 3123 ±664 | 0.0 ±0.0 | 1249 ±266 |
| gradient + fast repair | 40% ±33 | 384.3 ±171.2 | 0% | 2803 ±682 | 0.0 ±0.0 | 1121 ±273 |
| gradient + fast + coding | 48% ±35 | 292.5 ±169.2 | 5% | 2581 ±533 | 0.0 ±0.0 | 1032 ±213 |
| gradient + fast + combining | 40% ±35 | 407.2 ±187.1 | 0% | 2863 ±654 | 0.0 ±0.0 | 1145 ±261 |
| route + coding | 38% ±27 | 74.9 ±17.5 | 14% | 203 ±54 | 0.0 ±0.0 | 81 ±22 |
| route + coding + pacing | 38% ±30 | 65.4 ±20.5 | 19% | 207 ±43 | 0.0 ±0.0 | 83 ±17 |
