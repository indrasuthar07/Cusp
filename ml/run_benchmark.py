"""CLI compatibility entrypoint for the Phase 7 local benchmark runner."""

import sys
from pathlib import Path

# The governance engine is a workspace package rather than an installed app.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "gateway"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "cusp-sdk-python"))

from ml.benchmarks.runner import main

if __name__ == "__main__":
    main()
