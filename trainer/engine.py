"""A serialized, local Stockfish connection with explicit score perspective."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from threading import RLock

import chess
import chess.engine


class EngineUnavailable(RuntimeError):
    pass


class EngineService:
    def __init__(self, project_dir: Path, path: str | None = None):
        filename = "stockfish.exe" if os.name == "nt" else "stockfish"
        self.path = path or os.environ.get("STOCKFISH_PATH") or str(project_dir / ".runtime" / filename)
        self.lock = RLock()
        self.engine: chess.engine.SimpleEngine | None = None
        self.name = "Stockfish (not installed)"
        self.error: str | None = None

    def ensure(self) -> chess.engine.SimpleEngine:
        with self.lock:
            if self.engine is not None:
                return self.engine
            executable = shutil.which(self.path) or self.path
            if not Path(executable).is_file():
                raise EngineUnavailable("Stockfish is unavailable. Run the engine setup script or set STOCKFISH_PATH to a Stockfish executable.")
            try:
                engine = chess.engine.SimpleEngine.popen_uci(executable, timeout=15)
                options = {}
                if "Threads" in engine.options:
                    options["Threads"] = 1
                if "Hash" in engine.options:
                    options["Hash"] = 64
                engine.configure(options)
                self.engine = engine
                self.name = engine.id.get("name", "Stockfish")
                self.error = None
                return engine
            except (OSError, chess.engine.EngineError, TimeoutError) as exc:
                self.error = "Stockfish could not start. Check the installed executable and its permissions."
                raise EngineUnavailable(self.error) from exc

    def status(self) -> dict:
        try:
            self.ensure()
            return {"available": True, "name": self.name}
        except EngineUnavailable:
            return {"available": False, "name": self.name}

    def analyze(self, board: chess.Board, *, move: chess.Move | None = None, importing: bool = False) -> dict:
        """Analyze the same root board, always returning cp from its mover's POV."""
        with self.lock:
            engine = self.ensure()
            try:
                # Remove analysis cache dependence across baseline/root-move trials.
                if "Clear Hash" in engine.options:
                    engine.configure({"Clear Hash": None})
                info = engine.analyse(
                    board,
                    chess.engine.Limit(depth=13 if importing else 16, nodes=20000 if importing else 80000),
                    root_moves=[move] if move is not None else None,
                    info=chess.engine.INFO_SCORE | chess.engine.INFO_PV | chess.engine.INFO_BASIC,
                )
                score = info.get("score")
                if score is None:
                    raise EngineUnavailable("Stockfish returned no evaluation. Please retry this position.")
                cp = score.pov(board.turn).score(mate_score=100000)
                if cp is None:
                    raise EngineUnavailable("Stockfish returned no numerical evaluation.")
                pv = info.get("pv", [])
                san_board = board.copy(stack=False)
                pv_san = []
                for variation_move in pv[:8]:
                    if variation_move not in san_board.legal_moves:
                        break
                    pv_san.append(san_board.san(variation_move))
                    san_board.push(variation_move)
                return {
                    "cp": int(cp), "depth": int(info.get("depth", 0)), "engine": self.name,
                    "best_move": pv[0].uci() if pv else None, "pv_san": pv_san,
                }
            except (chess.engine.EngineError, TimeoutError, OSError) as exc:
                self.close()
                raise EngineUnavailable("Stockfish analysis failed. Check the engine installation and retry.") from exc

    def close(self):
        with self.lock:
            if self.engine is not None:
                try:
                    self.engine.quit()
                except Exception:
                    pass
                self.engine = None
