## Part 1: codec

| K | RLNC extra pkts, mean (10% loss) | RLNC P(extra>0) | RLNC extra, mean (30% loss) | LT extra, mean | LT extra, p95 |
|---|---|---|---|---|---|
| 10 | 0.001 | 0.002 | 0.003 | 5.5 (55%) | 14 |
| 20 | 0.002 | 0.003 | 0.003 | 9.8 (49%) | 21 |
| 35 | 0.003 | 0.002 | 0.001 | 16.4 (47%) | 33 |
| 50 | 0.003 | 0.002 | 0.001 | 20.4 (41%) | 42 |
| 100 | 0.007 | 0.005 | 0.003 | 32.5 (32%) | 57 |
| 228 | 0.000 | 0.003 | 0.005 | 59.2 (26%) | 89 |
| 400 | 0.033 | 0.017 | 0.000 | 88.2 (22%) | 115 |

| K | chunks missing (30% loss) | Python encode / repair pkt | Python decode | GF mul-adds to decode | extra RAM for decode |
|---|---|---|---|---|---|
| 35 | 11 | 0.05 ms | 5 ms | 0.07 M | 2.4 KB |
| 228 | 67 | 0.24 ms | 41 ms | 3.03 M | 16.5 KB |
| 400 | 116 | 0.43 ms | 91 ms | 9.86 M | 33.9 KB |

## Part 2: delivery (channel time = airtime of every packet anyone sent until all listeners had the page or gave up)


### N=1 loss=5% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 5 s | 100% | 12 s | 17 s | 2 | 0 | 40 |
| Shared stream + RLNC (NEED) | 6 s | 100% | 12 s | 13 s | 3 | 0 | 40 |

### N=5 loss=5% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 109 s (+22 s still queued) | 83% | 49 s | never | 51 | 29 | 15 |
| Gorb today + Echo | 59 s (+21 s still queued) | 100% | 53 s | 59 s | 31 | 48 | 15 |
| ARQ, fixed (1 session/listener) | 27 s | 100% | 36 s | 42 s | 11 | 2 | 15 |
| Shared stream + RESEND lists | 8 s | 100% | 13 s | 20 s | 9 | 1 | 15 |
| Shared stream + RLNC (NEED) | 8 s (+1 s still queued) | 100% | 12 s | 13 s | 5 | 1 | 15 |

### N=20 loss=5% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 116 s (+23 min still queued) | 0% | never | never | 420 | 156 | 8 |
| Gorb today + Echo | 175 s (+19 min still queued) | 9% | never | never | 398 | 723 | 8 |
| ARQ, fixed (1 session/listener) | 111 s | 100% | 126 s | 149 s | 62 | 22 | 8 |
| Shared stream + RESEND lists | 12 s | 100% | 16 s | 24 s | 32 | 10 | 8 |
| Shared stream + RLNC (NEED) | 8 s | 100% | 12 s | 13 s | 17 | 5 | 8 |

### N=100 loss=5% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 152 s (+68 min still queued) | 0% | never | never | 2100 | 1297 | 3 |
| Gorb today + Echo | 418 s (+40 min still queued) | 9% | never | never | 1859 | 3519 | 3 |
| ARQ, fixed (1 session/listener) | 557 s | 100% | 532 s | 12 min | 452 | 265 | 3 |
| Shared stream + RESEND lists | 20 s | 100% | 20 s | 29 s | 184 | 107 | 3 |
| Shared stream + RLNC (NEED) | 13 s | 100% | 14 s | 16 s | 95 | 51 | 3 |

### N=1 loss=15% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 6 s | 100% | 17 s | 23 s | 3 | 0 | 40 |
| Shared stream + RLNC (NEED) | 6 s | 100% | 14 s | 23 s | 4 | 0 | 40 |

### N=5 loss=15% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 117 s (+81 s still queued) | 47% | never | never | 76 | 38 | 15 |
| Gorb today + Echo | 92 s (+13 s still queued) | 100% | 56 s | 100 s | 38 | 157 | 15 |
| ARQ, fixed (1 session/listener) | 31 s | 100% | 49 s | 64 s | 19 | 4 | 15 |
| Shared stream + RESEND lists | 11 s | 100% | 22 s | 34 s | 15 | 2 | 15 |
| Shared stream + RLNC (NEED) | 9 s (+1 s still queued) | 100% | 13 s | 14 s | 6 | 1 | 15 |

