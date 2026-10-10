"""Launcher used by PyInstaller (and handy from source: python run_databizex.py)."""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
