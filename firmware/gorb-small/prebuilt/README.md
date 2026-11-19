# Ready-to-flash G.O.R.B Small firmware

Each `.bin` is a complete image (bootloader, partition table and app), flashed
at address **0x0**. The easiest way is the official Espressif web flasher in
Chrome or Edge: https://espressif.github.io/esptool-js/ (Connect → add the file
at 0x0 → Program → press the board's reset button).

| file | board |
|---|---|
| `gorb-small-esp32dev.bin` | any ESP32 dev board (ESP32-WROOM / -WROVER), with an RFM95W wired as in docs/GETTING-STARTED.md |
| `gorb-small-heltec_v3.bin` | Heltec WiFi LoRa 32 V3 |
| `gorb-small-lilygo_t3.bin` | LilyGo TTGO LoRa32 T3 v1.6.1 |
| `gorb-small-tbeam.bin` | LilyGo T-Beam (SX1276) |

The ESP32 dev board file also runs on a bare ESP32 with no radio module:
the hotspot, browser, site editor, picture upload and Gorb Wi-Fi (ESP-NOW to other boards) all work, with LoRa off.

Rebuild these after changing the code (`pio run`, then esptool `merge_bin`).