### N=20 loss=15% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 115 s (+20 min still queued) | 0% | never | never | 420 | 156 | 8 |
| Gorb today + Echo | 150 s (+19 min still queued) | 1% | never | never | 415 | 447 | 8 |
| ARQ, fixed (1 session/listener) | 124 s | 100% | 148 s | 182 s | 84 | 28 | 8 |
| Shared stream + RESEND lists | 16 s | 100% | 26 s | 38 s | 51 | 13 | 8 |
| Shared stream + RLNC (NEED) | 9 s | 100% | 13 s | 14 s | 20 | 4 | 8 |

### N=100 loss=15% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 151 s (+59 min still queued) | 0% | never | never | 2100 | 1311 | 3 |
| Gorb today + Echo | 169 s (+47 min still queued) | 0% | never | never | 2064 | 1653 | 3 |
| ARQ, fixed (1 session/listener) | 11 min | 100% | 13 min | 14 min | 666 | 366 | 3 |
| Shared stream + RESEND lists | 27 s | 100% | 32 s | 46 s | 249 | 138 | 3 |
| Shared stream + RLNC (NEED) | 13 s | 100% | 9 s | 15 s | 64 | 34 | 3 |

### N=1 loss=30% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 9 s | 100% | 32 s | 91 s | 7 | 0 | 40 |
| Shared stream + RLNC (NEED) | 8 s | 100% | 20 s | 31 s | 4 | 0 | 40 |

### N=5 loss=30% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 120 s (+76 s still queued) | 28% | never | never | 91 | 42 | 15 |
| Gorb today + Echo | 120 s (+18 s still queued) | 87% | 94 s | never | 60 | 179 | 15 |
| ARQ, fixed (1 session/listener) | 38 s | 100% | 73 s | 100 s | 32 | 6 | 15 |
| Shared stream + RESEND lists | 22 s | 89% | 37 s | never | 32 | 3 | 15 |
| Shared stream + RLNC (NEED) | 12 s | 100% | 14 s | 20 s | 8 | 2 | 15 |

### N=20 loss=30% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 115 s (+16 min still queued) | 0% | never | never | 420 | 162 | 8 |
| Gorb today + Echo | 122 s (+15 min still queued) | 1% | never | never | 418 | 198 | 8 |
| ARQ, fixed (1 session/listener) | 153 s | 100% | 212 s | 261 s | 151 | 49 | 8 |
| Shared stream + RESEND lists | 34 s | 96% | 43 s | 63 s | 96 | 19 | 8 |
| Shared stream + RLNC (NEED) | 12 s | 100% | 15 s | 17 s | 24 | 6 | 8 |

### N=100 loss=30% 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 152 s (+48 min still queued) | 0% | never | never | 2100 | 1312 | 3 |
| Gorb today + Echo | 152 s (+48 min still queued) | 0% | never | never | 2100 | 1312 | 3 |
| ARQ, fixed (1 session/listener) | 13 min | 100% | 17 min | 19 min | 1054 | 532 | 3 |
| Shared stream + RESEND lists | 65 s | 99% | 47 s | 84 s | 389 | 176 | 3 |
| Shared stream + RLNC (NEED) | 20 s | 100% | 15 s | 19 s | 92 | 51 | 3 |

### N=1 loss=5% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 36 s | 100% | 46 s | 52 s | 2 | 0 | 40 |
| Shared stream + RLNC (NEED) | 36 s | 100% | 47 s | 53 s | 3 | 0 | 40 |

### N=5 loss=5% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 146 s (+37 min still queued) | 0% | never | never | 105 | 51 | 15 |
| Gorb today + Echo | 146 s (+37 min still queued) | 0% | never | never | 105 | 51 | 15 |
| ARQ, fixed (1 session/listener) | 180 s | 100% | 212 s | 224 s | 16 | 4 | 15 |
| Shared stream + RESEND lists | 44 s | 100% | 60 s | 67 s | 13 | 3 | 15 |
| Shared stream + RLNC (NEED) | 38 s | 100% | 46 s | 47 s | 5 | 2 | 15 |

