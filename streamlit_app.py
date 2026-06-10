"""Top-level Streamlit entry point.

Two responsibilities before the real dashboard runs:

1. Make the project root importable so `from config.settings import ...` works
   regardless of where Streamlit launched us from.
2. Hydrate the local filesystem (queues/, data/sequences/, sent/) from Postgres
   on container start. Platforms like Railway / Streamlit Cloud wipe the disk
   on every restart; without hydration the app starts empty even though state
   is durable in the DB.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Hydrate state from Postgres if DATABASE_URL is set. No-op locally without it.
if os.getenv("DATABASE_URL", "").strip():
    try:
        from state.file_sync import hydrate_local_files
        counts = hydrate_local_files()
        if not counts.get("skipped"):
            print(
                f"[startup] hydrated from Postgres: "
                f"queues={counts.get('queues', 0)}, "
                f"sent={counts.get('sent', 0)}, "
                f"sequences={counts.get('sequences', 0)}",
                flush=True,
            )
    except Exception as e:
        print(f"[startup] hydration failed (continuing with empty filesystem): {e}", flush=True)

# Now run the real dashboard.
import runpy
runpy.run_path(str(ROOT / "dashboard" / "app.py"), run_name="__main__")
