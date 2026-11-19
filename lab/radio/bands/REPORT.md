# Band scout: more radios for G.O.R.B

Written 2026-11-19 by the Gorb lab "band scout" (research only, no code changed).
This is for a beginner in New York (USA) with one classic ESP32-WROOM and a small budget, with EU notes where they differ.

How to read it:
* **License-free** means no ham license is needed, as long as you use certified hardware with its normal antenna and no amplifier.
* **Ranges** are real outdoor line-of-sight results from tests, not the number on the box. Expect a quarter to a tenth of that indoors.
* **(unverified)** means no official source confirmed it.
* This is not legal advice. Check current rules at https://www.ecfr.gov.

## 1. Ranked table

Gorb fit score: 5 = great, 1 = poor.

| # | Option | Extra cost | Real range (outdoor LOS) | Speed (raw / Gorb-usable) | License-free in US? | Works with your ESP32 as-is? | Gorb fit |
|---|---|---|---|---|---|---|---|
| 1 | **ESP-NOW in Wi-Fi Long Range (LR) mode** | $0 | ~300-900 m (~1 km ideal) | 250/500 kbit/s (~10-100 kbit/s) | Yes (15.247) | **Yes, firmware only** | **5** |
| 2 | ESP-NOW v2 (1470-byte packets) | $0 | same as ESP-NOW | 1 Mbit/s | Yes | Yes (ESP-IDF 5.4.2+ / Arduino-ESP32 3.2.1+) | 4 |
| 3 | **SX1280 2.4 GHz LoRa/FLRC** (Ebyte E28) | ~$5-9 | ~1-3 km LoRa | LoRa ~200 kbit/s, FLRC 1.3 Mbit/s | Yes (15.247) | Module on SPI | 4 |
| 4 | 5.8 GHz directional Wi-Fi link (e.g. NanoStation 5AC Loco pair) | ~$120-150/link | 1-10+ km rooftop to rooftop | 100+ Mbit/s | Yes (15.407) | Carries Gorb Wi-Fi as-is | 4 (long hops) |
| 5 | 915 MHz LoRa board (Gorb Small today) | ~$18-30/board | 2-10 km | 12.5 kbit/s | Yes (15.247) | Second board needed | 4 |
| 6 | SX126x/SX127x **FSK** on 902-928 MHz | $0 with a LoRa board | 0.3-2 km | up to ~300 kbit/s | Yes, but power depends on rule | LoRa board needed | 3 |
| 7 | CC1101, 902-928 MHz FSK | ~$3-19 | 0.2-0.5 km | 0.6-600 kbit/s | Yes, low power (15.249) | Module on SPI | 3 |
| 8 | Wi-Fi HaLow (802.11ah, 900 MHz) | ~$15 module + ESP32-S3, or ~$45 board | 1-3 km (16 km record) | ~1-30 Mbit/s | Yes (15.247) | **No** (ESP32-S3 or Pi) | 3 |
| 9 | BLE Coded PHY (long-range Bluetooth) | ~$4-8 (ESP32-C3/S3/C6) | ~200-800 m (unverified) | 125/500 kbit/s | Yes | **No**, not on classic ESP32 | 3 |
| 10 | MURS (151-154 MHz) + certified MURS radio + sound-card AFSK | ~$40-80/radio | 2-8 km | 1200 baud | Data yes (95.2731), **relaying no** (95.2733) | Uses the PC sound-card path | 3 |
| 11 | nRF24L01+ (PA+LNA) | ~$1-8 | 100-500 m | 250k-2 Mbit/s | Murky | Module on SPI | 2 |
| 12 | ESP-WIFI-MESH | $0 | Wi-Fi | Mbit/s | Yes | Yes | 2 |
| 13 | FRS / GMRS data | - | - | 1-s bursts only | **No** for Gorb | - | 1 |
| 14 | CB (27 MHz) data | - | - | - | **No** (voice only) | - | 1 |

**EU in short:**
* The 2.4 GHz options are fine under EN 300 328 at up to 100 mW EIRP.
* 868 MHz replaces 915, with duty-cycle limits (1% or 10%).
* 433 MHz allows 10 mW at a 10% duty cycle.
* 5.8 GHz outdoor links and HaLow vary by country.
* MURS, FRS and CB are US-only rules.

