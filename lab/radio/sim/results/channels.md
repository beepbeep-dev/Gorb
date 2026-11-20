# Idea 5: several LoRa channels (8 hosts, 8 loads at once)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## busy8: 8 hosts, 8 loads within 10 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| flood, 1 channel | 2% ±4 | 41.8 ±6.8 | 2% | 278 ±71 | 0.0 ±0.0 | 111 ±28 |
| flood, 4 channels (host cells) | 56% ±15 | 46.0 ±15.6 | 34% | 344 ±80 | 0.0 ±0.0 | 138 ±32 |
| flood, 8 channels (host cells) | 59% ±17 | 35.7 ±14.4 | 49% | 205 ±44 | 0.0 ±0.0 | 82 ±18 |
| gradient, 1 channel | 4% ±7 | 46.1 ±14.6 | 3% | 280 ±68 | 0.0 ±0.0 | 112 ±27 |
| gradient, 4 channels | 65% ±15 | 42.0 ±8.1 | 49% | 187 ±54 | 0.0 ±0.0 | 75 ±21 |
