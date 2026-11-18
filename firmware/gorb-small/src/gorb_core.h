// G.O.R.B protocol pieces for the ESP32, written as plain C++ so they can
// also be compiled and tested on a normal computer (see test/host_test.cpp).
//
//  * Packet      - same layout as gorb/packet.py
//  * inflate     - unpacks zlib-compressed pages from PC hosts
//  * zlibStore   - packs pages the board hosts (zlib "stored" blocks: no
//                  compression, but any zlib reader can open them)
//  * checkContent- rule 1 filter, same word list as gorb/rules.py
//  * parseUrl / rewriteLinks - browser helpers
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <string>
#include <vector>

namespace gorb {

// ---------------------------------------------------------------- packets
enum PacketType : uint8_t { BEACON = 1, GET = 2, DATA = 3, RESEND = 4, ERROR_PKT = 5 };
static const size_t HEAD_LEN = 15;
static const size_t MAX_FRAME = 255;

struct Packet {
  uint8_t type = 0, ttl = 3, hops = 0, round = 0;
  uint32_t msgId = 0;
  uint16_t seq = 0, total = 0;
  std::string src, dst;
  std::vector<uint8_t> payload;

  size_t overhead() const { return HEAD_LEN + 2 + src.size() + dst.size(); }

  std::vector<uint8_t> encode() const {
    std::vector<uint8_t> o;
    o.reserve(overhead() + payload.size());
    o.push_back('G');
    o.push_back('B');
    o.push_back(1);
    o.push_back(type);
    o.push_back(ttl);
    o.push_back(hops);
    o.push_back(round);
    for (int s = 24; s >= 0; s -= 8) o.push_back((msgId >> s) & 0xFF);
    o.push_back(seq >> 8);
    o.push_back(seq & 0xFF);
    o.push_back(total >> 8);
    o.push_back(total & 0xFF);
    o.push_back((uint8_t)src.size());
    o.insert(o.end(), src.begin(), src.end());
    o.push_back((uint8_t)dst.size());
    o.insert(o.end(), dst.begin(), dst.end());
    o.insert(o.end(), payload.begin(), payload.end());
    return o;
  }

  bool decode(const uint8_t *p, size_t n) {
    if (n < HEAD_LEN + 2 || p[0] != 'G' || p[1] != 'B' || p[2] != 1) return false;
    type = p[3];
    ttl = p[4];
    hops = p[5];
    round = p[6];
    msgId = ((uint32_t)p[7] << 24) | ((uint32_t)p[8] << 16) | ((uint32_t)p[9] << 8) | p[10];
    seq = (p[11] << 8) | p[12];
    total = (p[13] << 8) | p[14];
    size_t i = HEAD_LEN;
    size_t sl = p[i];
    if (i + 1 + sl >= n) return false;
    src.assign((const char *)p + i + 1, sl);
    i += 1 + sl;
    size_t dl = p[i];
    if (i + 1 + dl > n) return false;
    dst.assign((const char *)p + i + 1, dl);
    i += 1 + dl;
    payload.assign(p + i, p + n);
    return true;
  }
};

// ----------------------------------------------------------------- inflate
// A small DEFLATE decoder in the style of zlib's "puff".
class Inflater {
 public:
  // Returns false on corrupt data or if the output would exceed max_out.
  static bool zlibDecompress(const uint8_t *in, size_t n, std::string &out, size_t max_out) {
    if (n < 6) return false;
    if ((in[0] & 0x0F) != 8 || ((in[0] << 8) | in[1]) % 31 != 0 || (in[1] & 0x20)) return false;
    Inflater s(in + 2, n - 2, out, max_out);
    if (!s.run()) return false;
    size_t end = 2 + s.incnt_;
    if (end + 4 > n) return false;
    uint32_t want = ((uint32_t)in[end] << 24) | ((uint32_t)in[end + 1] << 16) |
                    ((uint32_t)in[end + 2] << 8) | in[end + 3];
    return adler32((const uint8_t *)out.data(), out.size()) == want;
  }