## 2. Details

### 2.1 ESP-NOW in Wi-Fi Long Range mode: a free upgrade for your board

* **What it is:** Espressif's own Wi-Fi mode at 500 and 250 kbit/s. Reception is about 4 dB better than 802.11b, which Espressif says gives about 2-2.5x the distance in theory.
  [ESP-IDF Wi-Fi guide, "Long Range"](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/wifi-driver/overview.html)
* **Does a classic ESP32 support it?** Yes. LR works on "ESP32-series chips (excluding the ESP32-C2)".
* **Real tests:**
  * Espressif's outdoor test reached about 100% delivery up to 450 m and about 40% at 900 m in open fields. In forest it fell below 50% at 400 m. Throughput dropped from about 100 kbit/s to about 10 kbit/s with distance. [Espressif blog](https://developer.espressif.com/blog/esp-now-for-outdoor-applications/)
  * An independent test got about 320 m with normal ESP-NOW and about 500 m with LR. [atomic14](https://atomic14.com/videos/posts/oz0a7Ur7nko)
  * One user saw no gain at all. [esp32.com](https://esp32.com/viewtopic.php?p=115993)
  * So expect about 1.5-2.5x today's range, and test it yourself.
* **Legal:** it's the same certified module, so FCC 15.247 and EN 300 328 still apply as before.
* **How to turn it on:**
  * Call `esp_wifi_set_protocol(ifx, 11B|11G|11N|LR)`.
  * Set the ESP-NOW rate to `WIFI_PHY_RATE_LORA_250K` or `_500K` with `esp_now_set_peer_rate_config()`. `esp_wifi_config_espnow_rate()` is deprecated from ESP-IDF 5.3. [ESP-NOW API](https://docs.espressif.com/projects/esp-idf/en/v5.3.3/esp32c6/api-reference/network/esp_now.html)
  * Use a recent ESP-IDF, because older ones had an "invalid rate" bug.
* **Coexisting with the phone hotspot:**
  * An LR-enabled access point sends its beacons in LR, so phones can't see it. Keep the access point in normal 11b/g/n.
  * Likely plan: use `WIFI_AP_STA`, enable LR on the STA interface only, and send ESP-NOW broadcasts from the STA interface on channel 6. **(unverified)** Test it on two boards first.
* **Catch:** only LR-enabled ESP32s can receive LR frames. [esp32.com](https://esp32.com/viewtopic.php?p=151675) Every board needs LR on, or must send each packet twice while some boards are still being switched over.
* **Fit:** excellent. It keeps the same broadcast, 250-byte frames and relaying.

### 2.2 ESP-NOW v2

* Packets up to **1470 bytes**.
* v2 boards receive from both v1 and v2 boards. v1 boards cut off or drop v2 frames over 250 bytes. [ESP-NOW docs](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html), [FAQ](https://docs.espressif.com/projects/esp-faq/en/latest/application-solution/esp-now.html)
* Needs ESP-IDF 5.4.2+ (Arduino-ESP32 about 3.2.1+).
* Defaults: 1 Mbit/s, broadcast can't be encrypted, 20 peers maximum.
* **Fit:** keep 250-byte frames as the shared format, and use wide frames only where both ends support v2. At LR rates, small frames are safer anyway.

### 2.3 BLE Coded PHY

* **Not supported on the classic ESP32.** [Feature table](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/ble/ble-feature-support-status.html)
* Supported on the C3, S3, C6 and H2. [C3 table](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-guides/ble/ble-feature-support-status.html)
* Speed is 125 or 500 kbit/s, with roughly 4x normal BLE range (unverified).
* For your board, ESP-NOW LR gives most of this for free.

### 2.4 nRF24L01+

* About $1-8. Real range is about 300-500 m line-of-sight with the PA+LNA version.
  [ShillehTek manual](https://shillehtek.com/blogs/shillehtek-product-manuals/nrf24l01-pa-lna-wireless-transceiver-module-antenna-1100m-range-manual)
* **Packets max out at 32 bytes**, so one Gorb frame needs about 9 radio packets.
* Most of these modules are uncertified clones. Home-built devices fall under the 15.23 limits, and +20 dBm PA versions likely exceed 15.249 (my reading). [fccid.io](https://fccid.io/blog/2016/07/09/fcc-rules-home-built-transmitters-not-sale)
* **Fit:** poor.

### 2.5 Wi-Fi HaLow (802.11ah)

* About 1-3 km in typical use. Morse Micro demonstrated 3 km in San Francisco and 16 km at about 2 Mbit/s.
  [Tom's Hardware](https://www.tomshardware.com/networking/halow-wi-fi-has-now-been-tested-at-99-miles-new-wi-fi-world-record-is-a-near-5x-increase-over-previous-best), [eeNews](https://www.eenewseurope.com/en/halow-hits-record-16km-wifi-distance-at-900mhz)
* Hardware:
  * Seeed HaLow module for XIAO, $14.90, plus a XIAO ESP32S3. [CNX](https://www.cnx-software.com/2025/03/07/seeed-studio-wi-fi-halow-mini-pcie-module-for-raspberry-pi-add-on-board-for-xiao-boards/)
  * Heltec HT-HC32, about $45. [Heltec](https://heltec.org/shop)
* No classic ESP32 support.
* The EU only has narrow 863-868 MHz sub-bands for it.
* **Fit:** good, but expensive for now. One for later.

### 2.6 SX1280 2.4 GHz LoRa / FLRC

* LoRa up to about 200 kbit/s, FLRC up to **1.3 Mbit/s**. [Semtech SX1280](https://www.semtech.com/products/wireless-rf/24-ghz-transceivers/sx1280)
* Modules:
  * Ebyte E28-2G4M12S, about $4.75. [RobotShop](https://robotshop.com/products/ebyte-e28-2g4m12sx-sx1281-chip-lora-module-24ghz-rf-transceiver-module-loraflrcgfsk-various-modulation-methods)
  * E28-2G4M27S, 27 dBm. [LCSC](https://lcsc.com/product-detail/Wireless-Modules_Chengdu-Ebyte-Elec-Tech-E28-2G4M27S_C411312.html)
  * ExpressLRS drone receivers use the same chip family.
* Connects by SPI, and RadioLib supports it.
* **Legal:** 15.247 in the US, at bandwidths of 812 kHz or 1.6 MHz. In the EU the limit is 100 mW EIRP, so the 27 dBm version is too strong.
* **Fit:** good. The best module under $10: a "far" mode and a "fast" mode in one.

### 2.7 FSK on 902-928 MHz (CC1101 or the LoRa chip's FSK mode)

* **15.249:** 50 mV/m at 3 m, which is about **0.75 mW EIRP** (my calculation). Expect 100-500 m. [15.249](https://www.law.cornell.edu/cfr/text/47/15.249)
* **15.247:** up to 1 W, but only if the 6 dB bandwidth is at least 500 kHz (with at most 8 dBm per 3 kHz), or with frequency hopping. Gorb Small's 500 kHz LoRa setting was chosen to fit this rule. [15.247](https://www.law.cornell.edu/cfr/text/47/15.247)
* CC1101: 0.6-600 kbit/s, about $3-19. [TI](https://www.ti.com/product/cc1101), [M5Stack](https://docs.m5stack.com/en/products/sku/M146)
* SX126x FSK can run up to about 300 kbit/s with no new hardware if you already have a LoRa board.
* **Fit:** moderate. A "fast lane" FSK mode on LoRa boards would be a nice free extra.

### 2.8 MURS, FRS, GMRS, CB

* **MURS:**
  * Data is allowed: "voice, data or image signals". [95.2731](https://www.law.cornell.edu/cfr/text/47/95.2731)
  * **Relaying is banned, including "store-and-forward packet operation".** [95.2733](https://www.law.cornell.edu/cfr/text/47/95.2733)
  * Limits: 2 W, 11.25 or 20 kHz bandwidth, antenna height limits.
  * F2D (data on an audio tone over FM) is an allowed emission, so Gorb's AFSK 1200 fits. [95.2771](https://www.law.cornell.edu/cfr/text/47/95.2771)
  * Feeding data into the mic jack of a certified MURS radio seems fine: no rule I found bans it, but it isn't explicitly confirmed.
  * Ham handhelds (e.g. Baofeng) are not MURS-certified, so they can't be used on MURS.
* **FRS:** data only as 1-second location or short-message bursts, and no store-and-forward. [95.587](https://www.law.cornell.edu/cfr/text/47/95.587)
* **GMRS:** similar limits, plus it needs a license. [95.1787](https://www.law.cornell.edu/cfr/text/47/95.1787)
* **CB:** voice only. [95.931](https://www.law.cornell.edu/cfr/text/47/95.931), [95.971](https://www.law.cornell.edu/cfr/text/47/95.971)

### 2.9 5.8 GHz directional links

* Example: Ubiquiti NanoStation 5AC Loco, 13 dBi, about $58-70 each. You need two for one link. [Newegg](https://www.newegg.com/p/0ED-00EM-00004)
* US rule: 15.407 (U-NII-3). [15.407](https://www.law.cornell.edu/cfr/text/47/15.407) The EU is more restricted.
* **Fit:** excellent for one fixed long hop. It works like an Ethernet cable through the air and carries Gorb Wi-Fi unchanged.

### 2.10 Other findings

* **EU 869.40-869.65 MHz:** 500 mW at a 10% duty cycle. This is the best EU band for Gorb Small.
* **EU 433 MHz:** 10 mW at a 10% duty cycle, or 1 mW with no limit. In the US, 433 MHz is not a general data band.
  [CEPT tables](https://efis.cept.org/adhoc_grabber.jsp?annex=4), [ERC 70-03](https://docdb.cept.org/download/4444)
* **Meshtastic boards** (Heltec V3/V4 at about $18-30, T-Beam, RAK) are the same hardware class as Gorb Small. Second-hand ones are a cheap way to get LoRa. [atomic14](https://www.atomic14.com/esp32/boards/heltec-wifi-lora-32-v3/)
* **ESP-WIFI-MESH** builds a tree with leaf nodes that don't relay, so it's a poor fit compared with Gorb's own flooding. [Espressif](https://docs.espressif.com/projects/esp-idf/en/v5.0/esp32/api-guides/esp-wifi-mesh.html)

## 3. Top 3 recommendations

**(a) Best free upgrade: ESP-NOW Long Range mode on your existing ESP32.** It costs nothing and gives about 1.5-2.5x the range. Use `WIFI_AP_STA`, put LR on the STA interface only, use a 250 kbit/s rate, and use ESP-IDF 5.3+ (Arduino-ESP32 3.x). Test it on two boards first.

**(b) Best module under $10: SX1280 (Ebyte E28-2G4M12S, about $5).** It connects by SPI, it's legal license-free in both the US and EU at 12.5 dBm, and it has a far LoRa mode and a fast FLRC mode. Runner-up: a second-hand Meshtastic board (about $15-20) runs Gorb Small today.

**(c) Best long range: 5.8 GHz directional bridges (about $120-150 per link)** for fixed rooftop hops, with 915 MHz LoRa covering the area.

## 4. What NOT to do

1. Don't add amplifiers or high-gain antennas to certified modules.
2. No Gorb data on FRS, GMRS or CB.
3. On MURS, no relaying, and no ham handhelds.
4. No ham bands without a license, and never encryption on ham bands.
5. Don't put the ESP32 hotspot into LR-only mode: phones won't see it.
6. Keep every board on channel 6. LR frames are only heard by LR-enabled boards, and v1 boards drop v2 frames over 250 bytes.
7. In the EU: no US915 settings, no 27 dBm at 2.4 GHz, and respect the 868 MHz duty cycle.
8. Don't expect nRF24L01+ to replace ESP-NOW.
9. No BLE long range on a classic ESP32.
10. Don't sell or hand out home-built radios. The 15.23 exemption only covers 5 or fewer personal devices.
