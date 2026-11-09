"""Resolve writable application data paths for source and frozen builds."""

import sys
from pathlib import Path


def get_application_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
