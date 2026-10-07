#!/usr/bin/env python3
"""One-click local launcher; creates a project environment only when needed."""
from pathlib import Path
import os
import subprocess
import sys
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required. Install Python from https://www.python.org/downloads/.", file=sys.stderr)
        return 1
    os.chdir(ROOT)
    # Reuse an existing local instance rather than starting another owner on the same port.
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=1) as response:
            if response.status == 200 and b'"puzzles"' in response.read(20000):
                webbrowser.open("http://127.0.0.1:8765")
                print("Knightlab is already running. Opened the training app.")
                return
    except (OSError, ValueError):
        pass
    if not PYTHON.exists():
        print("Preparing the local Python environment...", flush=True)
        venv.create(ENV, with_pip=True)
    check = subprocess.run([str(PYTHON), "-c", "import chess, fastapi, uvicorn, multipart, httpx, zstandard"], capture_output=True)
    if check.returncode:
        subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    engine = ROOT / ".runtime" / ("stockfish.exe" if os.name == "nt" else "stockfish")
    if not engine.exists() and not os.environ.get("STOCKFISH_PATH"):
        try:
            subprocess.run([str(PYTHON), "scripts/install_stockfish.py"], check=True)
        except subprocess.CalledProcessError:
            print("Puzzle modes are available. Install Stockfish later to enable Intuition training.", flush=True)
    print("Starting Knightlab. Keep this window open; Ctrl+C stops the server.", flush=True)
    try:
        subprocess.run([str(PYTHON), "-m", "trainer", "--open"], check=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Knightlab could not start: {error}", file=sys.stderr)
        print("Check that this folder is writable and that first-time setup has internet access.", file=sys.stderr)
        sys.exit(1)
