# Experiment 2: what's on screen while a picture arrives

Time until the picture shown reaches each quality step (one relay hop doubles these). Before the first step the reader sees an empty box.


**US915**

| strategy | steps (time → SSIM) |
|---|---|
| A: WebP 6 KB | 6.9s → 0.85 |
| B: preview + WebP 6 KB | 0.2s → 0.60, 6.9s → 0.85 |
| C: Gorb Pixels 2 (AVIF layers) | 0.2s → 0.60, 6.9s → 0.87, 26.5s → 0.94 |

**EU868**

| strategy | steps (time → SSIM) |
|---|---|
| A: WebP 6 KB | 12.2s → 0.85 |
| B: preview + WebP 6 KB | 0.4s → 0.60, 12.2s → 0.85 |
| C: Gorb Pixels 2 (AVIF layers) | 0.4s → 0.60, 12.2s → 0.87, 82.2s → 0.94 |