### N=20 loss=5% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 150 s (+166 min still queued) | 0% | never | never | 420 | 143 | 8 |
| Gorb today + Echo | 150 s (+166 min still queued) | 0% | never | never | 420 | 143 | 8 |
| ARQ, fixed (1 session/listener) | 12 min | 100% | 17 min | 18 min | 72 | 25 | 8 |
| Shared stream + RESEND lists | 66 s | 100% | 74 s | 90 s | 52 | 17 | 8 |
| Shared stream + RLNC (NEED) | 39 s | 100% | 47 s | 48 s | 22 | 9 | 8 |

### N=100 loss=5% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 205 s (+467 min still queued) | 0% | never | never | 2099 | 1293 | 1 |
| Gorb today + Echo | 205 s (+467 min still queued) | 0% | never | never | 2099 | 1293 | 1 |
| ARQ, fixed (1 session/listener) | 60 min | 100% | 102 min | 112 min | 615 | 372 | 3 |
| Shared stream + RESEND lists | 142 s | 100% | 98 s | 180 s | 341 | 205 | 3 |
| Shared stream + RLNC (NEED) | 42 s | 100% | 49 s | 51 s | 97 | 48 | 3 |

### N=1 loss=15% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 40 s | 100% | 62 s | 73 s | 4 | 0 | 40 |
| Shared stream + RLNC (NEED) | 40 s | 100% | 55 s | 64 s | 4 | 0 | 40 |

### N=5 loss=15% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 147 s (+32 min still queued) | 0% | never | never | 105 | 52 | 15 |
| Gorb today + Echo | 147 s (+32 min still queued) | 0% | never | never | 105 | 52 | 15 |
| ARQ, fixed (1 session/listener) | 201 s | 100% | 252 s | 268 s | 29 | 9 | 15 |
| Shared stream + RESEND lists | 60 s | 100% | 86 s | 93 s | 20 | 5 | 15 |
| Shared stream + RLNC (NEED) | 43 s | 100% | 51 s | 53 s | 7 | 2 | 15 |

### N=20 loss=15% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 151 s (+146 min still queued) | 0% | never | never | 420 | 148 | 8 |
| Gorb today + Echo | 151 s (+146 min still queued) | 0% | never | never | 420 | 148 | 8 |
| ARQ, fixed (1 session/listener) | 13 min | 100% | 20 min | 21 min | 115 | 40 | 8 |
| Shared stream + RESEND lists | 166 s | 96% | 111 s | 232 s | 108 | 32 | 8 |
| Shared stream + RLNC (NEED) | 44 s | 100% | 52 s | 54 s | 21 | 6 | 8 |

### N=100 loss=15% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 205 s (+412 min still queued) | 0% | never | never | 2099 | 1303 | 1 |
| Gorb today + Echo | 205 s (+412 min still queued) | 0% | never | never | 2099 | 1303 | 1 |
| ARQ, fixed (1 session/listener) | 67 min | 100% | 122 min | 127 min | 788 | 390 | 3 |
| Shared stream + RESEND lists | 336 s (+19 s still queued) | 4% | never | never | 1635 | 1226 | 3 |
| Shared stream + RLNC (NEED) | 53 s | 100% | 53 s | 55 s | 76 | 38 | 3 |

### N=1 loss=30% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 81 s | 88% | 89 s | never | 11 | 0 | 40 |
| Shared stream + RLNC (NEED) | 51 s | 100% | 72 s | 86 s | 5 | 0 | 40 |

### N=5 loss=30% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 147 s (+27 min still queued) | 0% | never | never | 105 | 50 | 15 |
| Gorb today + Echo | 147 s (+27 min still queued) | 0% | never | never | 105 | 50 | 15 |
| ARQ, fixed (1 session/listener) | 246 s | 100% | 328 s | 368 s | 51 | 15 | 15 |
| Shared stream + RESEND lists | 211 s | 23% | never | never | 82 | 21 | 15 |
| Shared stream + RLNC (NEED) | 57 s (+3 s still queued) | 100% | 62 s | 68 s | 10 | 3 | 15 |

### N=20 loss=30% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 150 s (+118 min still queued) | 0% | never | never | 420 | 150 | 8 |
| Gorb today + Echo | 150 s (+118 min still queued) | 0% | never | never | 420 | 150 | 8 |
| ARQ, fixed (1 session/listener) | 16 min | 100% | 26 min | 27 min | 226 | 77 | 8 |
| Shared stream + RESEND lists | 281 s | 1% | never | never | 230 | 86 | 8 |
| Shared stream + RLNC (NEED) | 62 s | 100% | 63 s | 66 s | 30 | 8 | 8 |

