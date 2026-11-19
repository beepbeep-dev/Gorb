// G.O.R.B Small firmware: turns an ESP32 + LoRa board into a G.O.R.B station.
//
// One board can do everything on its own:
//  * Browser: it opens a Wi-Fi hotspot ("GORB-<name>"). Join it with your
//    phone and open http://192.168.4.1. Pages you ask for travel over LoRa;
//    the Wi-Fi only links your phone to the board (no internet).
//  * Host: write pages in the "My site" editor; they are stored on the board
//    and served to anyone who asks for your .gorb domain.
//  * Relay: repeats G.O.R.B packets for others (rule 2), even with no
//    phone or computer around.
//  * Radio for a computer: plugged in over USB it is the radio for
//    `gorb ... --small` on the computer.
//
// It follows the rules by itself:
//  * only transmits on the license-free band preset for its region, never
//    above that region's power limit (rule 5: no jamming),
//  * listens before talking with LoRa channel activity detection (rule 3),
//  * enforces the region's duty-cycle limit,
//  * refuses to transmit until a region has been chosen,
//  * won't host or show pages that break rule 1.
//
// Serial protocol with a computer (115200 baud, KISS framing):
//   PC -> board  0x00 TX     gorb packet
//                0x10 CONFIG region id, power dBm (int8), relay 0/1
//                0x11 PING
//                0x12 NAMES  comma separated names not to relay (the PC's)
//   board -> PC  0x01 RX     rssi int16 BE, snr*4 int8, gorb packet
//                0x02 TXDONE status (0 ok, 2 radio error, 3 no region)
//                0x03 HELLO  JSON status
//                0x04 LOG    text
//
// Arduino IDE: rename this file gorb-small.ino, keep gorb_core.h and
// web_pages.h next to it, install RadioLib, and set the pins below.

#include <Arduino.h>
#include <DNSServer.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>

#include "gorb_core.h"
#include "web_pages.h"

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

#define FW_VERSION "0.2.0"
#define SYNC_WORD 0x47  // 'G' - G.O.R.B Small only hears G.O.R.B Small
#define DEFAULT_WIFI_PASS "gorbradio"
#define MAX_BODY (32 * 1024)  // biggest page this board will show or host
#define BEACON_MS (10UL * 60 * 1000)

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
  const char *note;
};

static const Region REGIONS[] = {
    {1, "EU868", 869.525, 250, 8, 5, 27, 0.10, "Europe, 500 mW, 10% duty cycle"},
    {2, "EU433", 433.875, 125, 8, 5, 10, 0.10, "Europe 433 MHz, 10 mW, 10% duty cycle"},
    {3, "US915", 915.000, 500, 8, 5, 30, 0.50, "US / Canada"},
    {4, "AU915", 917.000, 500, 8, 5, 30, 0.50, "Australia / New Zealand"},
    {5, "IN865", 866.000, 250, 8, 5, 30, 0.50, "India"},
};

static const Region *findRegion(uint8_t id) {
  for (auto &r : REGIONS)
    if (r.id == id) return &r;
  return nullptr;
}

// ---- settings --------------------------------------------------------------
Preferences prefs;
static const Region *region = nullptr;
static int8_t powerDbm = 14;
static bool relayOn = true;
static bool radioOk = false;
static std::string stationName;  // upper case, like the PC software
static std::string myDomain;     // e.g. "cats.gorb", empty = not hosting
static String wifiPass = DEFAULT_WIFI_PASS;
static String pcNames = ",";  // names of the computer's station, ",A,B,"

static uint32_t statRx = 0, statRelayed = 0, statServed = 0;
static int16_t lastRssi = 0;

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

static void resetBudget() {
  budgetStamp = millis();
  airBudget = region ? region->duty * 360.0f : 0;  // start with 6 minutes' worth
}

// ---- duplicate detection ---------------------------------------------------
struct Seen {
  uint32_t hash;
  uint8_t count;
};
static Seen seen[128];
static uint8_t seenNext = 0;

// FNV-1a over the packet, skipping ttl and hops (offsets 4, 5), which relays change.
static uint32_t packetHash(const uint8_t *p, size_t n) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < n; i++) {
    if (i == 4 || i == 5) continue;
    h ^= p[i];
    h *= 16777619u;
  }
  return h;
}

static uint8_t markSeen(uint32_t h) {
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
static const int TXQ_LEN = 12;
static TxItem txq[TXQ_LEN];

static int txqFree() {
  int n = 0;
  for (auto &t : txq)
    if (!t.used) n++;
  return n;
}

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

// Queue a packet made by the board's own station.
static bool sendOwn(const gorb::Packet &pk) {
  if (!radioOk) return false;
  auto bytes = pk.encode();
  if (bytes.size() > gorb::MAX_FRAME) return false;
  uint32_t h = packetHash(bytes.data(), bytes.size());
  markSeen(h);
  return enqueue(bytes.data(), bytes.size(), false, false, h, 0);
}

static float airtimeSec(size_t len) { return radioOk ? radio.getTimeOnAir(len) / 1e6f : 0.5f; }

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

static std::string hotspotName();
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
  j += ",\"station\":\"";
  j += stationName.c_str();
  j += "\",\"domain\":\"";
  j += myDomain.c_str();
  j += "\",\"wifi\":\"";
  j += hotspotName().c_str();
  j += "\",\"air_budget_s\":";
  j += String(airBudget, 1);
  j += "}";
  kissSend(0x03, (const uint8_t *)j.c_str(), j.length());
}

// ---- radio setup -----------------------------------------------------------
static int8_t clampPower(const Region *r, int8_t p) {
  if (p > r->maxDbm) p = r->maxDbm;
  if (p > CHIP_MAX_DBM) p = CHIP_MAX_DBM;
  if (p < -9) p = -9;
  return p;
}

