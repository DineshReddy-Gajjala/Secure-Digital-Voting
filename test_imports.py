import sys
import os
import traceback
from pathlib import Path

# BASIC ASCII-ONLY DIAGNOSTIC SCRIPT
print("=== SECUREVOTE DIAGNOSTICS (ASCII) ===")
print("Python: " + sys.version)
print("Working Dir: " + os.getcwd())
print("Path:")
for p in sys.path:
    print("  - " + str(p))

import importlib
deps = ["flask", "flask_sqlalchemy", "flask_cors", "jwt", "cv2", "numpy", "sklearn", "joblib", "pandas"]
for d in deps:
    try:
        importlib.import_module(d)
        print("[OK] " + d)
    except ImportError:
        print("[ERR] " + d + " IS MISSING")

print("\n=== PROJECT STRUCTURE ===")
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    print("Importing backend.app...")
    import backend.app
    print("[OK] backend.app imported")
    
    print("Importing face_engine...")
    from face_recognition_module.face_engine import verify_face_match
    print("[OK] face_engine imported")
    
    print("Importing fraud_detector...")
    from face_recognition_module.fraud_detector import FraudDetector
    print("[OK] fraud_detector imported")
except Exception as e:
    print("\n[ERR] PROJECT IMPORT FAILED: " + str(e))
    traceback.print_exc()

print("\n=== DIAGNOSTICS DONE ===")