### N=100 loss=30% 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 205 s (+345 min still queued) | 0% | never | never | 2099 | 1298 | 1 |
| Gorb today + Echo | 205 s (+345 min still queued) | 0% | never | never | 2099 | 1298 | 1 |
| ARQ, fixed (1 session/listener) | 133 min | 1% | never | never | 6121 | 4925 | 3 |
| Shared stream + RESEND lists | 376 s (+23 s still queued) | 0% | never | never | 1866 | 1528 | 3 |
| Shared stream + RLNC (NEED) | 81 s (+10 s still queued) | 100% | 66 s | 71 s | 113 | 69 | 3 |

### hetero N=20 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 116 s (+20 min still queued) | 0% | never | never | 420 | 150 | 8 |
| Gorb today + Echo | 195 s (+14 min still queued) | 12% | never | never | 379 | 908 | 8 |
| ARQ, fixed (1 session/listener) | 129 s | 100% | 151 s | 207 s | 94 | 28 | 8 |
| Shared stream + RESEND lists | 24 s | 99% | 20 s | 49 s | 58 | 13 | 8 |
| Shared stream + RLNC (NEED) | 12 s | 100% | 13 s | 15 s | 20 | 4 | 8 |

### bursty N=20 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 117 s (+21 min still queued) | 0% | never | never | 420 | 157 | 8 |
| Gorb today + Echo | 151 s (+17 min still queued) | 2% | never | never | 402 | 501 | 8 |
| ARQ, fixed (1 session/listener) | 125 s | 100% | 154 s | 204 s | 82 | 24 | 8 |
| Shared stream + RESEND lists | 18 s | 99% | 24 s | 38 s | 46 | 12 | 8 |
| Shared stream + RLNC (NEED) | 11 s (+1 s still queued) | 100% | 13 s | 15 s | 21 | 6 | 8 |

### relay N=20 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 146 s (+42 s still queued) | 0% | never | never | 455 | 681 | 8 |
| Gorb today + Echo | 146 s (+42 s still queued) | 0% | never | never | 455 | 681 | 8 |
| ARQ, fixed (1 session/listener) | 527 s | 100% | 484 s | 14 min | 932 | 998 | 8 |
| Shared stream + RESEND lists | 88 s | 89% | 68 s | never | 258 | 198 | 8 |
| Shared stream + RLNC (NEED) | 26 s (+1 s still queued) | 100% | 26 s | 39 s | 42 | 49 | 8 |

### relay N=20 paced 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| ARQ, fixed (1 session/listener) | 261 s | 100% | 11 min | 12 min | 222 | 87 | 8 |
| Shared stream + RESEND lists | 49 s | 98% | 81 s | 110 s | 151 | 44 | 8 |
| Shared stream + RLNC (NEED) | 18 s | 100% | 42 s | 47 s | 46 | 14 | 8 |

### bursty N=1 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 6 s | 100% | 12 s | 33 s | 3 | 0 | 40 |
| Shared stream + RLNC (NEED) | 6 s | 100% | 13 s | 26 s | 4 | 0 | 40 |

### relay N=1 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 15 s | 100% | 33 s | 46 s | 8 | 3 | 40 |
| Shared stream + RLNC (NEED) | 17 s | 100% | 27 s | 39 s | 8 | 6 | 40 |

### relay N=1 paced 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 13 s | 100% | 46 s | 62 s | 7 | 0 | 40 |
| Shared stream + RLNC (NEED) | 13 s | 100% | 43 s | 52 s | 7 | 0 | 40 |

### staggered N=20 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 123 s | 100% | 18 s | 28 s | 64 | 2 | 8 |
| Gorb today + Echo | 192 s | 100% | 17 s | 33 s | 64 | 485 | 8 |
| ARQ, fixed (1 session/listener) | 119 s | 100% | 18 s | 29 s | 63 | 3 | 8 |
| Shared stream + RLNC (NEED) | 155 s | 100% | 14 s | 20 s | 60 | 0 | 8 |
| RLNC + Echo (keep partial) | 86 s | 100% | 12 s | 18 s | 35 | 0 | 8 |

