"""Server-owned chess sessions, legal move checks, clocks and scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from threading import RLock
import time
import uuid

import chess
from fastapi import HTTPException

from .engine import EngineService, EngineUnavailable
from .library import LibraryStore


@dataclass
class Session:
    id: str
    mode: str
    theme: str
    duration: int
    initial_lives: int
    current_rating: int
    total_positions: int
    min_eval: int
    max_eval: int
    lives: int = 3
    state: str = "playing"
    solved: int = 0
    failed: int = 0
    current_position: int = 0
    started: float = 0
    board: chess.Board = field(default_factory=chess.Board)
    player_color: chess.Color = chess.WHITE
    current: dict | None = None
    cursor: int = 0
    last_move: str | None = None
    transition: list[dict[str, str]] = field(default_factory=list)
    feedback: dict | None = None
    results: list = field(default_factory=list)
    summary: dict | None = None
    pool: dict = field(default_factory=dict)
    intuition_pool: list = field(default_factory=list)
    baseline: dict | None = None
    used: set = field(default_factory=set)


class SessionManager:
    def __init__(self, store: LibraryStore, engine: EngineService, clock=time.monotonic):
        self.store, self.engine, self.clock = store, engine, clock
        self.lock = RLock()
        self.sessions: dict[str, Session] = {}

    def create(self, config) -> dict:
        with self.lock:
            # Bounded local state; keep finished runs available until capacity needs reclaiming.
            if len(self.sessions) >= 512:
                for identifier, previous in list(self.sessions.items()):
                    self.expire(previous)
                    if previous.state == "finished":
                        self.sessions.pop(identifier)
                    if len(self.sessions) < 512:
                        break
                if len(self.sessions) >= 512:
                    raise HTTPException(409, "Too many active sessions. Stop a session before starting another.")
            duration = 0 if config.mode == "free" else config.duration_seconds
            session = Session(
                id=str(uuid.uuid4()), mode=config.mode, theme=config.theme, duration=duration,
                initial_lives=config.lives, lives=config.lives, current_rating=config.start_rating,
                total_positions=config.positions, min_eval=config.min_eval, max_eval=config.max_eval,
            )
            if session.mode == "intuition":
                self.engine.ensure()
                session.intuition_pool = self.store.intuition_pool()
                if not session.intuition_pool:
                    raise HTTPException(409, "No real-game intuition positions are available. Import a PGN game to build this library.")
            else:
                theme = session.theme
                if session.mode == "mate" and not theme:
                    theme = "mateIn2"
                    session.theme = theme
                session.pool = self.store.puzzle_pool(theme, config.puzzle_id)
                session.total_positions = len(session.pool)
                if not session.pool:
                    raise HTTPException(409, "No puzzles match this category. Choose another category or import a puzzle library.")
            if not self.load_challenge(session):
                raise HTTPException(409, "No positions match the current evaluation range at the live engine limit. Import more PGN games or widen the range.")
            # Clocks start only when initial challenge preparation has completed.
            session.started = self.clock()
            self.sessions[session.id] = session
            return self.snapshot(session)

    def load_challenge(self, session: Session) -> bool:
        session.transition = []
        if session.mode == "intuition":
            session.current = None
            session.baseline = None
            while session.intuition_pool:
                candidate = session.intuition_pool.pop()
                board = chess.Board(candidate["fen"])
                baseline = self.engine.analyze(board)
                if session.min_eval <= baseline["cp"] <= session.max_eval and baseline["best_move"]:
                    session.current, session.baseline = candidate, baseline
                    session.board = board
                    break
            if session.current is None:
                return False
        else:
            remaining = [(identifier, rating) for identifier, rating in session.pool.items() if identifier not in session.used]
            if not remaining:
                return False
            near = [(identifier, rating) for identifier, rating in remaining if session.current_rating - 150 <= rating <= session.current_rating + 250]
            if not near:
                closest = min(abs(rating - session.current_rating) for _, rating in remaining)
                near = [(identifier, rating) for identifier, rating in remaining if abs(rating - session.current_rating) <= closest + 75]
            identifier, _ = random.choice(near)
            session.used.add(identifier)
            session.current = self.store.puzzle(identifier)
            session.board = chess.Board(session.current["fen"])
        session.player_color = session.board.turn
        session.cursor = 0
        session.last_move = None
        session.feedback = None
        session.current_position += 1
        session.state = "playing"
        return True

    def get(self, identifier: str) -> Session:
        session = self.sessions.get(identifier)
        if session is None:
            raise HTTPException(404, "Session not found. Start a new training session.")
        self.expire(session)
        return session

    def remaining(self, session: Session) -> int:
        if not session.duration:
            return 0
        if session.state == "finished":
            elapsed = session.summary["duration_seconds"] if session.summary else 0
        else:
            elapsed = max(0, self.clock() - session.started)
        return max(0, math.ceil(session.duration - elapsed))

    def expire(self, session: Session):
        if session.state != "finished" and session.duration and self.clock() - session.started >= session.duration:
            session.transition = []
            self.finish(session, "Time expired")

    def finish(self, session: Session, reason: str):
        if session.state == "finished":
            return
        session.state = "finished"
        session.summary = {"solved": session.solved, "failed": session.failed, "reason": reason,
                           "duration_seconds": round(max(0, self.clock() - session.started), 1)}
        if session.mode == "intuition":
            session.summary["accuracy"] = round(sum(result["accuracy"] for result in session.results) / len(session.results), 1) if session.results else None
        self.store.save_history(session)

    def snapshot(self, session: Session) -> dict:
        challenge = None
        if session.current:
            challenge = {key: session.current.get(key, "" if key not in ("rating", "themes") else (0 if key == "rating" else []))
                         for key in ("id", "rating", "themes", "game_url", "source")}
            if session.mode == "intuition":
                challenge["rating"] = None
                challenge["depth"] = session.baseline["depth"]
                challenge["engine"] = session.baseline["engine"]
                # Header metadata is real-game provenance and contains no move solution.
                provenance = session.current.get("provenance", {})
                if isinstance(provenance, str):
                    import json
                    provenance = json.loads(provenance)
                challenge["provenance"] = provenance
        return {
            "id": session.id, "mode": session.mode, "state": session.state, "lives": session.lives,
            "solved": session.solved, "failed": session.failed, "current_rating": session.current_rating,
            "remaining_seconds": self.remaining(session), "current_position": session.current_position,
            "total_positions": session.total_positions,
            "board": {"fen": session.board.fen(), "turn": "w" if session.board.turn else "b",
                      "player_color": "w" if session.player_color else "b", "legal_moves": [move.uci() for move in session.board.legal_moves] if session.state == "playing" else [],
                      "last_move": session.last_move, "transition": list(session.transition)},
            "challenge": challenge, "feedback": session.feedback, "results": session.results,
            "summary": session.summary,
        }

    def refresh(self, identifier: str) -> dict:
        with self.lock:
            return self.snapshot(self.get(identifier))

    def move(self, identifier: str, uci: str) -> dict:
        with self.lock:
            session = self.get(identifier)
            session.transition = []
            if session.state != "playing":
                return self.snapshot(session)
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                session.feedback = {"type": "illegal", "message": "That move is not legal in this position.", "played_move": uci}
                return self.snapshot(session)
            if move not in session.board.legal_moves:
                session.feedback = {"type": "illegal", "message": "That move is not legal in this position. Your lives are unchanged.", "played_move": uci}
                return self.snapshot(session)
            if session.mode == "intuition":
                # Both scores originate from the same board and the same player's POV.
                baseline = session.baseline
                played = self.engine.analyze(session.board, move=move)
                self.expire(session)
                if session.state == "finished":
                    return self.snapshot(session)
                loss = max(0, baseline["cp"] - played["cp"])
                accuracy = round(100 * math.exp(-loss / 200), 1)
                session.feedback = {"type": "evaluated", "message": f"Training score: {accuracy:.1f}%. Centipawn loss: {loss}.",
                                    "best_move": baseline["best_move"], "played_move": uci, "before_cp": baseline["cp"],
                                    "after_cp": played["cp"], "loss_cp": loss, "accuracy": accuracy, "pv_san": baseline["pv_san"]}
                session.results.append({**session.feedback, "challenge_id": session.current["id"], "fen": session.board.fen(),
                                        "depth": baseline["depth"], "engine": baseline["engine"]})
                session.board.push(move)
                session.transition.append({"uci": uci, "fen": session.board.fen()})
                session.last_move = uci
                session.solved += 1
                session.state = "between"
                if session.solved >= session.total_positions:
                    self.finish(session, "Round complete")
                return self.snapshot(session)
            expected = session.current["moves"][session.cursor]
            test_board = session.board.copy(stack=False)
            test_board.push(move)
            mate_challenge = any(theme.startswith("mateIn") or theme == "mate" for theme in session.current["themes"])
            alternative_mate = mate_challenge and test_board.is_checkmate()
            if uci != expected and not alternative_mate:
                session.failed += 1
                if session.mode != "free":
                    session.lives -= 1
                session.feedback = {"type": "wrong", "message": "Try another move. The position is unchanged.", "played_move": uci}
                if session.mode != "free" and session.lives <= 0:
                    self.finish(session, "No lives remaining")
                return self.snapshot(session)
            session.board.push(move)
            session.transition.append({"uci": uci, "fen": session.board.fen()})
            session.last_move = uci
            session.cursor += 1
            if alternative_mate or session.cursor >= len(session.current["moves"]):
                session.solved += 1
                session.state = "between"
                session.feedback = {"type": "solved", "message": "Puzzle solved. Continue to the next challenge.", "played_move": uci}
            else:
                # The line is validated at import, including all opponent replies.
                reply = session.current["moves"][session.cursor]
                session.board.push_uci(reply)
                session.transition.append({"uci": reply, "fen": session.board.fen()})
                session.last_move = reply
                session.cursor += 1
                if session.cursor >= len(session.current["moves"]):
                    session.solved += 1
                    session.state = "between"
                    session.feedback = {"type": "solved", "message": "Puzzle solved. Continue to the next challenge.", "played_move": uci}
                else:
                    session.feedback = {"type": "correct", "message": "Correct. Find your next move.", "played_move": uci}
            return self.snapshot(session)

    def next(self, identifier: str) -> dict:
        with self.lock:
            session = self.get(identifier)
            if session.state == "finished":
                return self.snapshot(session)
            if session.state != "between":
                raise HTTPException(409, "Complete the current position before moving to the next challenge.")
            if session.mode != "intuition":
                session.current_rating = min(4000, session.current_rating + 100)
            if not self.load_challenge(session):
                self.finish(session, "Library complete")
            self.expire(session)
            return self.snapshot(session)

    def stop(self, identifier: str) -> dict:
        with self.lock:
            session = self.get(identifier)
            self.finish(session, "Stopped")
            return self.snapshot(session)
