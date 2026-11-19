# Experiment 4: Gorb Echo

Five neighbours each open the same page: about 103 KB (HTML, two pictures with preview + standard + HD layers, one video). The host is one relay hop away, so without Echo every byte is sent twice (host → relay → you).

| region | case | radio time used | wait for visitor 2-5 |
|---|---|---|---|
| US915 | no Echo | 19.5 min | 234s |
| US915 | Echo, neighbours on the air at the time | 3.9 min | instant |
| US915 | Echo, neighbours turn up later | 11.7 min | 117s |
| EU868 | no Echo | 244.9 min | 2939s |
| EU868 | Echo, neighbours on the air at the time | 49.0 min | instant |
| EU868 | Echo, neighbours turn up later | 146.9 min | 1469s |

Radio is shared, so radio time saved is time everyone else can use (rule 3). With Echo, a popular page costs about one download per neighbourhood instead of one per person, which is what makes the big HD layer affordable.
