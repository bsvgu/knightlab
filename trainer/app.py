"""FastAPI application served only on the local loopback interface."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import io
import os
from pathlib import Path
import time
from typing import Literal
from urllib.parse import urlsplit

import chess.pgn
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .engine import EngineService, EngineUnavailable
from .library import LibraryStore, MAX_UPLOAD_BYTES, import_puzzle_file
from .sessions import SessionManager

PROJECT_DIR = Path(__file__).resolve().parent.parent


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["mate", "tactics", "free", "intuition"] = "mate"
    theme: str = Field(default="mateIn2", max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    duration_seconds: Literal[0, 180, 300] = 180
    lives: int = Field(default=3, ge=1, le=10)
    start_rating: int = Field(default=800, ge=0, le=4000)
    positions: int = Field(default=10, ge=1, le=50)
    min_eval: int = Field(default=100, ge=100, le=300)
    max_eval: int = Field(default=300, ge=100, le=300)
    puzzle_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def eval_order(self):
        if self.min_eval > self.max_eval:
            raise ValueError("Minimum evaluation must not exceed maximum evaluation.")
        return self


class MoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uci: str = Field(min_length=4, max_length=5)


def import_pgn_file(store: LibraryStore, engine: EngineService, text: str, *, max_positions: int = 160, max_games: int = 50) -> dict:
    engine.ensure()
    result = {"imported": 0, "inspected": 0, "skipped": 0, "errors": []}
    stream = io.StringIO(text)
    started = time.monotonic()
    games_read = 0
    def error(message):
        if len(result["errors"]) < 20:
            result["errors"].append(message)
    while games_read < max_games and result["inspected"] < max_positions and time.monotonic() - started < 45:
        try:
            game = chess.pgn.read_game(stream)
        except (ValueError, UnicodeError) as exc:
            error(f"PGN parse error: {exc}")
            break
        if game is None:
            break
        games_read += 1
        if game.errors:
            error(f"Game {games_read}: invalid moves; this game was skipped.")
            result["skipped"] += 1
            continue
        # Real-game training positions are taken from normal game starts with player provenance.
        if game.headers.get("SetUp") == "1" or game.headers.get("FEN") or game.headers.get("White", "?") == "?" or game.headers.get("Black", "?") == "?":
            error(f"Game {games_read}: use a normal game PGN with White and Black player headers.")
            result["skipped"] += 1
            continue
        board = game.board()
        site = game.headers.get("Site", "")
        game_url = site[:1000] if site.startswith(("https://", "http://")) else ""
        provenance = {key: str(game.headers.get(key, "?"))[:200] for key in ("Event", "Site", "Date", "White", "Black", "Result")}
        for ply, move in enumerate(game.mainline_moves(), 1):
            if move not in board.legal_moves:
                error(f"Game {games_read}: illegal move at ply {ply}.")
                break
            board.push(move)
            if ply < 12 or ply > 120 or ply % 3 or board.is_game_over():
                continue
            if result["inspected"] >= max_positions or time.monotonic() - started >= 45:
                break
            with store.lock:
                duplicate = store.db.execute("SELECT 1 FROM intuition WHERE fen=?", [board.fen()]).fetchone()
            if duplicate:
                result["skipped"] += 1
                continue
            analysis = engine.analyze(board, importing=True)
            result["inspected"] += 1
            if not 100 <= analysis["cp"] <= 300:
                result["skipped"] += 1
                continue
            identifier = "pgn-" + hashlib.sha256(board.fen().encode()).hexdigest()[:20]
            item = {"id": identifier, "fen": board.fen(), "game_url": game_url, "source": "Imported PGN",
                    "cp": analysis["cp"], "depth": analysis["depth"], "engine": analysis["engine"],
                    "provenance": {**provenance, "ply": ply}}
            with store.lock, store.db:
                inserted = store.add_intuition(item)
            result["imported" if inserted else "skipped"] += 1
    if games_read == 0:
        error("No games found. Choose a valid .pgn file.")
    if result["inspected"] >= max_positions or games_read >= max_games or time.monotonic() - started >= 45:
        error("Analysis limit reached (160 positions, 50 games or 45 seconds). Use a smaller PGN to examine additional games.")
    if result["imported"] == 0 and not result["errors"]:
        error("No new positions evaluated between +1.00 and +3.00 pawns for the mover at the import analysis limit.")
    return result


def create_app(*, data_dir: str | Path | None = None, project_dir: Path | None = None,
               engine: EngineService | None = None, clock=time.monotonic, seed: bool = True) -> FastAPI:
    project_dir = Path(project_dir or PROJECT_DIR)
    data_dir = data_dir or os.environ.get("CHESS_TRAINER_DATA_DIR") or project_dir / ".data"
    store = LibraryStore(data_dir)
    if seed:
        store.seed(project_dir)
    engine_service = engine or EngineService(project_dir)
    sessions = SessionManager(store, engine_service, clock=clock)

    @asynccontextmanager
    async def lifespan(application):
        yield
        engine_service.close()
        store.close()

    application = FastAPI(title="Chess Trainer", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.store = store
    application.state.engine = engine_service
    application.state.sessions = sessions

    @application.middleware("http")
    async def local_security(request: Request, call_next):
        # Restrict hosts to loopback to prevent DNS rebinding against a local trainer.
        host = urlsplit("//" + request.headers.get("host", "")).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "This application is available only through localhost."}, status_code=403)
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected_origin:
            return JSONResponse({"detail": "Requests must come from the local Chess Trainer page."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            # A link from a local launcher/file may navigate into the app. It may
            # only open the root document, never fetch data or mutate state.
            root_document_navigation = (
                request.method == "GET"
                and request.url.path == "/"
                and request.headers.get("sec-fetch-mode") == "navigate"
                and request.headers.get("sec-fetch-dest") == "document"
            )
            if not root_document_navigation:
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
        length = request.headers.get("content-length", "0")
        try:
            if int(length) > MAX_UPLOAD_BYTES:
                return JSONResponse({"detail": "Upload limit is 256 MiB. Use scripts/import_library.py for larger libraries."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(EngineUnavailable)
    async def unavailable(request: Request, exc: EngineUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        messages = []
        for error in exc.errors()[:5]:
            field_name = ".".join(str(part) for part in error["loc"] if part not in ("body", "query", "path"))
            messages.append(f"{field_name}: {error['msg']}" if field_name else error["msg"])
        return JSONResponse({"detail": "Invalid request. " + "; ".join(messages)}, status_code=422)

    @application.get("/api/status")
    def status():
        history, recent = store.history()
        return {"engine": engine_service.status(), "counts": store.counts(), "categories": store.categories(),
                "history": history, "recent_sessions": recent}

    @application.get("/api/library")
    def library(theme: str = Query(default="", max_length=40), min_rating: int = Query(default=0, ge=0, le=4000),
                max_rating: int = Query(default=4000, ge=0, le=4000), sort: Literal["rating_asc", "rating_desc", "random"] = "rating_asc",
                q: str = Query(default="", max_length=100), page: int = Query(default=1, ge=1, le=1000000),
                limit: int = Query(default=20, ge=1, le=100)):
        if min_rating > max_rating:
            raise HTTPException(422, "Minimum rating must not exceed maximum rating.")
        return store.library(theme, min_rating, max_rating, sort, q, page, limit)

    @application.post("/api/import/puzzles")
    def import_puzzles(file: UploadFile = File(...)):
        if file.size is not None and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload limit is 256 MiB. Use scripts/import_library.py for larger libraries.")
        if not (file.filename or "").lower().endswith((".csv", ".csv.zst", ".json")):
            raise HTTPException(422, "Choose a .csv, .csv.zst or .json puzzle file.")
        try:
            return import_puzzle_file(store, file.file, filename=file.filename)
        finally:
            file.file.close()

    @application.post("/api/import/pgn")
    def import_pgn(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".pgn"):
            raise HTTPException(422, "Choose a .pgn game file.")
        engine_service.ensure()
        try:
            content = file.file.read(5 * 1024 * 1024 + 1)
            if len(content) > 5 * 1024 * 1024:
                raise HTTPException(413, "PGN uploads are limited to 5 MiB and 160 analyzed positions. Split large PGN collections into smaller files.")
            try:
                text = content.decode("utf-8-sig")
            except UnicodeError:
                raise HTTPException(422, "The PGN must be UTF-8 encoded.")
            return import_pgn_file(store, engine_service, text)
        finally:
            file.file.close()

    @application.post("/api/sessions")
    def create_session(config: SessionConfig):
        return sessions.create(config)

    @application.get("/api/sessions/{identifier}")
    def get_session(identifier: str):
        return sessions.refresh(identifier)

    @application.post("/api/sessions/{identifier}/move")
    def play_move(identifier: str, move: MoveInput):
        return sessions.move(identifier, move.uci)

    @application.post("/api/sessions/{identifier}/next")
    def next_challenge(identifier: str):
        return sessions.next(identifier)

    @application.post("/api/sessions/{identifier}/stop")
    def stop_session(identifier: str):
        return sessions.stop(identifier)

    web = project_dir / "web"
    if web.is_dir():
        application.mount("/", StaticFiles(directory=web, html=True), name="web")
    return application


app = create_app()