### EU868 N=20 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 108 s (+46 min still queued) | 0% | never | never | 412 | 73 | 6 |
| ARQ, fixed (1 session/listener) | 247 s | 100% | 30 min | 34 min | 81 | 23 | 6 |
| Shared stream + RESEND lists | 37 s | 100% | 63 s | 90 s | 49 | 14 | 6 |
| Shared stream + RLNC (NEED) | 19 s | 100% | 32 s | 34 s | 15 | 4 | 6 |

### EU868 N=20 patient 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 122 s (+49 min still queued) | 0% | never | never | 420 | 55 | 6 |
| ARQ, fixed (1 session/listener) | 247 s | 100% | 30 min | 34 min | 81 | 23 | 6 |
| Shared stream + RESEND lists | 33 s | 100% | 63 s | 90 s | 48 | 14 | 6 |
| Shared stream + RLNC (NEED) | 19 s | 100% | 32 s | 34 s | 15 | 4 | 6 |

### EU868 N=1 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 12 s | 100% | 48 s | 67 s | 3 | 0 | 40 |
| Shared stream + RLNC (NEED) | 13 s | 100% | 33 s | 67 s | 4 | 0 | 40 |

### EU868 N=1 patient 6 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 12 s | 100% | 48 s | 67 s | 3 | 0 | 40 |
| Shared stream + RLNC (NEED) | 13 s | 100% | 33 s | 67 s | 4 | 0 | 40 |

### hetero N=20 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 150 s (+144 min still queued) | 0% | never | never | 420 | 146 | 8 |
| Gorb today + Echo | 150 s (+144 min still queued) | 0% | never | never | 420 | 146 | 8 |
| ARQ, fixed (1 session/listener) | 14 min | 100% | 20 min | 22 min | 150 | 56 | 8 |
| Shared stream + RESEND lists | 267 s | 50% | never | never | 166 | 50 | 8 |
| Shared stream + RLNC (NEED) | 55 s | 100% | 53 s | 63 s | 24 | 7 | 8 |

### bursty N=20 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 151 s (+148 min still queued) | 0% | never | never | 420 | 154 | 8 |
| Gorb today + Echo | 151 s (+148 min still queued) | 0% | never | never | 420 | 154 | 8 |
| ARQ, fixed (1 session/listener) | 13 min | 100% | 20 min | 21 min | 122 | 40 | 8 |
| Shared stream + RESEND lists | 180 s | 96% | 110 s | 242 s | 109 | 31 | 8 |
| Shared stream + RLNC (NEED) | 47 s | 100% | 52 s | 57 s | 22 | 6 | 8 |

### relay N=20 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 169 s (+508 s still queued) | 0% | never | never | 446 | 679 | 8 |
| Gorb today + Echo | 169 s (+508 s still queued) | 0% | never | never | 446 | 679 | 8 |
| ARQ, fixed (1 session/listener) | 38 min | 0% | never | never | 1932 | 3642 | 8 |
| Shared stream + RESEND lists | 295 s | 0% | never | never | 430 | 698 | 8 |
| Shared stream + RLNC (NEED) | 300 s (+42 s still queued) | 51% | 380 s | never | 154 | 315 | 8 |

### relay N=20 paced 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| ARQ, fixed (1 session/listener) | 28 min | 100% | 72 min | 74 min | 301 | 132 | 8 |
| Shared stream + RESEND lists | 384 s | 77% | 467 s | never | 284 | 93 | 8 |
| Shared stream + RLNC (NEED) | 97 s | 100% | 228 s | 239 s | 48 | 14 | 8 |

### bursty N=1 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 43 s | 100% | 58 s | 78 s | 4 | 0 | 40 |
| Shared stream + RLNC (NEED) | 41 s | 100% | 52 s | 63 s | 4 | 0 | 40 |

### relay N=1 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 253 s | 0% | never | never | 31 | 100 | 40 |
| Shared stream + RLNC (NEED) | 256 s (+27 s still queued) | 62% | 373 s | never | 21 | 96 | 40 |

### relay N=1 paced 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 97 s | 100% | 243 s | 496 s | 12 | 0 | 40 |
| Shared stream + RLNC (NEED) | 85 s | 100% | 229 s | 248 s | 8 | 0 | 40 |

