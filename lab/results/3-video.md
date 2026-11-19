# Experiment 3: video per byte

A 12 s narrated test clip (slow pans over photos plus synthetic speech). SSIM is measured at 640x360 and 30 frames per second, so jerky low frame rates count against a clip, as they do for a viewer. Higher is better.

| budget | format | bytes | SSIM | size | fps | sound |
|---|---|---|---|---|---|---|
| 16 KB | Motion v1: animated WebP (silent) | 15,676 | 0.726 | 150px | 1.0 | none |
| 16 KB | Motion 2 as shipped (AV1+Opus) | 15,720 | 0.866 | 320px | 12 | 6 kbps, 6s kept |
| 16 KB | Motion 2 as shipped (H.264+AAC) | 15,784 | 0.752 | 192px | 12 | 12 kbps, 5s kept |
| 32 KB | Motion v1: animated WebP (silent) | 31,652 | 0.750 | 200px | 1.0 | none |
| 32 KB | AV1 + Opus | 28,795 | 0.879 | 320px | 12 | 6 kbps |
| 32 KB | VP9 + Opus | 31,264 | 0.858 | 256px | 15 | 6 kbps |
| 32 KB | H.264 + AAC | 31,665 | 0.777 | 192px | 12 | 12 kbps |
| 32 KB | Motion 2 as shipped (AV1+Opus) | 28,795 | 0.879 | 320px | 12 | 6 kbps, 12s kept |
| 32 KB | Motion 2 as shipped (H.264+AAC) | 30,056 | 0.787 | 192px | 12 | 12 kbps, 9s kept |
| 48 KB | Motion v1: animated WebP (silent) | 47,898 | 0.789 | 200px | 2.0 | none |
| 48 KB | AV1 + Opus | 42,260 | 0.914 | 320px | 12 | 6 kbps |
| 48 KB | VP9 + Opus | 44,179 | 0.891 | 256px | 15 | 6 kbps |
| 48 KB | H.264 + AAC | 45,914 | 0.854 | 256px | 15 | 12 kbps |
| 48 KB | Motion 2 as shipped (AV1+Opus) | 43,578 | 0.914 | 320px | 12 | 6 kbps, 12s kept |
| 48 KB | Motion 2 as shipped (H.264+AAC) | 45,593 | 0.818 | 256px | 15 | 12 kbps, 12s kept |

LoRa US915 download time: 16 KB ≈ 18s, 32 KB ≈ 36s, 48 KB ≈ 54s.
