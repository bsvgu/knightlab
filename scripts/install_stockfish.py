#!/usr/bin/env python3
"""Install a checksum-verified official Stockfish binary into the ignored .runtime folder."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "sf_19"


def asset_name() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if system == "Darwin":
        return "stockfish-macos-universal.tar.gz"
    if system == "Windows" and (arm or machine in {"x86_64", "amd64"}):
        return f"stockfish-windows-{'arm64' if arm else 'x86-64'}-universal.zip"
    if system == "Linux" and (arm or machine in {"x86_64", "amd64", "riscv64"}):
        arch = "arm64" if arm else "riscv64" if machine == "riscv64" else "x86-64"
        return f"stockfish-linux-{arch}-universal.tar.gz"
    raise RuntimeError("No automatic Stockfish download for this platform. Set STOCKFISH_PATH to an existing UCI engine.")


def install() -> Path:
    name = asset_name()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    destination = runtime / ("stockfish.exe" if os.name == "nt" else "stockfish")
    with httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": "Knightlab-local-setup"}) as client:
        # Hosted CI runners share unauthenticated API limits. Use their optional
        # token only for release metadata, never for the binary download.
        token = os.environ.get("GITHUB_TOKEN")
        metadata_headers = {"Authorization": f"Bearer {token}"} if token else {}
        release = client.get(f"https://api.github.com/repos/official-stockfish/Stockfish/releases/tags/{RELEASE}", headers=metadata_headers)
        release.raise_for_status()
        asset = next((item for item in release.json()["assets"] if item["name"] == name), None)
        if not asset:
            raise RuntimeError(f"Official release asset not found: {name}")
        digest = asset.get("digest", "")
        if not digest.startswith("sha256:"):
            raise RuntimeError("Official release does not provide a SHA-256 checksum. Refusing an unverified install.")
        print(f"Downloading Stockfish 19 for {platform.system()} {platform.machine()} …", flush=True)
        with tempfile.TemporaryDirectory(prefix="knightlab-stockfish-") as directory:
            archive = Path(directory) / name
            checksum = hashlib.sha256()
            with client.stream("GET", asset["browser_download_url"]) as response:
                response.raise_for_status()
                with archive.open("wb") as file:
                    for chunk in response.iter_bytes():
                        checksum.update(chunk)
                        file.write(chunk)
            if checksum.hexdigest() != digest.removeprefix("sha256:"):
                raise RuntimeError("Stockfish checksum mismatch. The engine has not been installed.")
            temporary_binary = Path(directory) / "engine"
            # Extract only a verified expected binary and license; never extract archive paths.
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive) as bundle:
                    candidates = [entry for entry in bundle.infolist() if not entry.is_dir() and Path(entry.filename).name.startswith("stockfish") and entry.filename.endswith(".exe")]
                    if len(candidates) != 1:
                        raise RuntimeError("Unexpected Stockfish archive layout.")
                    with bundle.open(candidates[0]) as source, temporary_binary.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    license_entry = next((entry for entry in bundle.infolist() if Path(entry.filename).name.lower() == "copying.txt"), None)
                    if license_entry:
                        (runtime / "Stockfish-COPYING.txt").write_bytes(bundle.read(license_entry))
            else:
                with tarfile.open(archive) as bundle:
                    candidates = [entry for entry in bundle.getmembers() if entry.isfile() and Path(entry.name).name.startswith("stockfish") and "." not in Path(entry.name).name]
                    if len(candidates) != 1:
                        raise RuntimeError("Unexpected Stockfish archive layout.")
                    with bundle.extractfile(candidates[0]) as source, temporary_binary.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    license_entry = next((entry for entry in bundle.getmembers() if entry.isfile() and Path(entry.name).name.lower() == "copying.txt"), None)
                    if license_entry:
                        (runtime / "Stockfish-COPYING.txt").write_bytes(bundle.extractfile(license_entry).read())
            temporary_binary.chmod(0o755)
            shutil.copy2(temporary_binary, destination)
        (runtime / "stockfish-source.json").write_text(json.dumps({"release": RELEASE, "asset": name, "sha256": digest.removeprefix("sha256:"), "source": f"https://github.com/official-stockfish/Stockfish/tree/{RELEASE}", "download": asset["browser_download_url"]}, indent=2) + "\n")
    print(f"Installed: {destination}")
    return destination


if __name__ == "__main__":
    try:
        install()
    except (httpx.HTTPError, OSError, RuntimeError) as error:
        print(f"Stockfish setup failed: {error}", file=sys.stderr)
        sys.exit(1)
