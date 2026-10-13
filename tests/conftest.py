"""Direct pytest and focused verification share the same compiler boundary."""

import sys
from pathlib import Path


_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

pytest_plugins = ("verification.pytest_lean_integration",)
