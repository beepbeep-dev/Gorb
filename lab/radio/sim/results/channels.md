# Idea 5: several LoRa channels (8 hosts, 8 loads at once)

20 random seeds per row; mean ± standard deviation across seeds.
Load time = median over the loads that finished (failures excluded); 'within 60 s' = share of all loads finished within 60 s.

## busy8: 8 hosts, 8 loads within 10 s

| variant | pages delivered | load time (s) | within 60 s | LoRa airtime (s) | Wi-Fi airtime (s) | radio energy (J) |
|---|---|---|---|---|---|---|
| flood, 1 channel | 2% ±4 | 49.1 ±10.1 | 2% | 349 ±74 | 0.0 ±0.0 | 139 ±30 |
| flood, 4 channels (host cells) | 44% ±14 | 63.1 ±13.9 | 21% | 567 ±86 | 0.0 ±0.0 | 227 ±34 |
| flood, 8 channels (host cells) | 59% ±14 | 36.9 ±7.1 | 56% | 326 ±47 | 0.0 ±0.0 | 130 ±19 |
| gradient+fast, 1 channel | 2% ±5 | 46.2 ±8.8 | 2% | 669 ±54 | 0.0 ±0.0 | 268 ±21 |
| gradient+fast, 4 channels | 69% ±19 | 41.5 ±13.9 | 52% | 546 ±146 | 0.0 ±0.0 | 219 ±58 |