### staggered N=20 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 19 min (+130 min still queued) | 5% | never | never | 402 | 168 | 8 |
| Gorb today + Echo | 45 min (+18 min still queued) | 41% | never | never | 254 | 12970 | 8 |
| ARQ, fixed (1 session/listener) | 13 min | 100% | 110 s | 254 s | 102 | 24 | 8 |
| Shared stream + RLNC (NEED) | 14 min | 100% | 51 s | 57 s | 41 | 3 | 8 |
| RLNC + Echo (keep partial) | 429 s (+36 s still queued) | 100% | 35 s | 55 s | 28 | 2 | 8 |

### EU868 N=20 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 108 s (+326 min still queued) | 0% | never | never | 414 | 77 | 6 |
| ARQ, fixed (1 session/listener) | 25 min | 17% | never | never | 64 | 17 | 6 |
| Shared stream + RESEND lists | 78 s (+13 s still queued) | 0% | never | never | 46 | 8 | 6 |
| Shared stream + RLNC (NEED) | 78 s (+251 s still queued) | 0% | never | never | 52 | 12 | 6 |

### EU868 N=20 patient 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 143 s (+333 min still queued) | 0% | never | never | 420 | 73 | 6 |
| ARQ, fixed (1 session/listener) | 27 min | 100% | 255 min | 260 min | 105 | 24 | 6 |
| Shared stream + RESEND lists | 229 s | 99% | 20 min | 23 min | 80 | 22 | 6 |
| Shared stream + RLNC (NEED) | 93 s (+5 s still queued) | 100% | 442 s | 473 s | 14 | 3 | 6 |

### EU868 N=1 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 76 s (+13 min still queued) | 0% | never | never | 15 | 0 | 40 |
| Shared stream + RLNC (NEED) | 75 s (+249 s still queued) | 0% | never | never | 6 | 0 | 40 |

### EU868 N=1 patient 40 KB

| scheme | channel time | got the page | median wait | p90 wait | control pkts | collisions | trials |
|---|---|---|---|---|---|---|---|
| Gorb today (ARQ) | 80 s | 100% | 457 s | 483 s | 4 | 0 | 40 |
| Shared stream + RLNC (NEED) | 80 s | 100% | 440 s | 473 s | 4 | 0 | 40 |

## Part 3: carousel (packets a listener waits after tuning in; 'passes' = multiples of K)

| page / loss | plain loop | plain+coded passes | RLNC loop | LT loop |
|---|---|---|---|---|
| 6 KB loss=5% | 1.61 (p90 2.00) | 1.05 (p90 1.11) | 1.05 (p90 1.11) | 1.53 (p90 1.86) |
| 6 KB loss=15% | 2.34 (p90 3.06) | 1.18 (p90 1.29) | 1.17 (p90 1.29) | 1.74 (p90 2.17) |
| 6 KB loss=30% | 3.51 (p90 4.74) | 1.43 (p90 1.60) | 1.43 (p90 1.57) | 2.11 (p90 2.63) |
| 6 KB loss=GE | 2.09 (p90 2.92) | 1.18 (p90 1.37) | 1.18 (p90 1.37) | 1.72 (p90 2.15) |
| 40 KB loss=5% | 2.24 (p90 2.87) | 1.05 (p90 1.07) | 1.05 (p90 1.07) | 1.32 (p90 1.43) |
| 40 KB loss=15% | 3.28 (p90 4.07) | 1.18 (p90 1.21) | 1.18 (p90 1.21) | 1.48 (p90 1.60) |
| 40 KB loss=30% | 5.04 (p90 6.15) | 1.43 (p90 1.50) | 1.43 (p90 1.50) | 1.80 (p90 1.95) |
| 40 KB loss=GE | 3.22 (p90 4.01) | 1.18 (p90 1.26) | 1.18 (p90 1.25) | 1.48 (p90 1.62) |

## Part 4: frame budget

| K | DATA frame | coded frame (seed) | data bytes left if the full coefficient vector is sent |
|---|---|---|---|
| 10 | 212 B | 216 B | 204 B |
| 35 | 212 B | 216 B | 179 B |
| 100 | 212 B | 216 B | 114 B |
| 228 | 212 B | 216 B | -14 B |
| 400 | 212 B | 216 B | -186 B |

| K | seq numbers per RESEND (180 B) | bitmap of all K |
|---|---|---|
| 35 | 35 | 5 B |
| 228 | 63 | 29 B |
| 364 | 63 | 46 B |

airtime US915: DATA 148.6 ms, coded 151.2 ms
