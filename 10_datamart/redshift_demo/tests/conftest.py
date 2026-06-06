"""Make the scripts/ directory importable as top-level modules in tests."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