static bool applyRadio() {
  radioOk = false;
  if (!region) return false;
  powerDbm = clampPower(region, powerDbm);
#if defined(GORB_SX1262)
  int st = radio.begin(region->freqMHz, region->bwKHz, region->sf, region->cr, SYNC_WORD, powerDbm,
                       8, LORA_TCXO);
  if (st == RADIOLIB_ERR_NONE) radio.setDio2AsRfSwitch(true);
#else
  int st =
      radio.begin(region->freqMHz, region->bwKHz, region->sf, region->cr, SYNC_WORD, powerDbm, 8);
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

static std::string upper(std::string s) {
  for (auto &c : s)
    if (c >= 'a' && c <= 'z') c -= 32;
  return s;
}

static void loadConfig() {
  prefs.begin("gorb", true);
  region = findRegion(prefs.getUChar("region", 0));
  powerDbm = prefs.getChar("power", 14);
  relayOn = prefs.getBool("relay", true);
  stationName = prefs.getString("name", "").c_str();
  myDomain = prefs.getString("domain", "").c_str();
  wifiPass = prefs.getString("wifipass", DEFAULT_WIFI_PASS);
  prefs.end();
  if (!gorb::validName(stationName)) {
    char buf[16];
    snprintf(buf, sizeof(buf), "GORB-%04X", (unsigned)(ESP.getEfuseMac() & 0xFFFF));
    stationName = buf;
  }
  stationName = upper(stationName);
  if (!myDomain.empty() && !gorb::validDomain(myDomain)) myDomain.clear();
  if (wifiPass.length() < 8) wifiPass = DEFAULT_WIFI_PASS;
}

static void saveConfig() {
  prefs.begin("gorb", false);
  prefs.putUChar("region", region ? region->id : 0);
  prefs.putChar("power", powerDbm);
  prefs.putBool("relay", relayOn);
  prefs.putString("name", stationName.c_str());
  prefs.putString("domain", myDomain.c_str());
  prefs.putString("wifipass", wifiPass);
  prefs.end();
}

// Wi-Fi hotspot name: "GORB-<station>" (without doubling a GORB- prefix).
static std::string hotspotName() {
  return stationName.compare(0, 5, "GORB-") == 0 ? stationName : "GORB-" + stationName;
}

// ---- who is "us" (not to be relayed) -------------------------------------
static bool isLocalName(const std::string &n) {
  if (n.empty()) return false;
  if (n == stationName || (!myDomain.empty() && n == myDomain)) return true;
  return pcNames.indexOf((",") + String(n.c_str()) + ",") >= 0;
}

// ==========================================================================
// Directory of stations heard (from beacons)
// ==========================================================================
struct DirEntry {
  std::string domain, station;
  uint8_t hops;
  uint32_t heardAt;
};
static DirEntry directory[16];

static void dirAdd(const std::string &domain, const std::string &station, uint8_t hops) {
  DirEntry *slot = nullptr;
  for (auto &d : directory)
    if (d.domain == domain) slot = &d;
  if (!slot)
    for (auto &d : directory)
      if (d.domain.empty()) {
        slot = &d;
        break;
      }
  if (!slot) {  // replace the oldest
    slot = &directory[0];
    for (auto &d : directory)
      if (d.heardAt < slot->heardAt) slot = &d;
  }
  *slot = {domain, station, hops, millis()};
}

static void handleBeacon(const gorb::Packet &pk) {
  std::string j(pk.payload.begin(), pk.payload.end());
  size_t p = j.find("\"domains\"");
  if (p == std::string::npos) return;
  size_t end = j.find(']', p);
  p = j.find('[', p);
  while (p != std::string::npos && p < end) {
    size_t a = j.find('"', p + 1);
    if (a == std::string::npos || a > end) break;
    size_t b = j.find('"', a + 1);
    if (b == std::string::npos || b > end) break;
    std::string d = j.substr(a + 1, b - a - 1);
    if (gorb::validDomain(d)) dirAdd(d, pk.src, pk.hops);
    p = b;
  }
}

static uint32_t lastBeacon = 0;
static bool beaconSoon = false;

static void sendBeacon() {
  gorb::Packet pk;
  pk.type = gorb::BEACON;
  pk.src = stationName;
  pk.dst = "*";
  pk.msgId = esp_random();
  std::string j = "{\"call\": \"" + stationName + "\", \"domains\": [";
  if (!myDomain.empty()) j += "\"" + myDomain + "\"";
  j += "]}";
  pk.payload.assign(j.begin(), j.end());
  if (sendOwn(pk)) {
    lastBeacon = millis();
    beaconSoon = false;
  }
}

// ==========================================================================
// Hosting: serve pages stored on the board (LittleFS /site/...)
// ==========================================================================
static bool safeFileName(const std::string &f) {
  if (f.empty() || f.size() > 40 || f[0] == '.') return false;
  for (char c : f)
    if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_'))
      return false;
  return f.find("..") == std::string::npos;
}

static const char *contentType(const std::string &f) {
  auto ends = [&](const char *s) {
    size_t n = strlen(s);
    return f.size() >= n && f.compare(f.size() - n, n, s) == 0;
  };
  if (ends(".html") || ends(".htm")) return "text/html";
  if (ends(".txt")) return "text/plain";
  if (ends(".css")) return "text/css";
  if (ends(".svg")) return "image/svg+xml";
  if (ends(".webp")) return "image/webp";
  if (ends(".jpg") || ends(".jpeg")) return "image/jpeg";
  if (ends(".png")) return "image/png";
  if (ends(".gif")) return "image/gif";
  return "application/octet-stream";
}

static bool readFile(const std::string &name, std::string &out) {
  File f = LittleFS.open(("/site/" + name).c_str(), "r");
  if (!f || f.isDirectory() || f.size() > MAX_BODY) return false;
  out.resize(f.size());
  size_t got = f.read((uint8_t *)&out[0], out.size());
  f.close();
  return got == out.size();
}

// Build the same response a PC host would: zlib( {"status":..,"type":..} \n body )
static void buildResponse(std::string path, int &status, std::string &ctype, std::string &body) {
  size_t cut = path.find_first_of("?#");
  if (cut != std::string::npos) path.resize(cut);
  while (!path.empty() && path[0] == '/') path.erase(0, 1);
  if (path.empty()) path = "index.html";
  if (!safeFileName(path) || !readFile(path, body)) {
    status = 404;
    ctype = "text/html";
    body = "<h1>404 - not on this frequency</h1><p>" + gorb::htmlEscape(myDomain + "/" + path) +
           " was not found.</p>";
    return;
  }
  status = 200;
  ctype = contentType(path);
  if (ctype.compare(0, 4, "text") == 0 || ctype == "image/svg+xml") {
    if (const char *term = gorb::checkContent(body)) {
      status = 451;
      ctype = "text/html";
      body = std::string("<h1>Blocked</h1><p>This page breaks the G.O.R.B rules (rule 1: '") +
             term + "').</p>";
    }
  }
}

struct HostJob {
  bool used = false;
  std::string requester;
  uint32_t msgId = 0;
  uint8_t round = 0, ttl = 3;
  std::vector<uint8_t> blob;
  size_t chunk = 0;
  uint16_t total = 0;
  std::vector<uint16_t> todo;
  uint32_t lastUsed = 0;
};
static HostJob jobs[2];

static void hostRequest(const gorb::Packet &pk) {
  HostJob *job = nullptr;
  for (auto &j : jobs)
    if (j.used && j.requester == pk.src && j.msgId == pk.msgId) job = &j;

  if (!job) {
    if (pk.type == gorb::RESEND) return;  // we don't remember this one
    for (auto &j : jobs)
      if (!j.used || (j.todo.empty() && millis() - j.lastUsed > 60000)) {
        job = &j;
        break;
      }
    if (!job) return;  // busy; the requester will ask again
    int status;
    std::string ctype, body;
    std::string path(pk.payload.begin(), pk.payload.end());
    buildResponse(path, status, ctype, body);
    std::string raw = "{\"status\": " + std::to_string(status) + ", \"type\": \"" + ctype +
                      "\"}\n" + body;
    *job = HostJob();
    job->used = true;
    job->requester = pk.src;
    job->msgId = pk.msgId;
    job->blob = gorb::zlibStore(raw);
    gorb::Packet probe;
    probe.src = myDomain;
    probe.dst = pk.src;
    job->chunk = std::min<size_t>(180, gorb::MAX_FRAME - probe.overhead());
    job->total = (job->blob.size() + job->chunk - 1) / job->chunk;
    statServed++;
  }
  job->round = pk.round;
  job->ttl = std::max<uint8_t>(3, pk.hops + 1);
  job->lastUsed = millis();
  job->todo.clear();
  if (pk.type == gorb::GET) {
    for (uint16_t s = 0; s < job->total; s++) job->todo.push_back(s);
  } else {
    std::string list(pk.payload.begin(), pk.payload.end());
    size_t p = 0;
    while (p < list.size()) {
      size_t c = list.find(',', p);
      if (c == std::string::npos) c = list.size();
      if (c > p) {
        int s = atoi(list.substr(p, c - p).c_str());
        if (s >= 0 && s < job->total) job->todo.push_back((uint16_t)s);
      }
      p = c + 1;
    }
  }
}

static void serviceHost() {
  for (auto &j : jobs) {
    if (!j.used || j.todo.empty()) continue;
    if (txqFree() <= 3) return;  // leave room for relays and our own requests
    uint16_t seq = j.todo.front();
    gorb::Packet pk;
    pk.type = gorb::DATA;
    pk.src = myDomain;
    pk.dst = j.requester;
    pk.msgId = j.msgId;
    pk.seq = seq;
    pk.total = j.total;
    pk.ttl = j.ttl;
    pk.round = j.round;
    size_t off = seq * j.chunk;
    size_t len = std::min(j.chunk, j.blob.size() - off);
    pk.payload.assign(j.blob.begin() + off, j.blob.begin() + off + len);
    if (sendOwn(pk)) {
      j.todo.erase(j.todo.begin());
      j.lastUsed = millis();
    }
    return;
  }
}

// ==========================================================================
// Browsing: fetch pages over the air
// ==========================================================================
struct CachedPage {
  std::string url, ctype, body;
  int status = 0;
  uint8_t hops = 0;
  float secs = 0;
  uint32_t at = 0;
};
static CachedPage cache[2];
static uint8_t cacheNext = 0;

// Pictures (Gorb Pixels / Gorb Motion) get their own cache, capped in bytes
// so a picture-heavy page can't run the board out of memory.
static const size_t MEDIA_CACHE_BYTES = 48 * 1024;
static std::vector<CachedPage> mediaCache;

static void mediaPut(CachedPage &&p) {
  for (auto it = mediaCache.begin(); it != mediaCache.end(); ++it)
    if (it->url == p.url) {
      mediaCache.erase(it);
      break;
    }
  size_t total = p.body.size();
  for (auto &m : mediaCache) total += m.body.size();
  while (total > MEDIA_CACHE_BYTES && !mediaCache.empty()) {  // drop the oldest
    total -= mediaCache.front().body.size();
    mediaCache.erase(mediaCache.begin());
  }
  mediaCache.push_back(std::move(p));
}

static CachedPage *cacheFind(const std::string &url) {
  for (auto &c : cache)
    if (!c.url.empty() && c.url == url && millis() - c.at < 300000) return &c;
  for (auto &c : mediaCache)
    if (c.url == url && millis() - c.at < 300000) return &c;
  return nullptr;
}

static bool isPicture(const std::string &ctype) {
  return ctype.compare(0, 6, "image/") == 0 && ctype != "image/svg+xml";
}

// After a page arrives, its pictures are fetched one by one before the page
// is shown, so it appears complete.
static std::string mediaFor;  // page whose pictures are loading
static std::vector<std::string> mediaQueue;
static std::string mediaInFlight;
static size_t mediaTotal = 0, mediaDone = 0;
static std::vector<std::string> mediaFailed;

static void queueMedia(const std::string &url, const std::string &html) {
  std::string domain, path;
  if (!gorb::parseUrl(url, domain, path)) return;
  mediaFor = url;
  mediaQueue.clear();
  mediaInFlight.clear();
  mediaFailed.clear();
  mediaDone = 0;
  for (auto &src : gorb::findImages(gorb::videoToImg(html))) {
    std::string u = gorb::resolveUrl(domain, path, src);
    if (u.empty() || cacheFind(u)) continue;
    bool dup = false;
    for (auto &q : mediaQueue) dup |= q == u;
    if (!dup && mediaQueue.size() < 6) mediaQueue.push_back(u);
  }
  mediaTotal = mediaQueue.size();
}

static bool mediaPending(const std::string &pageUrl) {
  return mediaFor == pageUrl && (!mediaQueue.empty() || !mediaInFlight.empty());
}

static void cachePut(CachedPage &&p) {
  for (auto &c : cache)
    if (c.url == p.url) {
      c = std::move(p);
      return;
    }
  cache[cacheNext] = std::move(p);
  cacheNext = (cacheNext + 1) % 2;
}

struct Fetch {
  bool active = false;
  std::string url, domain, path, error;
  uint32_t msgId = 0;
  uint8_t round = 0, hops = 0;
  uint16_t total = 0, have = 0;
  std::vector<std::string> chunks;
  std::vector<bool> got;
  uint32_t started = 0, lastAct = 0;
  bool getQueued = false;
};
static Fetch fetchState;
static std::string lastErrorUrl, lastError;

static void fetchFail(const std::string &why) {
  lastErrorUrl = fetchState.url;
  lastError = why;
  fetchState = Fetch();
}

static void finishPage(const std::string &url, int status, std::string ctype, std::string body,
                       uint8_t hops, float secs) {
  if (ctype.compare(0, 4, "text") == 0 || ctype == "image/svg+xml") {
    if (const char *term = gorb::checkContent(body)) {
      status = 451;
      ctype = "text/html";
      body = std::string("<h1>Blocked</h1><p>G.O.R.B hid this page (rule 1: '") + term + "').</p>";
    }
  }
  CachedPage p;
  p.url = url;
  p.status = status;
  p.ctype = ctype;
  p.body = std::move(body);
  p.hops = hops;
  p.secs = secs;
  p.at = millis();
  if (isPicture(p.ctype)) {
    if (p.status < 400) mediaPut(std::move(p));
    return;
  }
  if (p.ctype == "text/html" && p.status < 400 && url != mediaInFlight) queueMedia(url, p.body);
  cachePut(std::move(p));
}

static void sendGet() {
  gorb::Packet pk;
  pk.type = gorb::GET;
  pk.src = stationName;
  pk.dst = fetchState.domain;
  pk.msgId = fetchState.msgId;
  pk.round = fetchState.round;
  pk.payload.assign(fetchState.path.begin(), fetchState.path.end());
  fetchState.getQueued = sendOwn(pk);
}

static void startFetch(const std::string &url, const std::string &domain, const std::string &path) {
  if (domain == myDomain && !myDomain.empty()) {  // our own site: no radio needed
    int status;
    std::string ctype, body;
    buildResponse(path, status, ctype, body);
    finishPage(url, status, ctype, std::move(body), 0, 0);
    return;
  }
  fetchState = Fetch();
  fetchState.active = true;
  fetchState.url = url;
  fetchState.domain = domain;
  fetchState.path = path;
  fetchState.msgId = esp_random();
  fetchState.started = fetchState.lastAct = millis();
  lastErrorUrl.clear();
  if (!radioOk) {
    fetchFail(region ? "the radio did not start - check the board and antenna"
                     : "choose your region in Settings first");
    return;
  }
  sendGet();
}

static void completeFetch() {
  std::string blob;
  for (auto &c : fetchState.chunks) blob += c;
  std::string raw;
  if (!gorb::Inflater::zlibDecompress((const uint8_t *)blob.data(), blob.size(), raw,
                                      MAX_BODY + 256)) {
    fetchFail("the page arrived garbled, or is too big for this board");
    return;
  }
  size_t nl = raw.find('\n');
  std::string head = raw.substr(0, nl == std::string::npos ? 0 : nl);
  int status = 200;
  std::string ctype = "text/html";
  size_t p = head.find("\"status\"");
  if (p != std::string::npos) status = atoi(head.c_str() + head.find(':', p) + 1);
  p = head.find("\"type\"");
  if (p != std::string::npos) {
    size_t a = head.find('"', head.find(':', p));
    size_t b = head.find('"', a + 1);
    if (a != std::string::npos && b != std::string::npos) ctype = head.substr(a + 1, b - a - 1);
  }
  finishPage(fetchState.url, status, ctype, nl == std::string::npos ? "" : raw.substr(nl + 1),
             fetchState.hops, (millis() - fetchState.started) / 1000.0f);
  fetchState = Fetch();
}

static void fetchReceive(const gorb::Packet &pk) {
  if (!fetchState.active || pk.msgId != fetchState.msgId || pk.src != fetchState.domain) return;
  fetchState.lastAct = millis();
  if (pk.hops > fetchState.hops) fetchState.hops = pk.hops;
  if (pk.type == gorb::ERROR_PKT) {
    fetchFail(std::string(pk.payload.begin(), pk.payload.end()));
    return;
  }
  if (pk.total == 0 || pk.total > 2048 || pk.seq >= pk.total) return;
  if (fetchState.total != pk.total) {
    fetchState.total = pk.total;
    fetchState.chunks.assign(pk.total, "");
    fetchState.got.assign(pk.total, false);
    fetchState.have = 0;
  }
  if (!fetchState.got[pk.seq]) {
    fetchState.got[pk.seq] = true;
    fetchState.chunks[pk.seq].assign(pk.payload.begin(), pk.payload.end());
    fetchState.have++;
  }
  if (fetchState.have == fetchState.total) completeFetch();
}

static void serviceFetch() {
  if (!fetchState.active) return;
  if (!fetchState.getQueued && fetchState.total == 0) {
    sendGet();
    return;
  }
  uint32_t now = millis();
  if (now - fetchState.started > 180000) {
    fetchFail("timed out");
    return;
  }
  // How long to wait for the next chunk: the host may be pausing to keep
  // within its duty cycle, and relays add delay.
  float air = airtimeSec(255);
  uint32_t idle = 3000 + (uint32_t)(1000 * 4 * air / (region ? region->duty : 0.5f));
  if (now - fetchState.lastAct < idle) return;
  fetchState.round++;
  fetchState.lastAct = now;
  if (fetchState.round > 6) {
    if (fetchState.total)
      fetchFail("lost signal: got " + std::to_string(fetchState.have) + "/" +
                std::to_string(fetchState.total) + " pieces");
    else
      fetchFail("no answer from " + fetchState.domain +
                " (nobody in range hosts it, or no relay can reach it)");
    return;
  }
  if (fetchState.total) {
    gorb::Packet pk;
    pk.type = gorb::RESEND;
    pk.src = stationName;
    pk.dst = fetchState.domain;
    pk.msgId = fetchState.msgId;
    pk.round = fetchState.round;
    size_t limit = std::min<size_t>(180, gorb::MAX_FRAME - pk.overhead());
    std::string list;
    for (uint16_t s = 0; s < fetchState.total; s++) {
      if (fetchState.got[s]) continue;
      std::string n = std::to_string(s) + ",";
      if (list.size() + n.size() > limit) break;
      list += n;
    }
    pk.payload.assign(list.begin(), list.end());
    sendOwn(pk);
  } else {
    sendGet();
  }
}

static void serviceMedia() {
  if (!mediaInFlight.empty()) {
    if (fetchState.active && fetchState.url == mediaInFlight) return;  // still coming
    if (!cacheFind(mediaInFlight)) mediaFailed.push_back(mediaInFlight);
    mediaInFlight.clear();
    mediaDone++;
  }
  if (mediaQueue.empty() || fetchState.active) return;
  std::string url = mediaQueue.front();
  mediaQueue.erase(mediaQueue.begin());
  std::string domain, path;
  if (!gorb::parseUrl(url, domain, path)) {
    mediaDone++;
    return;
  }
  mediaInFlight = url;
  startFetch(url, domain, path);
}

// ==========================================================================
// Radio receive + relay
// ==========================================================================
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
  gorb::Packet pk;
  if (st != RADIOLIB_ERR_NONE || !pk.decode(buf, len)) return;

  uint32_t h = packetHash(buf, len);
  if (markSeen(h) > 1) return;  // heard before (another relay repeated it)
  statRx++;
  lastRssi = rssi;

#ifdef LED_PIN
  digitalWrite(LED_PIN, HIGH);
#endif
  uint8_t out[259];
  out[0] = rssi >> 8;
  out[1] = rssi & 0xFF;
  out[2] = (uint8_t)snr4;
  memcpy(out + 3, buf, len);
  kissSend(0x01, out, len + 3);  // let a connected computer see it too

  // The board's own station.
  if (pk.type == gorb::BEACON) {
    handleBeacon(pk);
  } else if ((pk.type == gorb::GET || pk.type == gorb::RESEND) && !myDomain.empty() &&
             pk.dst == myDomain) {
    hostRequest(pk);
  } else if ((pk.type == gorb::DATA || pk.type == gorb::ERROR_PKT) && pk.dst == stationName) {
    fetchReceive(pk);
  }

  // Rule 2: repeat it for stations that can't hear the sender.
  if (relayOn && pk.ttl > 0 && !isLocalName(pk.src) && !isLocalName(pk.dst)) {
    buf[4] = pk.ttl - 1;
    buf[5] = pk.hops + 1;
    uint32_t air = radio.getTimeOnAir(len) / 1000;
    if (enqueue(buf, len, false, true, h, random(50, 400) + air / 2)) statRelayed++;
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

// ==========================================================================
// Serial commands from a computer
// ==========================================================================
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
      if (n < gorb::HEAD_LEN + 2 || d[0] != 'G' || d[1] != 'B' || n > 255) {
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
      int8_t want = clampPower(r, (int8_t)d[1]);
      bool changed = (r != region) || (powerDbm != want) || (relayOn != (d[2] != 0));
      region = r;
      powerDbm = want;
      relayOn = d[2] != 0;
      if (changed || !radioOk) {
        applyRadio();
        saveConfig();
        resetBudget();
      }
      break;
    }
    case 0x11:
      sendHello();
      break;
    case 0x12: {
      pcNames = ",";
      for (size_t i = 0; i < n; i++) pcNames += (char)d[i];
      pcNames += ",";
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

// ==========================================================================
// Built-in browser (Wi-Fi hotspot + web pages)
// ==========================================================================
WebServer server(80);
DNSServer dns;
static bool restartWifi = false;
static uint32_t restartWifiAt = 0;

static String S(const std::string &s) { return String(s.c_str()); }
static std::string esc(const std::string &s) { return gorb::htmlEscape(s); }

static std::string urlEncode(const std::string &s) {
  static const char *hex = "0123456789ABCDEF";
  std::string o;
  for (unsigned char c : s) {
    if (isalnum(c) || c == '-' || c == '.' || c == '_' || c == '~')
      o += (char)c;
    else {
      o += '%';
      o += hex[c >> 4];
      o += hex[c & 15];
    }
  }
  return o;
}

static std::string urlDecode(const std::string &s) {
  std::string o;
  for (size_t i = 0; i < s.size(); i++) {
    if (s[i] == '%' && i + 2 < s.size()) {
      o += (char)strtol(s.substr(i + 1, 2).c_str(), nullptr, 16);
      i += 2;
    } else {
      o += s[i] == '+' ? ' ' : s[i];
    }
  }
  return o;
}

static std::string topBar(const std::string &url) {
  std::string s = "<header><a class=\"logo\" href=\"/\">G.O.R.B</a>"
                  "<form class=\"bar\" action=\"/go\"><input name=\"url\" value=\"" +
                  esc(url) +
                  "\" placeholder=\"gorb://example.gorb\" autocapitalize=\"off\">"
                  "<button>Tune in</button></form><span class=\"meta\">" +
                  esc(stationName) + " &middot; " + (region ? region->name : "no region") +
                  "</span></header>";
  return s;
}

static void sendHtml(int code, const std::string &body) {
  std::string page = std::string(PAGE_HEAD) + body + PAGE_TAIL;
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(page.size());
  server.send(code, "text/html; charset=utf-8", "");
  server.sendContent(page.data(), page.size());
}

static void handleHome() {
  std::string b = topBar("") + "<div class=\"wrap\"><h1>Global Open Radio Browser</h1>";
  if (!region)
    b += "<div class=\"note\">Pick your region in <a href=\"/settings\">Settings</a> before "
         "this board can use the radio.</div>";
  b += "<p>Web pages carried by radio. Type a gorb:// address above.</p><h3>Stations heard</h3><ul>";
  bool any = false;
  uint32_t now = millis();
  for (auto &d : directory) {
    if (d.domain.empty()) continue;
    any = true;
    uint32_t mins = (now - d.heardAt) / 60000;
    b += "<li><a href=\"/go?url=" + urlEncode("gorb://" + d.domain) + "\">gorb://" +
         esc(d.domain) + "</a> <span class=\"meta\">" + esc(d.station) + ", " +
         std::to_string(d.hops) + " hop(s), " + std::to_string(mins) + " min ago</span></li>";
  }
  if (!myDomain.empty())
    b += "<li><a href=\"/go?url=" + urlEncode("gorb://" + myDomain) + "\">gorb://" +
         esc(myDomain) + "</a> <span class=\"meta\">your site, on this board</span></li>";
  if (!any && myDomain.empty())
    b += "<li class=\"meta\">Nothing heard yet. Stations announce themselves every 10 minutes.</li>";
  b += "</ul><p><a href=\"/site\">My site</a> &middot; <a href=\"/settings\">Settings</a></p>";
  b += "<p class=\"meta\">heard " + std::to_string(statRx) + " packets &middot; relayed " +
       std::to_string(statRelayed) + " &middot; served " + std::to_string(statServed) +
       " pages" + (statRx ? " &middot; last signal " + std::to_string(lastRssi) + " dBm" : "") +
       "</p>";
  b += "<h3>Rules</h3><ol><li>No not-safe-for-work, violent or adults-only content.</li>"
       "<li>If you use G.O.R.B you help: your radio relays signals for others.</li>"
       "<li>Treat others the way you want to be treated.</li><li>Vibecoded sites are accepted."
       "</li><li>No signal jammers.</li></ol></div>";
  sendHtml(200, b);
}

static void handleGo() {
  std::string url = server.arg("url").c_str();
  std::string domain, path;
  if (url.empty()) {
    server.sendHeader("Location", "/");
    server.send(302);
    return;
  }
  if (!gorb::parseUrl(url, domain, path)) {
    sendHtml(400, topBar(url) + "<div class=\"wrap\"><h2 class=\"err\">That isn't a gorb address."
                                "</h2><p>Try something like gorb://cats.gorb</p></div>");
    return;
  }
  std::string full = "gorb://" + domain + path;
  sendHtml(200, topBar(full) + "<main class=\"frame\"><iframe sandbox src=\"/g/" + domain + path +
                    "\"></iframe></main>");
}

// /g/<domain>/<path>: the page itself (shown inside the frame)
static void handlePage(const std::string &rest) {
  size_t slash = rest.find('/');
  std::string domain = rest.substr(0, slash);
  std::string path = slash == std::string::npos ? "/" : rest.substr(slash);
  std::string d2, p2;
  if (!gorb::parseUrl(domain + path, d2, p2)) {
    server.send(400, "text/plain", "bad address");
    return;
  }
  std::string url = "gorb://" + d2 + p2;
  bool retry = server.hasArg("retry");
  server.sendHeader("Content-Security-Policy", PAGE_CSP);
  server.sendHeader("Cache-Control", "no-store");

  CachedPage *page = retry ? nullptr : cacheFind(url);
  if (page && isPicture(page->ctype)) {  // a picture on a page (or opened directly)
    server.sendHeader("Cache-Control", "max-age=600", true);
    server.setContentLength(page->body.size());
    server.send(200, S(page->ctype), "");
    server.sendContent(page->body.data(), page->body.size());
    return;
  }
  if (!page) {
    for (auto &f : mediaFailed)
      if (f == url) {  // a picture that didn't make it: don't refetch it for every view
        server.send(404, "text/plain", "picture lost");
        return;
      }
  }
  if (page && mediaPending(url)) {  // page is here, its pictures are still arriving
    std::string b = "<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<meta http-equiv=\"refresh\" content=\"1; url=/g/" + d2 + p2 + "\">";
    b += TUNING_PAGE;
    b += "<div>page received, now the pictures</div><div>picture " +
         std::to_string(std::min(mediaDone + 1, mediaTotal)) + " of " + std::to_string(mediaTotal);
    if (fetchState.active && fetchState.total)
      b += ": " + std::to_string(fetchState.have) + "/" + std::to_string(fetchState.total) +
           " pieces";
    b += "</div></body></html>";
    server.send(200, "text/html; charset=utf-8", S(b));
    return;
  }
  if (page) {
    std::string body = page->ctype == "text/html"
                           ? gorb::rewriteLinks(gorb::videoToImg(page->body), d2)
                           : "<pre>" + esc(page->body) + "</pre>";
    char foot[200];
    snprintf(foot, sizeof(foot),
             "<div style=\"font:12px monospace;color:#777;border-top:1px solid #ddd;margin-top:"
             "24px;padding:6px\">received over radio &middot; %u bytes &middot; %u relay hop(s) "
             "&middot; %.1fs</div>",
             (unsigned)page->body.size(), page->hops, page->secs);
    body += foot;
    server.setContentLength(body.size());
    server.send(page->status < 400 ? 200 : page->status, "text/html; charset=utf-8", "");
    server.sendContent(body.data(), body.size());
    return;
  }

  if (!retry && lastErrorUrl == url) {
    std::string b = "<body style=\"font-family:monospace;padding:24px\"><h2>No signal</h2><p>" +
                    esc(lastError) + "</p><p><a href=\"/g/" + d2 + p2 +
                    "?retry=1\">Try again</a></p></body>";
    server.send(504, "text/html; charset=utf-8", S(b));
    return;
  }

  if (!fetchState.active || fetchState.url != url) {
    mediaQueue.clear();  // the user went somewhere else
    mediaInFlight.clear();
    mediaFor.clear();
    startFetch(url, d2, p2);
    if (CachedPage *now = cacheFind(url)) {  // own site: ready instantly
      (void)now;
      server.sendHeader("Location", S("/g/" + d2 + p2));
      server.send(302);
      return;
    }
  }
  std::string b = "<!doctype html><html><head><meta charset=\"utf-8\">"
                  "<meta http-equiv=\"refresh\" content=\"1; url=/g/" + d2 + p2 + "\">";
  b += TUNING_PAGE;
  b += "<div>tuning in to " + esc(url) + "</div><div>";
  if (fetchState.active && fetchState.total)
    b += "got " + std::to_string(fetchState.have) + " of " + std::to_string(fetchState.total) +
         " pieces";
  else if (fetchState.active && fetchState.round)
    b += "no answer yet, asking again (" + std::to_string(fetchState.round) + "/6)";
  else
    b += "radio is slow, hang tight";
  b += "</div></body></html>";
  server.send(200, "text/html; charset=utf-8", S(b));
}

static void handleSettings() {
  std::string msg;
  if (server.method() == HTTP_POST) {
    std::string name = upper(server.arg("name").c_str());
    const Region *r = findRegion(server.arg("region").toInt());
    String pass = server.arg("wifipass");
    if (!gorb::validName(name)) {
      msg = "<p class=\"err\">Station name must be 3-20 letters, numbers, - or /.</p>";
    } else if (pass.length() && pass.length() < 8) {
      msg = "<p class=\"err\">Wi-Fi password must be at least 8 characters.</p>";
    } else {
      bool nameChanged = name != stationName;
      stationName = name;
      region = r;
      powerDbm = r ? clampPower(r, server.arg("power").toInt()) : 14;
      relayOn = server.hasArg("relay");
      if (pass.length() && pass != wifiPass) {
        wifiPass = pass;
        restartWifi = true;
      }
      if (nameChanged) restartWifi = true;
      saveConfig();
      if (region) {
        applyRadio();
        resetBudget();
        beaconSoon = true;
      } else {
        radioOk = false;
      }
      restartWifiAt = millis() + 2000;
      msg = "<p>Saved.";
      if (restartWifi) msg += " The hotspot is restarting: reconnect to " + esc(hotspotName()) + ".";
      if (region && !radioOk) msg += " <span class=\"err\">But the radio did not start.</span>";
      msg += "</p>";
    }
  }
  std::string b = topBar("") + "<div class=\"wrap\"><h2>Settings</h2>" + msg +
                  "<form method=\"post\"><label>Station name (anything, no license needed)</label>"
                  "<input name=\"name\" value=\"" +
                  esc(stationName) + "\" maxlength=\"20\"><label>Region</label><select name=\"region\">"
                  "<option value=\"0\">choose your region</option>";
  for (auto &r : REGIONS) {
    b += "<option value=\"" + std::to_string(r.id) + "\"" + (region == &r ? " selected" : "") + ">" +
         r.name + " - " + r.note + "</option>";
  }
  b += "</select><label>Power (dBm, capped to the legal and chip limit)</label><input name=\"power\" "
       "type=\"number\" min=\"-9\" max=\"30\" value=\"" +
       std::to_string(powerDbm) +
       "\"><label><input type=\"checkbox\" name=\"relay\"" + (relayOn ? " checked" : "") +
       "> Relay for others (rule 2, please leave on)</label>"
       "<label>Hotspot password (leave empty to keep it)</label><input name=\"wifipass\" "
       "type=\"password\" minlength=\"8\"><p><button>Save</button></p></form>"
       "<p class=\"meta\">Firmware " FW_VERSION " &middot; " +
       std::string(CHIP) + " &middot; radio " + (radioOk ? "on" : "off") +
       "</p><div class=\"note\">Use the antenna that came with the board. A high-gain "
       "antenna can push you over the legal limit.</div></div>";
  sendHtml(200, b);
}

static void handleSite() {
  std::string msg;
  std::string editName = server.hasArg("edit") ? server.arg("edit").c_str() : "index.html";
  std::string editBody;
  if (server.method() == HTTP_POST) {
    std::string action = server.arg("action").c_str();
    if (action == "domain") {
      std::string d = server.arg("domain").c_str();
      for (auto &c : d) c = gorb::lower(c);
      if (!d.empty() && d.find(".gorb") == std::string::npos) d += ".gorb";
      if (d.empty() || gorb::validDomain(d)) {
        myDomain = d;
        saveConfig();
        beaconSoon = true;
        msg = "<p>Domain saved.</p>";
      } else {
        msg = "<p class=\"err\">Domains look like cats.gorb (letters, numbers and -).</p>";
      }
    } else if (action == "save") {
      editName = server.arg("file").c_str();
      editBody = server.arg("content").c_str();
      if (!safeFileName(editName)) {
        msg = "<p class=\"err\">File names: lowercase letters, numbers, - _ . only.</p>";
      } else if (editBody.size() > MAX_BODY) {
        msg = "<p class=\"err\">Too big for radio. Keep pages under 32 KB.</p>";
      } else if (const char *term = gorb::checkContent(editBody)) {
        msg = std::string("<p class=\"err\">Not saved: this breaks rule 1 ('") + term + "').</p>";
      } else {
        File f = LittleFS.open(("/site/" + editName).c_str(), "w");
        if (f) {
          f.write((const uint8_t *)editBody.data(), editBody.size());
          f.close();
          msg = "<p>Saved " + esc(editName) + ".</p>";
          for (auto &c : cache) c.url.clear();
        } else {
          msg = "<p class=\"err\">Could not save (storage full?).</p>";
        }
      }
    } else if (action == "delete") {
      std::string f = server.arg("file").c_str();
      if (safeFileName(f)) LittleFS.remove(("/site/" + f).c_str());
      msg = "<p>Deleted " + esc(f) + ".</p>";
      editName = "index.html";
    }
  }
  if (isPicture(contentType(editName))) editName = "index.html";
  if (editBody.empty()) readFile(editName, editBody);

  std::string b = topBar("") + "<div class=\"wrap\"><h2>My site</h2>" + msg +
                  "<form method=\"post\"><input type=\"hidden\" name=\"action\" value=\"domain\">"
                  "<label>Your domain (leave empty to not host)</label><input name=\"domain\" "
                  "value=\"" +
                  esc(myDomain) +
                  "\" placeholder=\"cats.gorb\" autocapitalize=\"off\"> <button>Save domain</button>"
                  "</form><h3>Pages on this board</h3><ul>";
  File dir = LittleFS.open("/site");
  bool any = false;
  if (dir && dir.isDirectory()) {
    for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
      std::string n = f.name();
      size_t s = n.rfind('/');
      if (s != std::string::npos) n = n.substr(s + 1);
      any = true;
      if (isPicture(contentType(n))) {  // pictures can't be edited as text
        b += "<li>" + esc(n) + " <span class=\"meta\">" + std::to_string(f.size()) +
             " bytes, use it as &lt;img src=&quot;" + esc(n) + "&quot;&gt;</span> "
             "<form method=\"post\" style=\"display:inline\"><input type=\"hidden\" name=\"action\" "
             "value=\"delete\"><input type=\"hidden\" name=\"file\" value=\"" + esc(n) +
             "\"><button class=\"ghost\">delete</button></form></li>";
      } else {
        b += "<li><a href=\"/site?edit=" + urlEncode(n) + "\">" + esc(n) +
             "</a> <span class=\"meta\">" + std::to_string(f.size()) + " bytes</span></li>";
      }
    }
  }
  if (!any) b += "<li class=\"meta\">No pages yet. Start with index.html below.</li>";
  b += "</ul><form method=\"post\"><input type=\"hidden\" name=\"action\" value=\"save\">"
       "<label>File name</label><input name=\"file\" value=\"" +
       esc(editName) +
       "\" autocapitalize=\"off\"><label>HTML (vibecoded welcome; no scripts, they won't run)</label>"
       "<textarea name=\"content\" spellcheck=\"false\">" +
       esc(editBody) +
       "</textarea><p><button>Save page</button></p></form>" + std::string(PICTURE_UPLOAD) +
       "<form method=\"post\" onsubmit=\"return confirm('Delete this page?')\">"
       "<input type=\"hidden\" name=\"action\" value=\"delete\"><input type=\"hidden\" name=\"file\" "
       "value=\"" +
       esc(editName) + "\"><button class=\"ghost\">Delete " + esc(editName) +
       "</button></form><p class=\"meta\">Pages are sent uncompressed from the board, so keep them "
       "small: about 1 KB loads in a few seconds.</p></div>";
  sendHtml(200, b);
}

// Picture upload (already shrunk by the phone, see PICTURE_UPLOAD).
static File uploadFile;
static std::string uploadName, uploadErr;
static size_t uploadSize = 0;

static void handleUploadChunk() {
  HTTPUpload &up = server.upload();
  if (up.status == UPLOAD_FILE_START) {
    uploadErr.clear();
    uploadSize = 0;
    uploadName = up.filename.c_str();
    for (auto &c : uploadName) c = gorb::lower(c);
    if (!safeFileName(uploadName) || !isPicture(contentType(uploadName))) {
      uploadErr = "Only .webp, .jpg, .png or .gif pictures with simple names.";
      return;
    }
    uploadFile = LittleFS.open(("/site/" + uploadName).c_str(), "w");
    if (!uploadFile) uploadErr = "Could not save (storage full?).";
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (!uploadErr.empty() || !uploadFile) return;
    uploadSize += up.currentSize;
    if (uploadSize > MAX_BODY) {
      uploadErr = "Too big for radio (over 32 KB). The page shrinks pictures before uploading; "
                  "if that failed, use 'gorb shrink' on a computer.";
      uploadFile.close();
      LittleFS.remove(("/site/" + uploadName).c_str());
      return;
    }
    uploadFile.write(up.buf, up.currentSize);
  } else if (up.status == UPLOAD_FILE_END || up.status == UPLOAD_FILE_ABORTED) {
    if (uploadFile) uploadFile.close();
  }
}

static void handleUploadDone() {
  if (!uploadErr.empty()) {
    server.send(400, "text/plain", S(uploadErr));
    return;
  }
  mediaCache.clear();
  server.send(200, "text/plain",
              S("Saved " + uploadName + " (" + std::to_string(uploadSize) +
                " bytes). Put it on a page with <img src=\"" + uploadName + "\">"));
}

static void handleNotFound() {
  std::string uri = server.uri().c_str();
  if (uri.compare(0, 3, "/g/") == 0) {
    handlePage(urlDecode(uri.substr(3)));
    return;
  }
  // Captive portal: send any other address to the home page.
  server.sendHeader("Location", "http://192.168.4.1/");
  server.send(302);
}

static void startWifi() {
  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1),
                    IPAddress(255, 255, 255, 0));
  WiFi.softAP(hotspotName().c_str(), wifiPass.c_str());
  dns.start(53, "*", IPAddress(192, 168, 4, 1));
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
  LittleFS.begin(true);
  if (!LittleFS.exists("/site")) LittleFS.mkdir("/site");
  if (region) {
    applyRadio();
    resetBudget();
  }

  startWifi();
  server.on("/", handleHome);
  server.on("/go", handleGo);
  server.on("/settings", handleSettings);
  server.on("/site", handleSite);
  server.on("/upload", HTTP_POST, handleUploadDone, handleUploadChunk);
  server.onNotFound(handleNotFound);
  server.begin();

  lastBeacon = millis() - BEACON_MS + random(3000, 8000);  // first beacon a few seconds in
  sendHello();
  sendLog(String("hotspot ") + hotspotName().c_str() + " password " + wifiPass);
}

void loop() {
  readSerial();
  dns.processNextRequest();
  server.handleClient();
  if (rxFlag) handleRadioPacket();
  serviceFetch();
  serviceMedia();
  serviceHost();
  if (radioOk && (beaconSoon || millis() - lastBeacon > BEACON_MS)) sendBeacon();
  serviceTxQueue();
  if (restartWifi && millis() > restartWifiAt) {
    restartWifi = false;
    dns.stop();
    WiFi.softAPdisconnect(true);
    startWifi();
  }
  delay(1);
}
