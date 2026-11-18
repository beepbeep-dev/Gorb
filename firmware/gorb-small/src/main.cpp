// G.O.R.B Small firmware: turns an ESP32 + LoRa board into a G.O.R.B radio.
//
// Plugged into a computer over USB it is the radio for `gorb ... --small`.
// On its own (battery / USB charger) it relays G.O.R.B packets for everyone
// (rule 2), using the region it was last configured with.
//
// It follows the rules by itself, whatever the computer asks:
//  * only transmits on the license-free band preset for its region,
//    never above that region's power limit (rule 5: no jamming),
//  * listens before talking with LoRa channel activity detection (rule 3),
//  * enforces the region's duty cycle limit,
//  * refuses to transmit at all until a region has been chosen.
//
// Serial protocol (115200 baud, KISS framing, first byte = command):
//   PC -> board  0x00 TX     gorb packet
//                0x10 CONFIG region id, power dBm (int8), relay 0/1
//                0x11 PING
//                0x12 NAMES  comma separated names not to relay (ours)
//   board -> PC  0x01 RX     rssi int16 BE, snr*4 int8, gorb packet
//                0x02 TXDONE status (0 ok, 2 radio error, 3 no region)
//                0x03 HELLO  JSON status
//                0x04 LOG    text
//
// Arduino IDE users: rename this file gorb-small.ino, install RadioLib, and
// uncomment one board block below.

#include <Arduino.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SPI.h>

// ---- board selection when not using PlatformIO ---------------------------
#if !defined(GORB_SX1262) && !defined(GORB_SX1276)
// Heltec WiFi LoRa 32 V3:
#define GORB_SX1262
#define LORA_NSS 8
#define LORA_DIO1 14
#define LORA_RST 12
#define LORA_BUSY 13
#define LORA_SCK 9
#define LORA_MISO 11
#define LORA_MOSI 10
#define LORA_TCXO 1.8
#define LED_PIN 35
#endif

#ifndef LORA_TCXO
#define LORA_TCXO 1.6
#endif

#define FW_VERSION "0.1.0"
#define SYNC_WORD 0x47  // 'G' - G.O.R.B Small only hears G.O.R.B Small

// ---- radio -----------------------------------------------------------------
#if defined(GORB_SX1262)
SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY, SPI);
static const int8_t CHIP_MAX_DBM = 22;
static const char *CHIP = "SX1262";
#else
SX1276 radio = new Module(LORA_NSS, LORA_DIO0, LORA_RST, LORA_DIO1, SPI);
static const int8_t CHIP_MAX_DBM = 20;
static const char *CHIP = "SX1276";
#endif

// ---- license-free band presets (same table as gorb/small.py) -------------
struct Region {
  uint8_t id;
  const char *name;
  float freqMHz;
  float bwKHz;
  uint8_t sf;
  uint8_t cr;
  int8_t maxDbm;
  float duty;
};

static const Region REGIONS[] = {
    {1, "EU868", 869.525, 250, 8, 5, 27, 0.10},
    {2, "EU433", 433.875, 125, 8, 5, 10, 0.10},
    {3, "US915", 915.000, 500, 8, 5, 30, 0.50},
    {4, "AU915", 917.000, 500, 8, 5, 30, 0.50},
    {5, "IN865", 866.000, 250, 8, 5, 30, 0.50},
};

static const Region *findRegion(uint8_t id) {
  for (auto &r : REGIONS)
    if (r.id == id) return &r;
  return nullptr;
}

// ---- state -----------------------------------------------------------------
Preferences prefs;
static const Region *region = nullptr;
static int8_t powerDbm = 0;
static bool relayOn = true;
static bool radioOk = false;
static String localNames = ",";  // ",NAME1,NAME2," for easy searching

static volatile bool rxFlag = false;
#if defined(ESP8266) || defined(ESP32)
ICACHE_RAM_ATTR
#endif
static void onRadioIrq() { rxFlag = true; }

