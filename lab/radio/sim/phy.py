"""Radio modes: airtime, sensitivity and packet error rate (PER) curves.

Every number here is an approximation of a published figure; the source is
given next to it. The lab cares about *relative* comparisons, so what matters
most is that all modes are modelled the same way.

Noise floor   N = -174 dBm/Hz + 10*log10(bandwidth) + receiver noise figure
Sensitivity   S = N + SNR_required            (PER about 10% for a 32-byte frame)
PER curve     P_ok(snr, L) = sigmoid((snr - snr50) / w) ** (L / 32)
              where snr50 is chosen so that P_ok(SNR_required, 32 B) = 0.9.
              Longer frames fail more often at the same SNR.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# LoRa. Required demodulator SNR per spreading factor: Semtech SX1276/77/78/79
# datasheet rev 7 (table 13, "LoRa demodulator SNR"); SF5/SF6 from the
# SX1261/2 datasheet (rev 2.1, table 6-1). With a 7 dB noise figure the
# resulting sensitivities are within ~1 dB of the SX1276 sensitivity table
# (e.g. SF7/125 kHz -123.5 vs -123 dBm, SF12/125 -136 vs -136/-137,
# SF8/500 kHz -120 vs -119 dBm).
LORA_SNR_REQ = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
LORA_NF = 7.0

# ESP32 Wi-Fi receive sensitivities (dBm), ESP32 Series datasheet v4.x,
# "Wi-Fi Radio" receiver table (11b 1 Mbps -98, 11 Mbps -88/-89, 11g 6 Mbps
# -93, 54 Mbps -75). Espressif staff quote -97 dBm for 11b 1 Mbps on real
# boards (esp32.com forum t=... "ESP32 Specifications (WiFi Receive)"),
# so we use -97. Long Range (LR) mode: Espressif says LR gains about 4 dB
# over 11b (ESP-IDF Wi-Fi guide, "Long Range"); a staff forum post quotes
# -105 dBm at the lowest LR rate. We use -101 dBm (500 kbit/s) and
# -104 dBm (250 kbit/s). LR figures are the least certain numbers here.
WIFI_SENS = {
    "b1": -97.0, "b2": -94.0, "b5.5": -92.0, "b11": -88.0,
    "g6": -92.0, "g12": -88.0, "g24": -83.0, "g54": -74.0,
    "lr500": -101.0, "lr250": -104.0,
}
WIFI_NF = 5.0
WIFI_TXP = {  # ESP32 datasheet: 11b ~19.5-20 dBm, 11g 54M ~14 dBm
    "b1": 20.0, "b2": 20.0, "b5.5": 20.0, "b11": 20.0,
    "g6": 18.0, "g12": 18.0, "g24": 17.0, "g54": 14.0,
    "lr500": 20.0, "lr250": 20.0,
}
WIFI_RATE = {"b1": 1e6, "b2": 2e6, "b5.5": 5.5e6, "b11": 11e6, "g6": 6e6, "g12": 12e6,
             "g24": 24e6, "g54": 54e6, "lr500": 0.5e6, "lr250": 0.25e6}
ESPNOW_OVERHEAD = 43  # bytes: 802.11 MAC header 24 + action/vendor element 15 + FCS 4


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


@dataclass(frozen=True)
class Mode:
    """One way of putting bits on the air."""
    name: str
    band: str  # "sub" (sub-GHz LoRa/AFSK) or "2g4" (Wi-Fi)
    kind: str  # "lora" | "wifi" | "afsk"
    bitrate: float
    bw_hz: float
    nf_db: float
    snr_req: float  # SNR (dB) for PER ~10% at 32 bytes
    tx_dbm: float
    max_frame: int
    capture_db: float  # stronger frame survives if this much above the rest
    pw: float = 0.8  # width (dB) of the PER waterfall
    sf: int = 0
    cr: int = 5
    preamble_syms: int = 8
    tx_watts: float = 0.40  # supply power while transmitting (energy estimate)
    rxkey: tuple = field(default=())  # receivers must be tuned to the same key
    code: str = ""  # short id, e.g. "b1" for Wi-Fi rates

    @property
    def noise_dbm(self) -> float:
        return -174.0 + 10 * math.log10(self.bw_hz) + self.nf_db

    @property
    def sens_dbm(self) -> float:
        return self.noise_dbm + self.snr_req

    @property
    def tsym(self) -> float:
        return (2 ** self.sf) / self.bw_hz if self.kind == "lora" else 0.0

    @property
    def preamble_s(self) -> float:
        if self.kind == "lora":
            return (self.preamble_syms + 4.25) * self.tsym
        if self.kind == "wifi":
            return 192e-6 if self.code.startswith(("b", "lr")) else 20e-6
        return 0.3

    def airtime(self, nbytes: int) -> float:
        if self.kind == "lora":
            return lora_airtime(nbytes, self.sf, self.bw_hz, self.cr, self.preamble_syms)
        if self.kind == "wifi":
            return wifi_airtime(nbytes, self.code)
        # AFSK 1200 (AX.25): 300 ms TX delay (radio keying), flags, ~5% bit stuffing
        return 0.3 + (nbytes + 20) * 8 * 1.05 / self.bitrate

    def p_ok(self, snr_db, nbytes: int):
        """Probability a frame of nbytes is received, at this SNR (scalar or numpy)."""
        import numpy as np
        snr50 = self.snr_req - self.pw * math.log(9.0)
        p32 = 1.0 / (1.0 + np.exp(-(np.asarray(snr_db) - snr50) / self.pw))
        return p32 ** (max(nbytes, 1) / 32.0)


def lora_airtime(nbytes: int, sf: int, bw_hz: float, cr: int = 5, preamble: int = 8,
                 explicit: bool = True, crc: bool = True) -> float:
    """Semtech time-on-air formula (SX1276 datasheet 4.1.1.7 / AN1200.13;
    SX1262 variant for SF5/SF6)."""
    tsym = (2 ** sf) / bw_hz
    de = 1 if tsym > 0.016 else 0  # low data rate optimisation (mandated above 16 ms)
    ih = 0 if explicit else 1
    if sf < 7:  # SX126x: SF5/6 use a different header term and 6.25 preamble symbols
        num = 8 * nbytes + 16 * crc - 4 * sf + 20 * (1 - ih) - 0
        nsym = 8 + max(math.ceil(num / (4 * sf)) * cr, 0)
        return (preamble + 6.25 + nsym) * tsym
    num = 8 * nbytes - 4 * sf + 28 + 16 * crc - 20 * ih
    nsym = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * cr, 0)
    return (preamble + 4.25 + nsym) * tsym


def wifi_airtime(nbytes: int, name: str) -> float:
    """802.11 frame duration for an ESP-NOW frame carrying nbytes of payload
    (IEEE 802.11-2016 clauses 16/17). LR is Espressif-proprietary; we treat
    it like 11b timing at its lower rate (an assumption)."""
    L = nbytes + ESPNOW_OVERHEAD
    r = WIFI_RATE[name]
    if name.startswith("b") or name.startswith("lr"):
        pre = 192e-6 if name.startswith("b") else 384e-6  # assume LR has a longer preamble
        return pre + 8 * L / r
    ndbps = r * 4e-6
    nsym = math.ceil((16 + 8 * L + 6) / ndbps)
    return 20e-6 + 4e-6 * nsym + 6e-6


def lora(sf: int = 8, bw_khz: float = 500, tx_dbm: float = 20.0, cr: int = 5) -> Mode:
    bw = bw_khz * 1e3
    rate = sf * bw / (2 ** sf) * 4 / cr
    return Mode(f"LoRa SF{sf}/{bw_khz:g}k", "sub", "lora", rate, bw, LORA_NF,
                LORA_SNR_REQ[sf], tx_dbm, 255, 6.0, 0.8, sf, cr, 8, 0.40,
                ("lora", sf, bw))


def wifi(name: str = "b1") -> Mode:
    bw = 20e6
    noise = -174 + 10 * math.log10(bw) + WIFI_NF
    label = {"b1": "ESP-NOW 1M", "b2": "ESP-NOW 2M", "b5.5": "ESP-NOW 5.5M", "b11": "ESP-NOW 11M",
             "g6": "ESP-NOW 6M", "g12": "ESP-NOW 12M", "g24": "ESP-NOW 24M", "g54": "ESP-NOW 54M",
             "lr500": "Wi-Fi LR 500k", "lr250": "Wi-Fi LR 250k"}[name]
    # Capture in 802.11: a frame ~10 dB stronger usually survives (measured
    # 4-10 dB for 11b, Ganu et al. 2005 / Lee et al. 2007); 10 dB is cautious.
    return Mode(label, "2g4", "wifi", WIFI_RATE[name], bw, WIFI_NF,
                WIFI_SENS[name] - noise, WIFI_TXP[name], 250, 10.0, 1.0,
                tx_watts=0.80, rxkey=("wifi",), code=name)


def afsk1200(tx_dbm: float = 37.0) -> Mode:
    """Ham AFSK 1200 on a 5 W FM radio. FM receivers reach 12 dB SINAD around
    -120 dBm; AFSK needs a few dB more, no FEC, so a soft PER curve."""
    return Mode("AFSK 1200", "sub", "afsk", 1200, 12.5e3, 8.0, 12.0, tx_dbm, 330, 12.0, 1.5,
                tx_watts=15.0, rxkey=("afsk",))


# Ready-made modes
US915 = lora(8, 500)  # G.O.R.B Small US/AU preset
EU868 = lora(8, 250, tx_dbm=20.0)  # G.O.R.B Small EU preset (27 dBm allowed, boards ~20)
ESPNOW = wifi("b1")
WIFI_LR250 = wifi("lr250")
WIFI_LR500 = wifi("lr500")
WIFI_RATES = [wifi(n) for n in ["lr250", "lr500", "b1", "b2", "b5.5", "b11", "g6", "g12", "g24", "g54"]]
LORA_FAST = [lora(sf, 500) for sf in (5, 6, 7, 8, 9, 10)]
