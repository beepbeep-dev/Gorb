# G.O.R.B Small firmware (ESP32 + LoRa)

This firmware turns a cheap ESP32 LoRa board into a G.O.R.B Small radio.

* **Plugged into a computer (USB)**: it's the radio for `gorb browse/host/get --small ...`.
* **On its own (battery or phone charger)**: it relays G.O.R.B Small packets for
  everyone nearby (rule 2). Put one on a windowsill or a roof and it extends the network.

## Boards

| board | PlatformIO env | chip |
|---|---|---|
| Heltec WiFi LoRa 32 V3 | `heltec_v3` | SX1262 |
| LilyGo TTGO LoRa32 T3 v1.6.1 | `lilygo_t3` | SX1276 |
| LilyGo T-Beam (SX1276 version) | `tbeam` | SX1276 |
| any ESP32 + RFM95W module | `esp32_rfm95` | SX1276 |

Buy the frequency version that matches your region (868/915 MHz boards for
EU868/US915/AU915/IN865, 433 MHz boards for EU433). **Always attach the antenna
before powering the board on**: transmitting without one can damage the radio.

Wiring for `esp32_rfm95` (ESP32 pin → RFM95W pin): 18→SCK, 19→MISO, 23→MOSI,
5→NSS, 14→RESET, 2→DIO0, 4→DIO1, 3V3→3.3V, GND→GND.

## Flash it

Install [PlatformIO](https://platformio.org/install/cli), plug in the board, then:

```bash
cd firmware/gorb-small
```

```bash
pio run -e heltec_v3 -t upload
```

Replace `heltec_v3` with your board's env. Arduino IDE also works: rename
`src/main.cpp` to `gorb-small.ino`, install the RadioLib library, and edit the
board pins at the top of the file.

## Use it

```bash
gorb regions
```

```bash
gorb browse --small US915 --device /dev/ttyUSB0 --callsign my-station
```

```bash
gorb host --small US915 --device /dev/ttyUSB0 --callsign my-station --site cats.gorb=./site
```

To set up a board as a standalone relay, configure it once and then unplug it.
It remembers the settings:

```bash
gorb esp32 --device /dev/ttyUSB0 --small US915
```

On Windows the device is something like `COM3`; on a Mac, `/dev/cu.usbserial-...`.

## What it enforces by itself

* It refuses to transmit until a region is chosen, and only uses that region's frequency.
* It never transmits above the region's power limit or the chip's maximum.
* It listens before talking, using LoRa channel activity detection (rule 3).
* It enforces the duty-cycle limit (10% in Europe) with an airtime budget.
* It uses its own LoRa sync word, so G.O.R.B Small doesn't interfere with
  LoRaWAN or Meshtastic networks (rule 5).
* It relays each packet at most once, and skips the relay if two other
  stations already repeated it.

The legal limits are measured at the antenna (ERP/EIRP). A high-gain antenna
can push you over, so stick to the small antenna that came with the board
unless you've checked the rules.
