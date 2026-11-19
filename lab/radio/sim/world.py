"""Station placement and the link model.

Received power (dBm) for a frame from i to j:

    P = P_tx + G_ant_i + G_ant_j - PL(d) - wall_i - wall_j + X_ij + F

    PL(d)  log-distance: FSPL(1 m, f) + 10 n log10(d / 1 m)
           (n ~ 3.3 suburb, 3.6 dense city: Rappaport, "Wireless
           Communications", table 4.2 gives 2.7-3.5 urban, 3-5 shadowed urban;
           with stations at roof/window height, n = 3.3 gives ~135 dB at 1 km
           at 915 MHz, close to Okumura-Hata for low antennas)
    wall   building entry loss per indoor end (ITU-R P.2109 median for
           "traditional" buildings: ~10 dB at 900 MHz, ~13 dB at 2.4 GHz)
    X_ij   log-normal shadowing, fixed per link (symmetric), sigma 6-8 dB;
           the sub-GHz and 2.4 GHz values of a pair are correlated (rho 0.7)
    F      per-frame fading: Rician with factor K (K=0 -> Rayleigh), drawn
           afresh for every frame and receiver (block fading)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

FREQ = {"sub": 915e6, "2g4": 2.437e9}
WALL_DB = {"sub": 10.0, "2g4": 13.0}
ANT_DBI = {"sub": 2.0, "2g4": 0.0}  # whip on LoRa boards; PCB antenna on ESP32


def fspl_1m(f_hz: float) -> float:
    return 20 * math.log10(4 * math.pi * f_hz / 3e8)


@dataclass
class Station:
    sid: int
    x: float
    y: float
    indoor: bool
    radios: tuple  # e.g. ("sub", "2g4") or ("2g4",)
    role: str = "user"


@dataclass
class World:
    stations: list
    gain: dict  # band -> NxN matrix of link gain in dB (without tx power)
    exponent: float
    sigma: float
    rician_k: float
    size_m: tuple
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.stations)

    def pos(self) -> np.ndarray:
        return np.array([[s.x, s.y] for s in self.stations])

    def has(self, band: str) -> np.ndarray:
        return np.array([band in s.radios for s in self.stations])


def place(layout: str, n: int, size: float, rng: np.random.Generator) -> np.ndarray:
    """Positions in metres. Layouts: 'uniform' (suburb), 'city' (clusters of
    houses around a few centres), 'line', 'grid'."""
    if layout == "uniform":
        return rng.uniform(0, size, (n, 2))
    if layout == "city":
        k = max(2, n // 12)
        centres = rng.uniform(0.15 * size, 0.85 * size, (k, 2))
        which = rng.integers(0, k, n)
        pts = centres[which] + rng.normal(0, size * 0.09, (n, 2))
        return np.clip(pts, 0, size)
    if layout == "line":
        xs = np.linspace(0, size, n)
        return np.column_stack([xs, rng.normal(0, 5, n)])
    if layout == "grid":
        k = int(math.ceil(math.sqrt(n)))
        g = np.array([(i % k, i // k) for i in range(n)], float) * (size / max(k - 1, 1))
        return g + rng.normal(0, 3, (n, 2))
    raise ValueError(layout)


def build(n: int = 60, size: float = 3000.0, layout: str = "uniform", seed: int = 0,
          exponent: float = 3.3, sigma: float = 7.0, rician_k: float = 3.0,
          indoor_frac: float = 0.7, lora_frac: float = 1.0, positions=None,
          wall_scale: float = 1.0) -> World:
    """Make a random neighbourhood. lora_frac = share of stations that have a
    LoRa board (they always also have Wi-Fi: ESP32 + LoRa boards like the
    Heltec WiFi LoRa 32); the rest are plain ESP32s (Wi-Fi only)."""
    rng = np.random.default_rng(seed)
    pts = place(layout, n, size, rng) if positions is None else np.asarray(positions, float)
    n = len(pts)
    indoor = rng.random(n) < indoor_frac
    has_lora = rng.random(n) < lora_frac
    stations = [Station(i, float(pts[i, 0]), float(pts[i, 1]), bool(indoor[i]),
                        ("sub", "2g4") if has_lora[i] else ("2g4",)) for i in range(n)]
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    d = np.maximum(d, 1.0)
    z1 = rng.normal(0, 1, (n, n))
    z2 = rng.normal(0, 1, (n, n))
    z1 = np.triu(z1, 1) + np.triu(z1, 1).T
    z2 = np.triu(z2, 1) + np.triu(z2, 1).T
    rho = 0.7
    shadow = {"sub": sigma * z1, "2g4": sigma * (rho * z1 + math.sqrt(1 - rho ** 2) * z2)}
    gain = {}
    for band in ("sub", "2g4"):
        walls = WALL_DB[band] * wall_scale * indoor.astype(float)
        pl = fspl_1m(FREQ[band]) + 10 * exponent * np.log10(d)
        g = 2 * ANT_DBI[band] - pl - walls[:, None] - walls[None, :] + shadow[band]
        np.fill_diagonal(g, -300.0)
        gain[band] = g
    return World(stations, gain, exponent, sigma, rician_k, (size, size),
                 {"layout": layout, "seed": seed})


def rician_db(rng: np.random.Generator, k: float, size) -> np.ndarray:
    """Power gain (dB) of a unit-mean Rician channel with K-factor k (linear)."""
    if k is None or k < 0:
        return np.zeros(size)
    s = math.sqrt(k / (k + 1))
    sig = math.sqrt(1 / (2 * (k + 1)))
    re = s + sig * rng.standard_normal(size)
    im = sig * rng.standard_normal(size)
    return 10 * np.log10(re * re + im * im + 1e-12)


def link_range(mode, exponent=3.3, indoor_ends=0, band=None) -> float:
    """Median range (m) where the link reaches the mode's sensitivity."""
    band = band or mode.band
    budget = (mode.tx_dbm + 2 * ANT_DBI[band] - mode.sens_dbm - indoor_ends * WALL_DB[band]
              - fspl_1m(FREQ[band]))
    return 10 ** (budget / (10 * exponent))