// Duty cycle: token bucket of allowed airtime (seconds), refilled at `duty`
// seconds per second, holding at most one hour's allowance.
static float airBudget = 0;
static uint32_t budgetStamp = 0;

static void refillBudget() {
  if (!region) return;
  uint32_t now = millis();
  airBudget += (now - budgetStamp) / 1000.0f * region->duty;
  budgetStamp = now;
  float cap = region->duty * 3600.0f;
  if (airBudget > cap) airBudget = cap;
}

// ---- duplicate detection (for relaying) ----------------------------------
struct Seen {
  uint32_t hash;
  uint8_t count;
};
static Seen seen[96];
static uint8_t seenNext = 0;

// FNV-1a over the packet, skipping the ttl and hops bytes (offsets 4, 5),
// which relays change.
static uint32_t packetHash(const uint8_t *p, size_t n) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < n; i++) {
    if (i == 4 || i == 5) continue;
    h ^= p[i];
    h *= 16777619u;
  }
  return h;
}

static uint8_t markSeen(uint32_t h) {  // returns times heard including now
  for (auto &s : seen)
    if (s.count && s.hash == h) {
      if (s.count < 255) s.count++;
      return s.count;
    }
  seen[seenNext] = {h, 1};
  seenNext = (seenNext + 1) % (sizeof(seen) / sizeof(seen[0]));
  return 1;
}

static uint8_t seenCount(uint32_t h) {
  for (auto &s : seen)
    if (s.count && s.hash == h) return s.count;
  return 0;
}

// ---- transmit queue --------------------------------------------------------
struct TxItem {
  bool used;
  bool fromPc;
  bool isRelay;
  uint32_t hash;
  uint32_t notBefore;
  uint8_t len;
  uint8_t data[255];
};
static TxItem txq[8];

static bool enqueue(const uint8_t *data, size_t len, bool fromPc, bool isRelay, uint32_t hash,
                    uint32_t delayMs) {
  if (len > 255) return false;
  for (auto &t : txq)
    if (!t.used) {
      t.used = true;
      t.fromPc = fromPc;
      t.isRelay = isRelay;
      t.hash = hash;
      t.notBefore = millis() + delayMs;
      t.len = len;
      memcpy(t.data, data, len);
      return true;
    }
  return false;
}

// ---- KISS serial -----------------------------------------------------------
static const uint8_t FEND = 0xC0, FESC = 0xDB, TFEND = 0xDC, TFESC = 0xDD;

static void kissSend(uint8_t cmd, const uint8_t *data, size_t len) {
  Serial.write(FEND);
  Serial.write(cmd);
  for (size_t i = 0; i < len; i++) {
    if (data[i] == FEND) {
      Serial.write(FESC);
      Serial.write(TFEND);
    } else if (data[i] == FESC) {
      Serial.write(FESC);
      Serial.write(TFESC);
    } else {
      Serial.write(data[i]);
    }
  }
  Serial.write(FEND);
}

static void sendTxDone(uint8_t status) { kissSend(0x02, &status, 1); }

static void sendLog(const String &s) { kissSend(0x04, (const uint8_t *)s.c_str(), s.length()); }

static void sendHello() {
  refillBudget();
  String j = "{\"fw\":\"gorb-small " FW_VERSION "\",\"chip\":\"";
  j += CHIP;
  j += "\",\"radio_ok\":";
  j += radioOk ? "true" : "false";
  j += ",\"region\":\"";
  j += region ? region->name : "none";
  j += "\",\"power_dbm\":";
  j += powerDbm;
  j += ",\"relay\":";
  j += relayOn ? "true" : "false";
  j += ",\"air_budget_s\":";
  j += String(airBudget, 1);
  j += "}";
  kissSend(0x03, (const uint8_t *)j.c_str(), j.length());
}

