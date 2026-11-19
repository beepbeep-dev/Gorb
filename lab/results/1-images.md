# Experiment 1: which picture format looks best per byte

6 test photos, each judged at 640px wide. Number = average SSIM (1.0 = identical, 0.9 = good, 0.8 = soft). For each budget the lab tried several picture sizes and every quality level, and kept the best result.

| budget | JPEG | WEBP | AVIF | LoRa US915 time |
|---|---|---|---|---|
| 1.5 KB | 0.663 | 0.734 | 0.742 | 2s |
| 3 KB | 0.734 | 0.789 | 0.810 | 3s |
| 6 KB | 0.805 | 0.849 | 0.873 | 7s |
| 12 KB | 0.872 | 0.903 | 0.921 | 14s |
| 24 KB | 0.924 | 0.942 | 0.954 | 27s |

WebP at 6 KB scores 0.849; AVIF matches that with about 4.6 KB (23% fewer bytes).

Per photo at 6 KB (WebP → AVIF):

* astronaut (portrait): 0.861 → 0.893
* coffee (still life): 0.797 → 0.848
* chelsea (cat, fur): 0.882 → 0.908
* rocket (outdoor): 0.910 → 0.919
* hubble (stars): 0.771 → 0.786
* tower (drawing): 0.875 → 0.881
