#!/usr/bin/env python3
"""Startpunkt für Benthic: startet die lokale Web-GUI."""

import os

from benthic.app import app, start_background

if __name__ == "__main__":
    start_background()
    app.run(host="127.0.0.1", port=int(os.environ.get("BENTHIC_PORT", "8327")), debug=False, threaded=True)
