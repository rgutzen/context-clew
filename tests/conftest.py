"""Standalone cut: `clew` and its two shared modules all live here."""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
for p in (HERE, HERE / "lib"):
    sys.path.insert(0, str(p))
