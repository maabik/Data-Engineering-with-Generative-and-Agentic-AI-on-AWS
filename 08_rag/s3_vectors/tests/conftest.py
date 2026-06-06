import os
import sys
from pathlib import Path

# Ensure `src/` is importable without installing the package, so tests run in
# both `pip install -e .` and bare-checkout setups.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Tests should never reach AWS, but make accidental client construction obvious.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
