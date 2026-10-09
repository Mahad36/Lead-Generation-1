import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
ROOT = API_ROOT.parents[1]
for path in (str(API_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
