#!/usr/bin/env python3
"""Stream a full local puzzle database into SQLite without HTTP upload limits."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trainer.library import LibraryStore, import_puzzle_file


def main():
    parser = argparse.ArgumentParser(description="Import Lichess CSV / CSV.zst or a Knightlab JSON puzzle pack.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("CHESS_TRAINER_DATA_DIR", str(ROOT / ".data"))))
    parser.add_argument("--max-rows", type=int, default=None, help="Optional row limit. By default stream the entire local file.")
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("File does not exist.")
    if args.max_rows is not None and args.max_rows <= 0:
        parser.error("--max-rows must be positive.")
    print("Importing and validating legal solution lines. Large databases may take several minutes …", flush=True)
    result = import_puzzle_file(LibraryStore(args.data_dir), args.file, max_rows=args.max_rows, max_expanded_bytes=None)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
