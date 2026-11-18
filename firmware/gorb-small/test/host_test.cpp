// Host-side test helper for gorb_core.h (driven by tests/test_firmware_core.py).
//   host_test inflate   < zlib data   -> prints decompressed bytes
//   host_test store     < raw data    -> prints zlib (stored) bytes
//   host_test packet    < packet      -> re-encodes the decoded packet
//   host_test filter    < text        -> prints blocked term or "ok"
//   host_test rewrite D < html        -> prints rewritten html
//   host_test url       < url         -> prints "domain path" or "bad"
#include <iostream>
#include <iterator>
#include <string>

#include "../src/gorb_core.h"

int main(int argc, char **argv) {
  std::string in((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
  std::string mode = argc > 1 ? argv[1] : "";
  const uint8_t *p = (const uint8_t *)in.data();
  if (mode == "inflate") {
    std::string out;
    if (!gorb::Inflater::zlibDecompress(p, in.size(), out, 1 << 20)) return 2;
    std::cout << out;
  } else if (mode == "store") {
    auto o = gorb::zlibStore(in);
    std::cout.write((const char *)o.data(), o.size());
  } else if (mode == "packet") {
    gorb::Packet pk;
    if (!pk.decode(p, in.size())) return 2;
    auto o = pk.encode();
    std::cout.write((const char *)o.data(), o.size());
  } else if (mode == "filter") {
    const char *t = gorb::checkContent(in);
    std::cout << (t ? t : "ok");
  } else if (mode == "rewrite") {
    std::cout << gorb::rewriteLinks(in, argv[2]);
  } else if (mode == "url") {
    std::string d, pa;
    std::cout << (gorb::parseUrl(in, d, pa) ? d + " " + pa : "bad");
  } else {
    return 1;
  }
  return 0;
}
