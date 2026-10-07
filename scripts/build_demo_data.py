#!/usr/bin/env python3
"""Rebuild the small, genuine Lichess starter pack (never fetch the full database).

Online: python scripts/build_demo_data.py --download --engine .runtime/stockfish
Offline: python scripts/build_demo_data.py --engine .runtime/stockfish
Validate only: python scripts/build_demo_data.py --validate

Dependencies are the application's python-chess, httpx and zstandard packages.
The original selected CSV rows and exported PGNs are retained for provenance.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import json
import os
import pathlib
import re
import sys
import time

import chess
import chess.engine
import chess.pgn
import httpx
import zstandard

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUZZLE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
THEMES = ("mateIn1", "mateIn2", "mateIn3", "fork", "pin", "skewer",
          "discoveredAttack", "sacrifice", "promotion", "defensiveMove")
BANDS = (500, 900, 1300, 1700, 2100)
EXPORT_OPTIONS = {"evals": "true", "clocks": "false", "literate": "false"}


def dump(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalized_puzzle(row: dict[str, str]) -> dict:
    board = chess.Board(row["FEN"])
    if not board.is_valid():
        raise ValueError("Invalid source board")
    moves = row["Moves"].split()
    if len(moves) < 2:
        raise ValueError("No solution")
    for index, uci in enumerate(moves):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal source move {uci}")
        board.push(move)
        if index == 0:
            start_fen = board.fen()
    return {"id": row["PuzzleId"], "fen": start_fen, "moves": moves[1:],
            "rating": int(row["Rating"]), "themes": row["Themes"].split(),
            "game_url": row["GameUrl"], "source": "Lichess"}


def stream_csv_rows(client: httpx.Client):
    """Incremental zstd decompression; closing the generator cancels the HTTP body."""
    with client.stream("GET", PUZZLE_URL) as response:
        response.raise_for_status()
        decompressor = zstandard.ZstdDecompressor().decompressobj()
        buffer = b""
        fields = None
        for chunk in response.iter_bytes(16384):
            # The official archive includes a skippable zstd seek-table frame
            # and multiple compressed frames, not one continuous zstd frame.
            while chunk:
                buffer += decompressor.decompress(chunk)
                if decompressor.eof:
                    chunk = decompressor.unused_data
                    decompressor = zstandard.ZstdDecompressor().decompressobj()
                else:
                    break
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            for line in lines:
                if not line:
                    continue
                values = next(csv.reader([line.decode("utf-8")]))
                if fields is None:
                    fields = values
                else:
                    yield dict(zip(fields, values))


def download_puzzles(client: httpx.Client, max_rows: int) -> dict:
    counts = collections.Counter()
    picked = []
    seen_fens = set()
    scanned = 0
    invalid = 0
    iterator = stream_csv_rows(client)
    try:
        for row in iterator:
            scanned += 1
            rating = int(row["Rating"])
            if 500 <= rating < 2500:
                band = max(b for b in BANDS if b <= rating)
                themes = set(row["Themes"].split())
                # More mate-in-two drills; overlapping themes satisfy all quotas.
                wanted = [t for t in THEMES if t in themes and
                          counts[t, band] < (16 if t == "mateIn2" else 6)]
                if wanted:
                    try:
                        puzzle = normalized_puzzle(row)
                    except (ValueError, chess.InvalidMoveError):
                        invalid += 1
                    else:
                        if puzzle["fen"] not in seen_fens:
                            picked.append(row)
                            seen_fens.add(puzzle["fen"])
                            for theme in themes:
                                counts[theme, band] += 1
            if scanned >= max_rows:
                break
    finally:
        iterator.close()
    if len(picked) < 100:
        raise RuntimeError(f"Only {len(picked)} puzzles found, expected at least 100")
    with (DATA / "puzzle-source.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(picked[0]))
        writer.writeheader()
        writer.writerows(picked)
    print(f"Selected {len(picked)} puzzles after scanning {scanned} rows", flush=True)
    return {"download_url": PUZZLE_URL, "rows_scanned": scanned,
            "invalid_source_rows": invalid,
            "selection": "First matching unique positions; theme and rating-band quotas; not random sampling"}


def export_games(client: httpx.Client, puzzles: list[dict], game_count: int) -> list[str]:
    # Higher-rated puzzles tend to supply longer games; game IDs remain unmodified.
    games = []
    ids = []
    for puzzle in sorted(puzzles, key=lambda p: (-p["rating"], p["id"])):
        match = re.match(r"https://lichess\.org/([A-Za-z0-9]{8})", puzzle["game_url"])
        if match and match[1] not in ids:
            ids.append(match[1])
    for game_id in ids:
        if len(games) >= game_count:
            break
        url = f"https://lichess.org/game/export/{game_id}"
        response = client.get(url, params=EXPORT_OPTIONS)
        if response.status_code == 429:
            raise RuntimeError("Lichess rate limit reached; wait before retrying")
        if response.status_code != 200:
            print(f"Skipping unavailable game {game_id}: {response.status_code}", flush=True)
            continue
        text = response.text
        game = chess.pgn.read_game(io.StringIO(text))
        if game is None or game.errors or game.headers.get("Variant", "Standard") != "Standard":
            continue
        if game.headers.get("Site") != f"https://lichess.org/{game_id}":
            raise RuntimeError(f"Unexpected exported game identity for {game_id}")
        games.append(text.strip())
        print(f"Downloaded game {game_id} ({len(games)}/{game_count})", flush=True)
        time.sleep(0.25)
    (DATA / "intuition-source.pgn").write_text("\n\n".join(games) + "\n", encoding="utf-8")
    return [re.search(r'\[Site "([^"]+)"\]', game)[1] for game in games]


def read_games():
    with (DATA / "intuition-source.pgn").open(encoding="utf-8") as source:
        while (game := chess.pgn.read_game(source)) is not None:
            if game.errors:
                raise ValueError(f"Invalid PGN: {game.errors}")
            yield game


def intuition_candidates(game: chess.pgn.Game):
    for node in game.mainline():
        board = node.board()
        if board.ply() < 16 or board.is_game_over():
            continue
        if not board.is_valid() or len(board.piece_map()) < 10:
            continue
        score = node.eval()
        # Published annotations are only a prefilter; actual inclusion uses
        # a fresh Stockfish search below. Positive values favor side to move.
        annotated_cp = score.pov(board.turn).score() if score else None
        if annotated_cp is not None and not 60 <= annotated_cp <= 360:
            continue
        yield board


def build_intuition(engine_path: pathlib.Path, count: int, depth: int) -> list[dict]:
    selected = []
    seen = set()
    engine = chess.engine.SimpleEngine.popen_uci(str(engine_path.resolve()))
    try:
        engine.configure({"Threads": 1, "Hash": 32})
        name = engine.id["name"]
        games = list(read_games())
        # Round-robin keeps the starter set diverse even if one game has many
        # advantageous positions. A maximum of three positions per source game.
        queues = [(game, list(intuition_candidates(game))) for game in games]
        counts = collections.Counter()
        while len(selected) < count and any(queue for _, queue in queues):
            for game, queue in queues:
                url = game.headers["Site"]
                if counts[url] >= 3:
                    queue.clear()
                    continue
                while queue:
                    board = queue.pop(0)
                    if board.fen() in seen:
                        continue
                    engine.configure({"Clear Hash": None})
                    info = engine.analyse(board, chess.engine.Limit(depth=depth))
                    cp = info["score"].pov(board.turn).score()
                    if cp is None or not 100 <= cp <= 300:
                        continue
                    game_id = url.rstrip("/").split("/")[-1]
                    item = {"id": f"{game_id}-{board.ply()}", "fen": board.fen(),
                            "game_url": f"{url}#{board.ply()}", "source": "Lichess",
                            "cp": cp, "depth": info["depth"], "engine": name,
                            "ply": board.ply(), "analysis_limit": {"depth": depth},
                            "engine_options": {"Threads": 1, "Hash": 32}}
                    selected.append(item)
                    seen.add(board.fen())
                    counts[url] += 1
                    print(f"Intuition {len(selected)}/{count}: {game_id} ply {board.ply()} +{cp/100:.2f}", flush=True)
                    break
                if len(selected) >= count:
                    break
        if len(selected) < count:
            raise RuntimeError(f"Only {len(selected)} intuition positions found; download more games")
    finally:
        engine.quit()
    return selected


def validate() -> dict:
    puzzles = json.loads((DATA / "puzzles.json").read_text(encoding="utf-8"))
    intuition = json.loads((DATA / "intuition.json").read_text(encoding="utf-8"))
    source_rows = {r["PuzzleId"]: r for r in csv.DictReader((DATA / "puzzle-source.csv").open(encoding="utf-8"))}
    assert len(puzzles) == len({p["id"] for p in puzzles}) == len({p["fen"] for p in puzzles})
    for puzzle in puzzles:
        assert puzzle == normalized_puzzle(source_rows[puzzle["id"]])
        board = chess.Board(puzzle["fen"])
        assert board.is_valid()
        for uci in puzzle["moves"]:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            board.push(move)
        if any(t in puzzle["themes"] for t in ("mateIn1", "mateIn2", "mateIn3")):
            assert board.is_checkmate(), puzzle["id"]
    positions = {}
    for game in read_games():
        for node in game.mainline():
            positions[game.headers["Site"], node.board().ply()] = node.board().fen()
    assert len(intuition) == len({p["id"] for p in intuition}) == len({p["fen"] for p in intuition})
    for item in intuition:
        board = chess.Board(item["fen"])
        assert board.is_valid() and not board.is_game_over()
        assert 100 <= item["cp"] <= 300 and item["depth"] >= 12
        url, ply = item["game_url"].split("#")
        assert positions[url, int(ply)] == item["fen"], item["id"]
    return {"puzzles": len(puzzles), "intuition": len(intuition),
            "puzzle_ratings": [min(p["rating"] for p in puzzles), max(p["rating"] for p in puzzles)],
            "themes": dict(collections.Counter(t for p in puzzles for t in p["themes"])),
            "intuition_games": len({p["game_url"].split("#")[0] for p in intuition}),
            "all_lines_legal": True, "all_intuition_fens_verified_in_source_pgn": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", action="store_true", help="Fetch new public source samples; existing source files are replaced")
    parser.add_argument("--engine", type=pathlib.Path, default=ROOT / ".runtime" / ("stockfish.exe" if os.name == "nt" else "stockfish"))
    parser.add_argument("--max-rows", type=int, default=20000, help="Maximum puzzle CSV rows to stream")
    parser.add_argument("--games", type=int, default=24)
    parser.add_argument("--intuition", type=int, default=36)
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    if args.validate:
        print(json.dumps(validate(), indent=2))
        return
    if args.depth < 12:
        parser.error("Use at least depth 12 for the starter pack")
    if not args.engine.is_file():
        parser.error(f"Stockfish binary is missing: {args.engine}")
    meta = {"license": "CC0-1.0", "source_documentation": "https://database.lichess.org/#puzzles",
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "engine_depth": args.depth}
    if args.download:
        with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "ChessTrainer starter-pack builder"}) as client:
            meta["puzzle_sample"] = download_puzzles(client, args.max_rows)
            with (DATA / "puzzle-source.csv").open(encoding="utf-8") as source:
                puzzles = [normalized_puzzle(row) for row in csv.DictReader(source)]
            meta["games"] = export_games(client, puzzles, args.games)
    else:
        with (DATA / "puzzle-source.csv").open(encoding="utf-8") as source:
            puzzles = [normalized_puzzle(row) for row in csv.DictReader(source)]
        if (DATA / "provenance.json").exists():
            old = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
            for key in ("puzzle_sample", "games", "downloaded_at"):
                if key in old:
                    meta[key] = old[key]
    dump(DATA / "puzzles.json", sorted(puzzles, key=lambda p: (p["rating"], p["id"])))
    dump(DATA / "intuition.json", build_intuition(args.engine, args.intuition, args.depth))
    meta["validation"] = validate()
    if args.download:
        meta["downloaded_at"] = meta["built_at"]
    dump(DATA / "provenance.json", meta)
    print(json.dumps(meta["validation"], indent=2))


if __name__ == "__main__":
    main()
