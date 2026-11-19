# G.O.R.B Small firmware (ESP32 + LoRa)

This firmware turns a cheap ESP32 LoRa board into a G.O.R.B Small radio.

**One board does everything. No computer and no second board are needed.**

* **Browser**: the board opens its own Wi-Fi hotspot. Join it with your phone
  and browse G.O.R.B. Pages travel over LoRa; the Wi-Fi only connects your
  phone to the board, and there is no internet on it.
* **Host**: type your pages into the board's "My site" editor. They're stored
  on the board and served to anyone who asks for your `.gorb` domain.
* **Relay**: it always repeats G.O.R.B Small packets for everyone nearby (rule 2),
  even with nobody connected. Put one on a windowsill or a roof to extend the network.
* **Gorb Wi-Fi**: it also talks straight to other G.O.R.B boards nearby over
  Wi-Fi (ESP-NOW), and to laptops on its hotspot running `gorb ... --wifi`.
  Packets pass between Wi-Fi and LoRa, so the board bridges the two networks.
  This works on a bare ESP32 with no LoRa module.
* **Radio for a computer** (optional): plugged in over USB it's the radio for
  `gorb browse/host/get --small ...`.

To browse you only need your one board, plus somebody in range (directly or
through relays) who hosts a site. You can always open your own site on your
own board.

## First start (phone only)

1. Flash the board (below), attach the antenna, power it from USB or a battery.
2. On your phone, join the Wi-Fi network **GORB-xxxx**. The password is **gorbradio**.
3. Open **http://192.168.4.1** (many phones open it automatically).
4. Go to **Settings**: choose your region, give your station a name, and set a
   new hotspot password. Without a region the board never transmits.
5. Optional: go to **My site**, pick a domain like `cats.gorb`, and write `index.html`.
6. Type a gorb:// address in the bar and tap **Tune in**.

Pictures: in **My site → Add a picture**, pick any photo. Your phone shrinks
it to about 6 KB before uploading, then you put it on a page with
`<img src="name.webp">`. Pages with pictures show up once their pictures
have arrived. Videos from computer hosts play with sound, as small H.264 clips.

Speeds: a 1 KB page takes a few seconds; pages hosted on a board are sent
uncompressed and capped at 32 KB.

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

**Easiest, no installs:** open https://espressif.github.io/esptool-js/ in
Chrome or Edge, connect the board, add the file for your board from
[`prebuilt/`](prebuilt/README.md) at address **0x0**, and click Program.

**Or build it yourself:**

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

## Use it with a computer (optional)

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
* It won't save, host or show pages that break rule 1. Pages are shown with
  scripts switched off.

The legal limits are measured at the antenna (ERP/EIRP). A high-gain antenna
can push you over, so stick to the small antenna that came with the board
unless you've checked the rules.
