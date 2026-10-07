"""SQLite library and bounded, streaming puzzle imports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import sqlite3
from threading import RLock
from typing import BinaryIO

import chess
import zstandard

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_IMPORT_ROWS = 250000
csv.field_size_limit(1024 * 1024)


def category_label(theme: str) -> str:
    special = {"mateIn1": "Mate in 1", "mateIn2": "Mate in 2", "mateIn3": "Mate in 3", "mateIn4": "Mate in 4", "mateIn5": "Mate in 5"}
    if theme in special:
        return special[theme]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", theme).replace("_", " ").capitalize()


def normalize_puzzle(item: dict, *, lichess: bool = False) -> dict:
    if not isinstance(item, dict):
        raise ValueError("Every puzzle must be a JSON object.")
    identifier = str(item.get("PuzzleId" if lichess else "id", "")).strip()
    if not identifier or len(identifier) > 120:
        raise ValueError("A puzzle needs an id of 1–120 characters.")
    fen = item.get("FEN" if lichess else "fen", "")
    board = chess.Board(fen)
    if not board.is_valid() or board.is_game_over():
        raise ValueError("The puzzle FEN must be a valid, playable standard chess position.")
    moves = item.get("Moves" if lichess else "moves", [])
    if isinstance(moves, str):
        moves = moves.split()
    if not isinstance(moves, list) or not 1 <= len(moves) <= 64:
        raise ValueError("A puzzle needs a legal solution line with 1–64 moves.")
    for_move = []
    for value in moves:
        try:
            for_move.append(chess.Move.from_uci(value))
        except (ValueError, TypeError) as exc:
            raise ValueError("The solution contains an invalid UCI move.") from exc
    if lichess:
        # Lichess FEN precedes the opponent's setup move, not the solver's turn.
        setup = for_move.pop(0)
        if setup not in board.legal_moves:
            raise ValueError("The Lichess setup move is illegal.")
        board.push(setup)
        if not for_move:
            raise ValueError("The Lichess puzzle has no solution after its setup move.")
    start_fen = board.fen()
    for move in for_move:
        if move not in board.legal_moves:
            raise ValueError(f"The solution contains an illegal move: {move.uci()}.")
        board.push(move)
    rating = int(item.get("Rating" if lichess else "rating", 1000))
    if not 0 <= rating <= 4000:
        raise ValueError("Puzzle ratings must be between 0 and 4000.")
    themes = item.get("Themes" if lichess else "themes", [])
    if isinstance(themes, str):
        themes = themes.split()
    if not isinstance(themes, list) or len(themes) > 30 or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", t) for t in themes):
        raise ValueError("Puzzle themes must be short category identifiers.")
    themes = sorted(set(themes))
    for theme in themes:
        match = re.fullmatch(r"mateIn([1-5])", theme)
        if match and (len(for_move) != int(match[1]) * 2 - 1 or not board.is_checkmate()):
            raise ValueError(f"The {theme} label does not match the solution's mate length.")
    game_url = str(item.get("GameUrl" if lichess else "game_url", ""))[:1000]
    if game_url and not game_url.startswith(("https://", "http://")):
        raise ValueError("Game links must start with http:// or https://.")
    normalized = {"id": identifier, "fen": start_fen, "moves": [m.uci() for m in for_move], "rating": rating,
                  "themes": themes, "game_url": game_url, "source": "Lichess" if lichess else str(item.get("source", "Imported pack"))[:120]}
    normalized["fingerprint"] = hashlib.sha256((start_fen + " " + " ".join(normalized["moves"])).encode()).hexdigest()
    return normalized


class LibraryStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(self.data_dir / "trainer.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS puzzles (
                id TEXT PRIMARY KEY, fen TEXT NOT NULL, moves TEXT NOT NULL, rating INTEGER NOT NULL,
                themes TEXT NOT NULL, game_url TEXT NOT NULL, source TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS puzzle_themes (
                puzzle_id TEXT NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE, theme TEXT NOT NULL,
                PRIMARY KEY (puzzle_id, theme)
            );
            CREATE INDEX IF NOT EXISTS ix_puzzle_rating ON puzzles(rating);
            CREATE INDEX IF NOT EXISTS ix_puzzle_theme ON puzzle_themes(theme,puzzle_id);
            CREATE TABLE IF NOT EXISTS intuition (
                id TEXT PRIMARY KEY, fen TEXT UNIQUE NOT NULL, game_url TEXT NOT NULL, source TEXT NOT NULL,
                cp INTEGER NOT NULL, depth INTEGER NOT NULL, engine TEXT NOT NULL, provenance TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, mode TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                solved INTEGER NOT NULL, failed INTEGER NOT NULL, accuracy REAL, summary TEXT NOT NULL, results TEXT NOT NULL);
        """)
        self.db.commit()

    def add_puzzle(self, item: dict) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO puzzles VALUES (?,?,?,?,?,?,?,?)", (
            item["id"], item["fen"], json.dumps(item["moves"]), item["rating"], json.dumps(item["themes"]), item["game_url"], item["source"], item["fingerprint"]))
        if cursor.rowcount != 1:
            return False
        self.db.executemany("INSERT INTO puzzle_themes VALUES (?,?)", [(item["id"], t) for t in item["themes"]])
        return True

    def add_intuition(self, item: dict) -> bool:
        board = chess.Board(item["fen"])
        if not board.is_valid() or board.is_game_over():
            raise ValueError("Invalid intuition position.")
        if not 100 <= int(item["cp"]) <= 300:
            raise ValueError("Intuition positions must evaluate from +100 to +300 centipawns for the mover.")
        if not item.get("game_url") and not item.get("provenance"):
            raise ValueError("Intuition positions need real-game provenance.")
        cursor = self.db.execute("INSERT OR IGNORE INTO intuition VALUES (?,?,?,?,?,?,?,?)", (
            item["id"], board.fen(), item.get("game_url", ""), item.get("source", "Imported PGN"),
            int(item["cp"]), int(item.get("depth", 0)), item.get("engine", "Stockfish"), json.dumps(item.get("provenance", {}))))
        return cursor.rowcount == 1

    def seed(self, project_dir: Path):
        puzzles = project_dir / "data" / "puzzles.json"
        if puzzles.is_file():
            import_puzzle_file(self, puzzles)
        intuition = project_dir / "data" / "intuition.json"
        if intuition.is_file():
            with self.lock, self.db:
                for item in json.loads(intuition.read_text(encoding="utf-8")):
                    self.add_intuition(item)

    def categories(self) -> list[dict]:
        with self.lock:
            return [{"id": row["theme"], "label": category_label(row["theme"]), "count": row["n"]}
                    for row in self.db.execute("SELECT theme,COUNT(*) n FROM puzzle_themes GROUP BY theme ORDER BY theme")]

    def counts(self) -> dict:
        with self.lock:
            return {name: self.db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("puzzles", "intuition")}

    @staticmethod
    def puzzle_from_row(row, private=False):
        value = dict(row)
        value["themes"] = json.loads(value["themes"])
        value.pop("fingerprint", None)
        if private:
            value["moves"] = json.loads(value["moves"])
        else:
            value.pop("moves", None)
        return value

    def library(self, theme="", min_rating=0, max_rating=4000, sort="rating_asc", q="", page=1, limit=20):
        where, params = ["p.rating BETWEEN ? AND ?"], [min_rating, max_rating]
        if theme:
            where.append("EXISTS (SELECT 1 FROM puzzle_themes t WHERE t.puzzle_id=p.id AND t.theme=?)")
            params.append(theme)
        if q:
            where.append("(p.id LIKE ? OR p.source LIKE ? OR p.game_url LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        clause = " AND ".join(where)
        order = {"rating_asc": "p.rating ASC,p.id ASC", "rating_desc": "p.rating DESC,p.id ASC", "random": "RANDOM()"}[sort]
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM puzzles p WHERE {clause}", params).fetchone()[0]
            rows = self.db.execute(f"SELECT p.* FROM puzzles p WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, (page - 1) * limit]).fetchall()
            return {"items": [self.puzzle_from_row(row) for row in rows], "total": total, "page": page, "categories": self.categories()}

    def puzzle_pool(self, theme="", puzzle_id=None):
        with self.lock:
            if puzzle_id:
                rows = self.db.execute("SELECT id,rating FROM puzzles WHERE id=?", [puzzle_id]).fetchall()
            elif theme:
                rows = self.db.execute("SELECT p.id,p.rating FROM puzzles p JOIN puzzle_themes t ON t.puzzle_id=p.id WHERE t.theme=?", [theme]).fetchall()
            else:
                rows = self.db.execute("SELECT id,rating FROM puzzles").fetchall()
            return {row["id"]: row["rating"] for row in rows}

    def puzzle(self, identifier):
        with self.lock:
            row = self.db.execute("SELECT * FROM puzzles WHERE id=?", [identifier]).fetchone()
            return self.puzzle_from_row(row, private=True) if row else None

    def intuition_pool(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM intuition").fetchall()
            pool = [dict(row) for row in rows]
            random.shuffle(pool)
            return pool

    def save_history(self, session):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO history (id,mode,solved,failed,accuracy,summary,results) VALUES (?,?,?,?,?,?,?)", (
                session.id, session.mode, session.solved, session.failed, session.summary.get("accuracy"),
                json.dumps(session.summary), json.dumps(session.results)))

    def history(self):
        with self.lock:
            row = self.db.execute("SELECT COUNT(*) sessions,COALESCE(SUM(solved),0) solved,COALESCE(MAX(solved),0) best_score,AVG(accuracy) average_accuracy FROM history").fetchone()
            recent = []
            for record in self.db.execute("SELECT id,mode,finished_at,summary FROM history ORDER BY finished_at DESC,rowid DESC LIMIT 8"):
                value = dict(record)
                value["summary"] = json.loads(value["summary"])
                recent.append(value)
            return dict(row), recent

    def close(self):
        with self.lock:
            self.db.close()


class _LimitedReader(io.RawIOBase):
    def __init__(self, stream: BinaryIO, limit: int | None):
        self.stream, self.limit, self.count = stream, limit, 0

    def readable(self):
        return True

    def readinto(self, target):
        chunk = self.stream.read(len(target))
        self.count += len(chunk)
        if self.limit is not None and self.count > self.limit:
            raise ValueError("Expanded file limit reached. Use scripts/import_library.py for larger libraries.")
        target[:len(chunk)] = chunk
        return len(chunk)


def import_puzzle_file(store: LibraryStore, file: str | Path | BinaryIO, *, filename: str | None = None,
                       max_rows: int | None = MAX_IMPORT_ROWS, max_expanded_bytes: int | None = MAX_EXPANDED_BYTES) -> dict:
    """Stream a local CSV/CSV.zst or import a JSON pack; None removes CLI limits."""
    owned = isinstance(file, (str, Path))
    path = Path(file) if owned else None
    stream = path.open("rb") if owned else file
    filename = filename or (path.name if path else "puzzles.csv")
    name = filename.lower()
    result = {"imported": 0, "skipped": 0, "errors": []}
    def error(message):
        if len(result["errors"]) < 20:
            result["errors"].append(message)
    decompressor = None
    text_stream = None
    try:
        if name.endswith(".zst"):
            decompressor = zstandard.ZstdDecompressor().stream_reader(stream, closefd=False)
            source = decompressor
        else:
            source = stream
        text_stream = io.TextIOWrapper(io.BufferedReader(_LimitedReader(source, max_expanded_bytes)), encoding="utf-8-sig", newline="")
        if name.endswith(".json"):
            payload = json.load(text_stream)
            rows = payload.get("puzzles", []) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ValueError("JSON puzzle packs must be a list or an object containing a puzzles list.")
            lichess = False
        elif name.endswith((".csv", ".csv.zst")):
            rows = csv.DictReader(text_stream)
            if not {"PuzzleId", "FEN", "Moves", "Rating", "Themes"}.issubset(rows.fieldnames or []):
                raise ValueError("CSV files need Lichess headers: PuzzleId,FEN,Moves,Rating,Themes.")
            lichess = True
        else:
            raise ValueError("Choose a .csv, .csv.zst or .json puzzle file.")
        with store.lock, store.db:
            try:
                for row_number, item in enumerate(rows, 1):
                    if max_rows is not None and row_number > max_rows:
                        error("Row limit reached. Use scripts/import_library.py for larger libraries.")
                        break
                    try:
                        normalized = normalize_puzzle(item, lichess=lichess)
                        if store.add_puzzle(normalized):
                            result["imported"] += 1
                        else:
                            result["skipped"] += 1
                    except (ValueError, TypeError, KeyError, OverflowError) as exc:
                        result["skipped"] += 1
                        error(f"Row {row_number}: {exc}")
            except (ValueError, UnicodeError, csv.Error, zstandard.ZstdError) as exc:
                error(str(exc))
    except (ValueError, UnicodeError, csv.Error, zstandard.ZstdError, json.JSONDecodeError) as exc:
        error(str(exc))
    finally:
        if text_stream:
            text_stream.close()
        if decompressor:
            decompressor.close()
        if owned:
            stream.close()
    return result
