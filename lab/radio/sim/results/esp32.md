# Plain ESP32 only (no LoRa): ESP-NOW options

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## esp32: plain ESP32s, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline: ESP-NOW 1M flood, 180 B chunks | 92% ±18 | 8.9 ±3.8 | 92% | 0 ±0 | 7.0 ±1.6 | 6 ±1 |
| Wi-Fi LR 250k flood | 87% ±22 | 14.7 ±3.0 | 87% | 0 ±0 | 30.0 ±4.3 | 24 ±3 |
| Wi-Fi LR 500k flood | 98% ±7 | 10.1 ±2.3 | 98% | 0 ±0 | 15.2 ±2.3 | 12 ±2 |
| ESP-NOW v2 1400 B chunks, flood | 97% ±10 | 6.8 ±3.7 | 97% | 0 ±0 | 4.7 ±1.1 | 4 ±1 |
| LR 250k + 1400 B chunks, flood | 82% ±20 | 12.2 ±4.6 | 82% | 0 ±0 | 21.1 ±6.0 | 17 ±5 |
| gradient + fast repair | 100% ±0 | 5.5 ±4.4 | 100% | 0 ±0 | 2.8 ±1.5 | 2 ±1 |
| route + per-hop rate + ACK | 100% ±0 | 2.6 ±1.9 | 100% | 0 ±0 | 3.4 ±1.9 | 3 ±1 |
| route + per-hop rate + 1400 B | 98% ±7 | 2.6 ±1.2 | 98% | 0 ±0 | 3.6 ±1.7 | 3 ±1 |