// ---- radio setup -----------------------------------------------------------
static bool applyRadio() {
  radioOk = false;
  if (!region) return false;
  int8_t p = powerDbm;
  if (p > region->maxDbm) p = region->maxDbm;
  if (p > CHIP_MAX_DBM) p = CHIP_MAX_DBM;
  powerDbm = p;
#if defined(GORB_SX1262)
  int st = radio.begin(region->freqMHz, region->bwKHz, region->sf, region->cr, SYNC_WORD, p, 8,
                       LORA_TCXO);
  if (st == RADIOLIB_ERR_NONE) radio.setDio2AsRfSwitch(true);
#else
  int st = radio.begin(region->freqMHz, region->bwKHz, region->sf, region->cr, SYNC_WORD, p, 8);
#endif
  if (st != RADIOLIB_ERR_NONE) {
    sendLog(String("radio init failed: ") + st);
    return false;
  }
  radio.setCRC(true);
  radio.setPacketReceivedAction(onRadioIrq);
  radio.startReceive();
  radioOk = true;
  return true;
}

static void loadConfig() {
  prefs.begin("gorb", true);
  region = findRegion(prefs.getUChar("region", 0));
  powerDbm = prefs.getChar("power", 14);
  relayOn = prefs.getBool("relay", true);
  prefs.end();
}

static void saveConfig() {
  prefs.begin("gorb", false);
  prefs.putUChar("region", region ? region->id : 0);
  prefs.putChar("power", powerDbm);
  prefs.putBool("relay", relayOn);
  prefs.end();
}

// ---- packet helpers --------------------------------------------------------
// Gorb packet: 'G''B' ver type ttl hops round msgid(4) seq(2) total(2)
//              srclen src dstlen dst payload
static bool isGorb(const uint8_t *p, size_t n) { return n >= 17 && p[0] == 'G' && p[1] == 'B'; }

static bool nameIsLocal(const uint8_t *s, uint8_t n) {
  if (!n) return false;
  String name;
  for (uint8_t i = 0; i < n; i++) name += (char)s[i];
  return localNames.indexOf("," + name + ",") >= 0;
}

static bool isOurs(const uint8_t *p, size_t n) {
  size_t i = 15;
  uint8_t sl = p[i];
  if (i + 1 + sl >= n) return false;
  const uint8_t *src = p + i + 1;
  i += 1 + sl;
  uint8_t dl = p[i];
  if (i + 1 + dl > n) return false;
  const uint8_t *dst = p + i + 1;
  return nameIsLocal(src, sl) || nameIsLocal(dst, dl);
}

static void handleRadioPacket() {
  rxFlag = false;
  uint8_t buf[256];
  size_t len = radio.getPacketLength();
  if (len == 0 || len > 255) {
    radio.startReceive();
    return;
  }
  int st = radio.readData(buf, len);
  int16_t rssi = (int16_t)radio.getRSSI();
  int8_t snr4 = (int8_t)(radio.getSNR() * 4);
  radio.startReceive();
  if (st != RADIOLIB_ERR_NONE || !isGorb(buf, len)) return;

  uint32_t h = packetHash(buf, len);
  if (markSeen(h) > 1) return;  // heard before (another relay repeated it)

#ifdef LED_PIN
  digitalWrite(LED_PIN, HIGH);
#endif
  uint8_t out[259];
  out[0] = rssi >> 8;
  out[1] = rssi & 0xFF;
  out[2] = (uint8_t)snr4;
  memcpy(out + 3, buf, len);
  kissSend(0x01, out, len + 3);

  // Rule 2: repeat it for stations that can't hear the sender.
  uint8_t ttl = buf[4];
  if (relayOn && ttl > 0 && !isOurs(buf, len)) {
    buf[4] = ttl - 1;
    buf[5] = buf[5] + 1;
    uint32_t air = radio.getTimeOnAir(len) / 1000;
    enqueue(buf, len, false, true, h, random(50, 400) + air / 2);
  }
#ifdef LED_PIN
  digitalWrite(LED_PIN, LOW);
#endif
}

static uint32_t nextTryAt = 0;

