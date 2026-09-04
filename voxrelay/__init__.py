"""Vox Relay — read-only relay of chosen Messages threads to a local JSONL (and, if you turn it on, to Vox Ordo)."""
import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION.txt")
try:
    with open(_VERSION_FILE, encoding="utf-8") as _f:
        __version__ = _f.read().strip() or "0.0.0"
except OSError:
    __version__ = "0.0.0"
