"""Test path setup.

`processing.py` is HA-free and imported standalone (no package __init__, so no
Home Assistant needed). The api/CSRF tests import via the full package path and
self-skip when Home Assistant isn't installed.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components" / "nextenergy"))