  static uint32_t adler32(const uint8_t *d, size_t n) {
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < n; i++) {
      a = (a + d[i]) % 65521;
      b = (b + a) % 65521;
    }
    return (b << 16) | a;
  }

 private:
  struct Huff {
    short count[16];
    short symbol[288];
  };

  const uint8_t *in_;
  size_t inlen_, incnt_ = 0;
  uint32_t bitbuf_ = 0;
  int bitcnt_ = 0;
  std::string &out_;
  size_t max_;
  bool err_ = false;

  Inflater(const uint8_t *in, size_t n, std::string &out, size_t max_out)
      : in_(in), inlen_(n), out_(out), max_(max_out) {
    out_.clear();
  }

  int bits(int need) {
    uint32_t val = bitbuf_;
    while (bitcnt_ < need) {
      if (incnt_ >= inlen_) {
        err_ = true;
        return 0;
      }
      val |= (uint32_t)in_[incnt_++] << bitcnt_;
      bitcnt_ += 8;
    }
    bitbuf_ = val >> need;
    bitcnt_ -= need;
    return (int)(val & ((1UL << need) - 1));
  }

  bool put(uint8_t c) {
    if (out_.size() >= max_) return false;
    out_.push_back((char)c);
    return true;
  }

  bool stored() {
    bitbuf_ = 0;
    bitcnt_ = 0;
    if (incnt_ + 4 > inlen_) return false;
    unsigned len = in_[incnt_] | (in_[incnt_ + 1] << 8);
    unsigned nlen = in_[incnt_ + 2] | (in_[incnt_ + 3] << 8);
    incnt_ += 4;
    if (len != (~nlen & 0xFFFF) || incnt_ + len > inlen_) return false;
    for (unsigned i = 0; i < len; i++)
      if (!put(in_[incnt_++])) return false;
    return true;
  }

