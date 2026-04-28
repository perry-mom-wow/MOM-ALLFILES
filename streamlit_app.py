"""Top-level Streamlit Cloud entry point.

Streamlit Cloud's default working dir + import path don't always include the project
root, which breaks `from config.settings import ...` style imports inside agent modules.
This shim guarantees the path is set before importing the real dashboard.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Now run the real dashboard.
import runpy
runpy.run_path(str(ROOT / "dashboard" / "app.py"), run_name="__main__")
