import os
import sys
import time
import subprocess
import webbrowser
import threading

# Add necessary directories to Python path for robustness
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from backend.app import app, init_db
except ImportError as e:
    print(f"[Error] Failed to import backend: {e}")
    sys.exit(1)

def open_ms_edge():
    """Wait briefly, then open the localhost URL specifically in Microsoft Edge."""
    time.sleep(2.0)
    url = "http://127.0.0.1:5000"
    print(f"\n[Run Script] Automatically launching Microsoft Edge to {url} ...\n")
    
    # Try multiple ways to launch Edge
    try:
        # Standard paths for Windows
        edge_paths = [
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Microsoft\\Edge\\Application\\msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Microsoft\\Edge\\Application\\msedge.exe"),
        ]
        
        for path in edge_paths:
            if os.path.exists(path):
                subprocess.Popen([path, url])
                return

        # Fallback 1: Shell protocol (Windows only)
        if sys.platform == 'win32':
            os.system(f"start microsoft-edge:{url}")
            return
            
        # Fallback 2: Default browser
        webbrowser.open(url)
            
    except Exception as e:
        print(f"[Warning] Failed to launch Edge: {e}")

if __name__ == '__main__':
    print("="*60)
    print("  Initializing SecureVote (Phase 3 Strict Rubric Edition)")
    print("="*60)
    
    # Initialize the database logic
    try:
        init_db()
    except Exception as e:
        print(f"[Error] Database initialization failed: {e}")
        sys.exit(1)

    # Launch Edge automatically in a separate thread
    threading.Thread(target=open_ms_edge, daemon=True).start()

    # Start the Flask development server
    app.run(host='127.0.0.1', port=5000, debug=False)
