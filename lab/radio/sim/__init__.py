"""Gorb virtual radio lab: a small network-level radio simulator.

Modules
    phy       radio modes (LoRa, ESP32 Wi-Fi / ESP-NOW / LR, AFSK), airtime,
              sensitivity, SNR -> packet error curves
    world     station placement and the link model (path loss, walls,
              shadowing, fading)
    engine    discrete-event simulator: transmissions, carrier sense,
              half duplex, collisions with capture, duty cycle
    proto     a simplified G.O.R.B protocol (GET / DATA / RESEND / relay /
              Echo) with switchable "idea" variants
    experiments  the scenarios and idea comparisons used in REPORT.md
    radio_lab.py command line entry point (in this folder)
"""
