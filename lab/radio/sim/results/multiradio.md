# Idea 3: LoRa + Wi-Fi together (mixed town, 30% LoRa boards)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## mixed: mixed town, 3 loads

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| baseline: flood on every radio (firmware) | 23% ±24 | 43.1 ±29.2 | 13% | 151 ±49 | 4.3 ±1.3 | 64 ±20 |
| LoRa only (Wi-Fi off) | 18% ±17 | 60.6 ±9.0 | 8% | 99 ±68 | 0.0 ±0.0 | 39 ±27 |
| ESP-NOW only (LoRa off) | 12% ±19 | 9.5 ±6.1 | 12% | 0 ±0 | 0.3 ±0.3 | 0 ±0 |
| flood, Wi-Fi in LR 250k | 33% ±24 | 47.6 ±27.4 | 23% | 157 ±45 | 24.3 ±6.6 | 82 ±22 |
| route, best radio per hop | 38% ±32 | 35.0 ±35.8 | 32% | 227 ±92 | 8.7 ±4.7 | 98 ±40 |
| route, best radio + Wi-Fi rate per hop | 40% ±29 | 51.5 ±41.4 | 27% | 232 ±77 | 8.7 ±4.0 | 100 ±33 |
| gradient + fast repair (both radios) | 35% ±36 | 35.8 ±31.3 | 25% | 247 ±78 | 10.6 ±5.4 | 107 ±34 |