static void serviceTxQueue() {
  if (!radioOk || millis() < nextTryAt) return;
  TxItem *t = nullptr;
  for (auto &q : txq)
    if (q.used && (int32_t)(millis() - q.notBefore) >= 0) {
      t = &q;
      break;
    }
  if (!t) return;

  // A relay is pointless if two other stations already repeated it.
  if (t->isRelay && seenCount(t->hash) > 2) {
    t->used = false;
    return;
  }

  float air = radio.getTimeOnAir(t->len) / 1e6f;
  refillBudget();
  if (airBudget < air) {  // duty cycle used up; wait for it to refill
    nextTryAt = millis() + 250;
    return;
  }

  // Rule 3: listen before talking.
  int cad = radio.scanChannel();
  if (cad == RADIOLIB_LORA_DETECTED) {
    radio.startReceive();
    nextTryAt = millis() + random(100, 600);
    return;
  }

#ifdef LED_PIN
  digitalWrite(LED_PIN, HIGH);
#endif
  int st = radio.transmit(t->data, t->len);
#ifdef LED_PIN
  digitalWrite(LED_PIN, LOW);
#endif
  airBudget -= air;
  rxFlag = false;  // the IRQ fired for our own transmission
  radio.startReceive();
  if (t->fromPc) sendTxDone(st == RADIOLIB_ERR_NONE ? 0 : 2);
  t->used = false;
}

// ---- serial commands -------------------------------------------------------
static uint8_t kbuf[300];
static size_t klen = 0;
static bool kesc = false, kin = false;

static void handleCommand(uint8_t cmd, const uint8_t *d, size_t n) {
  switch (cmd) {
    case 0x00: {  // TX
      if (!region || !radioOk) {
        sendTxDone(3);
        return;
      }
      if (!isGorb(d, n) || n > 255) {
        sendTxDone(2);
        return;
      }
      uint32_t h = packetHash(d, n);
      markSeen(h);  // so we ignore relayed copies of our own packet
      if (!enqueue(d, n, true, false, h, 0)) sendTxDone(2);
      break;
    }
    case 0x10: {  // CONFIG
      if (n < 3) return;
      const Region *r = findRegion(d[0]);
      if (!r) {
        sendLog("unknown region");
        return;
      }
      int8_t want = (int8_t)d[1];
      if (want > r->maxDbm) want = r->maxDbm;
      if (want > CHIP_MAX_DBM) want = CHIP_MAX_DBM;
      bool changed = (r != region) || (powerDbm != want) || (relayOn != (d[2] != 0));
      region = r;
      powerDbm = want;
      relayOn = d[2] != 0;
      if (changed || !radioOk) {
        applyRadio();
        saveConfig();
        budgetStamp = millis();
      }
      break;
    }
    case 0x11:
      sendHello();
      break;
    case 0x12: {
      localNames = ",";
      for (size_t i = 0; i < n; i++) localNames += (char)d[i];
      localNames += ",";
      break;
    }
  }
}

static void readSerial() {
  while (Serial.available()) {
    uint8_t b = Serial.read();
    if (b == FEND) {
      if (kin && klen) handleCommand(kbuf[0], kbuf + 1, klen - 1);
      klen = 0;
      kin = true;
      kesc = false;
    } else if (!kin) {
      continue;
    } else if (kesc) {
      if (klen < sizeof(kbuf)) kbuf[klen++] = b == TFEND ? FEND : b == TFESC ? FESC : b;
      kesc = false;
    } else if (b == FESC) {
      kesc = true;
    } else if (klen < sizeof(kbuf)) {
      kbuf[klen++] = b;
    }
  }
}

// ---- main ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
#ifdef LED_PIN
  pinMode(LED_PIN, OUTPUT);
#endif
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  randomSeed(esp_random());
  loadConfig();
  budgetStamp = millis();
  if (region) applyRadio();  // standalone relay picks up where it left off
  sendHello();
}

void loop() {
  readSerial();
  if (rxFlag) handleRadioPacket();
  serviceTxQueue();
  delay(1);
}
