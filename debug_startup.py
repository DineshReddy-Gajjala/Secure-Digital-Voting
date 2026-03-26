import os
import sys
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

print("Checking imports...")
try:
    from backend.app import app, init_db
    print("Imports successful!")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("Initializing DB...")
try:
    init_db()
    print("DB initialized!")
except Exception as e:
    print(f"DB init failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("All diagnostic checks passed.")