  int decode(const Huff &h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len <= 15; len++) {
      code |= bits(1);
      if (err_) return -1;
      int count = h.count[len];
      if (code - count < first) return h.symbol[index + (code - first)];
      index += count;
      first += count;
      first <<= 1;
      code <<= 1;
    }
    return -1;
  }

  static int construct(Huff &h, const short *length, int n) {
    short offs[16];
    for (int len = 0; len < 16; len++) h.count[len] = 0;
    for (int s = 0; s < n; s++) h.count[length[s]]++;
    if (h.count[0] == n) return 0;
    int left = 1;
    for (int len = 1; len < 16; len++) {
      left <<= 1;
      left -= h.count[len];
      if (left < 0) return left;
    }
    offs[1] = 0;
    for (int len = 1; len < 15; len++) offs[len + 1] = offs[len] + h.count[len];
    for (int s = 0; s < n; s++)
      if (length[s] != 0) h.symbol[offs[length[s]]++] = (short)s;
    return left;
  }

  bool codes(const Huff &lencode, const Huff &distcode) {
    static const short lens[29] = {3,  4,  5,  6,  7,  8,  9,  10, 11,  13,  15,  17,  19,  23, 27,
                                   31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
    static const short lext[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2,
                                   2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
    static const short dists[30] = {1,   2,   3,   4,   5,   7,    9,    13,   17,   25,
                                    33,  49,  65,  97,  129, 193,  257,  385,  513,  769,
                                    1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
    static const short dext[30] = {0, 0, 0, 0, 1, 1, 2, 2,  3,  3,  4,  4,  5,  5,  6,
                                   6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
    int symbol;
    do {
      symbol = decode(lencode);
      if (symbol < 0) return false;
      if (symbol < 256) {
        if (!put((uint8_t)symbol)) return false;
      } else if (symbol > 256) {
        symbol -= 257;
        if (symbol >= 29) return false;
        int len = lens[symbol] + bits(lext[symbol]);
        int ds = decode(distcode);
        if (ds < 0 || ds >= 30) return false;
        size_t dist = dists[ds] + bits(dext[ds]);
        if (err_ || dist > out_.size()) return false;
        while (len--)
          if (!put((uint8_t)out_[out_.size() - dist])) return false;
      }
    } while (symbol != 256);
    return !err_;
  }

  bool fixed() {
    static Huff lencode, distcode;
    static bool built = false;
    if (!built) {
      short lengths[288];
      int s = 0;
      for (; s < 144; s++) lengths[s] = 8;
      for (; s < 256; s++) lengths[s] = 9;
      for (; s < 280; s++) lengths[s] = 7;
      for (; s < 288; s++) lengths[s] = 8;
      construct(lencode, lengths, 288);
      for (s = 0; s < 30; s++) lengths[s] = 5;
      construct(distcode, lengths, 30);
      built = true;
    }
    return codes(lencode, distcode);
  }

  bool dynamic() {
    static const short order[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
    short lengths[320];
    Huff lencode, distcode;
    int nlen = bits(5) + 257, ndist = bits(5) + 1, ncode = bits(4) + 4;
    if (err_ || nlen > 286 || ndist > 30) return false;
    int index;
    for (index = 0; index < ncode; index++) lengths[order[index]] = (short)bits(3);
    for (; index < 19; index++) lengths[order[index]] = 0;
    if (err_ || construct(lencode, lengths, 19) != 0) return false;
    index = 0;
    while (index < nlen + ndist) {
      int symbol = decode(lencode);
      if (symbol < 0) return false;
      if (symbol < 16) {
        lengths[index++] = (short)symbol;
      } else {
        short len = 0;
        if (symbol == 16) {
          if (index == 0) return false;
          len = lengths[index - 1];
          symbol = 3 + bits(2);
        } else if (symbol == 17) {
          symbol = 3 + bits(3);
        } else {
          symbol = 11 + bits(7);
        }
        if (err_ || index + symbol > nlen + ndist) return false;
        while (symbol--) lengths[index++] = len;
      }
    }
    if (lengths[256] == 0) return false;
    int e = construct(lencode, lengths, nlen);
    if (e < 0 || (e > 0 && nlen - lencode.count[0] != 1)) return false;
    e = construct(distcode, lengths + nlen, ndist);
    if (e < 0 || (e > 0 && ndist - distcode.count[0] != 1)) return false;
    return codes(lencode, distcode);
  }

  bool run() {
    int last;
    do {
      last = bits(1);
      int type = bits(2);
      if (err_) return false;
      bool ok = type == 0 ? stored() : type == 1 ? fixed() : type == 2 ? dynamic() : false;
      if (!ok) return false;
    } while (!last);
    return true;
  }
};

// zlib container with uncompressed ("stored") blocks.
inline std::vector<uint8_t> zlibStore(const std::string &data) {
  std::vector<uint8_t> o = {0x78, 0x01};
  size_t pos = 0;
  do {
    size_t len = data.size() - pos;
    if (len > 65535) len = 65535;
    bool last = pos + len >= data.size();
    o.push_back(last ? 1 : 0);
    o.push_back(len & 0xFF);
    o.push_back(len >> 8);
    o.push_back(~len & 0xFF);
    o.push_back((~len >> 8) & 0xFF);
    o.insert(o.end(), data.begin() + pos, data.begin() + pos + len);
    pos += len;
  } while (pos < data.size());
  uint32_t a = Inflater::adler32((const uint8_t *)data.data(), data.size());
  for (int s = 24; s >= 0; s -= 8) o.push_back((a >> s) & 0xFF);
  return o;
}

// ------------------------------------------------------------------ rule 1
static const char *const BLOCKED_TERMS[] = {
    "nsfw", "porn", "pornography", "xxx", "adults only", "adult only", "18+", "explicit content",
    "nude", "nudes", "gore", "gory", "snuff", "beheading", "torture video"};

inline char lower(char c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; }
inline bool wordChar(char c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' ||
         c == '+';
}

// Returns the blocked term found, or nullptr if the text is fine.
inline const char *checkContent(const std::string &text) {
  for (const char *term : BLOCKED_TERMS) {
    size_t tl = strlen(term);
    for (size_t i = 0; i + tl <= text.size(); i++) {
      size_t k = 0;
      while (k < tl && lower(text[i + k]) == term[k]) k++;
      if (k != tl) continue;
      bool before = i > 0 && wordChar(text[i - 1]);
      bool after = i + tl < text.size() && wordChar(text[i + tl]);
      if (!before && !after) return term;
    }
  }
  return nullptr;
}

// ------------------------------------------------------------------- urls
inline bool validDomain(const std::string &d) {
  if (d.size() < 6 || d.size() > 47 || d.compare(d.size() - 5, 5, ".gorb") != 0) return false;
  std::string label = d.substr(0, d.size() - 5);
  if (label.empty() || label.front() == '-' || label.back() == '-') return false;
  for (char c : label)
    if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-')) return false;
  return true;
}

inline bool validName(const std::string &n) {
  if (n.size() < 3 || n.size() > 20) return false;
  for (char c : n)
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '/' ||
          c == '-'))
      return false;
  return true;
}

// "gorb://Cats.gorb/a.html" or "cats" -> domain "cats.gorb", path "/a.html"
inline bool parseUrl(std::string url, std::string &domain, std::string &path) {
  while (!url.empty() && (url.front() == ' ')) url.erase(0, 1);
  while (!url.empty() && (url.back() == ' ')) url.pop_back();
  if (url.compare(0, 7, "gorb://") == 0) url = url.substr(7);
  size_t slash = url.find('/');
  domain = url.substr(0, slash);
  path = slash == std::string::npos ? "/" : url.substr(slash);
  for (auto &c : domain) c = lower(c);
  if (domain.size() < 5 || domain.compare(domain.size() - 5, 5, ".gorb") != 0) domain += ".gorb";
  return validDomain(domain);
}

// Point links at the board's /g/ pages. Pages are served at
// /g/<domain>/<path>, so relative links already work; root-relative and
// gorb:// links need fixing.
inline std::string rewriteLinks(const std::string &html, const std::string &domain) {
  std::string out;
  out.reserve(html.size() + 256);
  size_t i = 0;
  while (i < html.size()) {
    bool matched = false;
    static const char *const attrs[] = {"href=", "src=", "action="};
    for (const char *a : attrs) {
      size_t al = strlen(a);
      if (i + al + 1 < html.size()) {
        size_t k = 0;
        while (k < al && lower(html[i + k]) == a[k]) k++;
        if (k == al && (html[i + al] == '"' || html[i + al] == '\'') &&
            (i == 0 || html[i - 1] == ' ' || html[i - 1] == '\n' || html[i - 1] == '\t')) {
          char q = html[i + al];
          out.append(html, i, al + 1);
          size_t v = i + al + 1;
          if (html.compare(v, 7, "gorb://") == 0) {
            out += "/g/";
            v += 7;
          } else if (html.compare(v, 2, "//") != 0 && v < html.size() && html[v] == '/') {
            out += "/g/" + domain;
          } else if (html.compare(v, 11, "javascript:") == 0) {
            out += "#";
            size_t e = html.find(q, v);
            v = e == std::string::npos ? html.size() : e;
          }
          i = v;
          matched = true;
          break;
        }
      }
    }
    if (!matched) out += html[i++];
  }
  return out;
}

inline std::string htmlEscape(const std::string &s) {
  std::string o;
  for (char c : s) {
    switch (c) {
      case '<': o += "&lt;"; break;
      case '>': o += "&gt;"; break;
      case '&': o += "&amp;"; break;
      case '"': o += "&quot;"; break;
      case '\'': o += "&#39;"; break;
      default: o += c;
    }
  }
  return o;
}

}  // namespace gorb
