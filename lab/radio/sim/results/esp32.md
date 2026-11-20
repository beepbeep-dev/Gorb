# Plain ESP32 only (no LoRa): ESP-NOW options

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## esp32: plain ESP32s, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline: ESP-NOW 1M flood, 180 B chunks | 93% ±13 | 19.9 ±5.4 | 93% | 0 ±0 | 4.2 ±1.4 | 3 ±1 |
| Wi-Fi LR 250k flood | 95% ±12 | 16.4 ±4.5 | 95% | 0 ±0 | 12.6 ±4.1 | 10 ±3 |
| Wi-Fi LR 500k flood | 98% ±7 | 16.9 ±3.8 | 98% | 0 ±0 | 7.2 ±2.4 | 6 ±2 |
| ESP-NOW v2 1400 B chunks, flood | 87% ±19 | 8.1 ±3.3 | 87% | 0 ±0 | 3.3 ±1.3 | 3 ±1 |
| LR 250k + 1400 B chunks, flood | 87% ±22 | 16.5 ±4.4 | 87% | 0 ±0 | 15.3 ±4.6 | 12 ±4 |
| gradient | 93% ±13 | 17.1 ±4.6 | 93% | 0 ±0 | 1.3 ±0.3 | 1 ±0 |
| route + per-hop rate + ACK | 97% ±10 | 14.9 ±3.7 | 97% | 0 ±0 | 1.5 ±0.4 | 1 ±0 |
| route + per-hop rate + 1400 B | 98% ±7 | 4.1 ±2.0 | 98% | 0 ±0 | 1.5 ±0.6 | 1 ±0 |
