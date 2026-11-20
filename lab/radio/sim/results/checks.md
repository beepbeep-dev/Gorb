# Self-checks

## LoRa airtime (Semtech formula) vs. published calculator values

| mode | payload bytes | expected ms | lab ms |
|---|---|---|---|
| SF7/125k | 20 | 56.6 | 56.6 |
| SF12/125k | 20 | 1318.9 | 1318.9 |
| SF9/125k | 20 | 185.3 | 185.3 |
| SF10/125k | 51 | 616.4 | 616.4 |
| LoRa SF8/500k (Gorb) | 215 | - | 151.2 |
| LoRa SF8/250k (Gorb) | 215 | - | 302.3 |

## Pure ALOHA throughput (20 senders, no capture, no carrier sense)

| offered load G | lab S | theory G·e^-2G |
|---|---|---|
| 0.1 | 0.079 | 0.082 |
| 0.25 | 0.156 | 0.152 |
| 0.5 | 0.186 | 0.184 |
| 0.75 | 0.169 | 0.167 |
| 1.0 | 0.141 | 0.135 |
| 1.5 | 0.088 | 0.075 |
| 2.0 | 0.046 | 0.037 |

With carrier sense (CAD) on: G=0.25: S=0.234, G=0.5: S=0.435, G=1.0: S=0.647, G=2.0: S=0.66

## Modes: sensitivity and median range (path-loss exponent 3.3, no shadowing)

| mode | sensitivity dBm | kbit/s | airtime of a 215-byte frame (ms) | outdoor-outdoor m | one end indoors m | both indoors m |
|---|---|---|---|---|---|---|
| LoRa SF7/125k | -123.5 | 5.5 | 338.18 | 3243 | 1614 | 803 |
| LoRa SF12/125k | -136.0 | 0.3 | 7708.67 | 7757 | 3861 | 1922 |
| LoRa SF5/500k | -112.5 | 62.5 | 29.26 | 1503 | 748 | 372 |
| LoRa SF7/500k | -117.5 | 21.9 | 84.54 | 2131 | 1060 | 528 |
| LoRa SF8/500k | -120.0 | 12.5 | 151.17 | 2537 | 1262 | 628 |
| LoRa SF8/250k | -123.0 | 6.2 | 302.34 | 3129 | 1558 | 775 |
| Wi-Fi LR 250k | -104.0 | 250.0 | 8.64 | 347 | 140 | 57 |
| Wi-Fi LR 500k | -101.0 | 500.0 | 4.51 | 281 | 114 | 46 |
| ESP-NOW 1M | -97.0 | 1000.0 | 2.26 | 213 | 86 | 35 |
| ESP-NOW 11M | -88.0 | 11000.0 | 0.38 | 114 | 46 | 19 |
| ESP-NOW 54M | -74.0 | 54000.0 | 0.07 | 28 | 11 | 5 |
