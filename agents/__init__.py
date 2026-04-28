"""Agents package — ensure project root is on sys.path before any submodule imports.

Streamlit Cloud's working directory may not include the project root, which would
break `from config.settings import ...` style imports inside submodules. This
lightweight bootstrap fixes that without requiring the entry point to do it.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
