"""Start the local server: python -m trainer --open."""

import argparse
import ipaddress
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run the local Chess Trainer web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the app in your default browser when ready.")
    args = parser.parse_args()
    try:
        loopback = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error("--host must be a loopback address (127.0.0.1, ::1 or localhost).")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    host_url = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{host_url}:{args.port}"
    if args.open:
        def open_when_ready():
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + "/api/status", timeout=1):
                        webbrowser.open(url)
                        return
                except Exception:
                    time.sleep(0.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("trainer.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
