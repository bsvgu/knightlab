from __future__ import annotations

import csv
import io
import json
import math
import os
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient
import zstandard

from trainer.app import SessionConfig, create_app, import_pgn_file
from trainer.engine import EngineService, EngineUnavailable
from trainer.library import LibraryStore, import_puzzle_file, normalize_puzzle

ROOT = Path(__file__).resolve().parent.parent
ENGINE_BINARY = ROOT / ".runtime" / ("stockfish.exe" if os.name == "nt" else "stockfish")
PUZZLE = {
    "id": "mate-white", "fen": "6k1/5ppp/5n2/pp6/4b1rP/5N1Q/Pq2r1P1/3R2RK w - - 5 33",
    "moves": ["d1d8", "f6e8", "d8e8"], "rating": 800,
    "themes": ["mate", "mateIn2", "backRankMate"], "game_url": "https://lichess.org/mhIZR6Mc/black#64", "source": "Test fixture",
}


def mirrored_puzzle():
    value = dict(PUZZLE, id="mate-black", rating=1000)
    value["fen"] = chess.Board(PUZZLE["fen"]).mirror().fen()
    value["moves"] = [chess.Move(chess.square_mirror(m.from_square), chess.square_mirror(m.to_square), promotion=m.promotion).uci()
                      for m in map(chess.Move.from_uci, PUZZLE["moves"])]
    return value


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def local(tmp_path):
    clock = Clock()
    app = create_app(data_dir=tmp_path, seed=False, clock=clock,
                     engine=EngineService(ROOT, path="/missing/stockfish"))
    with app.state.store.lock, app.state.store.db:
        app.state.store.add_puzzle(normalize_puzzle(PUZZLE))
        app.state.store.add_puzzle(normalize_puzzle(mirrored_puzzle()))
    with TestClient(app, base_url="http://localhost") as client:
        yield client, app, clock


def start(client, **overrides):
    config = {"mode": "mate", "theme": "mateIn2", "duration_seconds": 180, "lives": 3, "start_rating": 800}
    response = client.post("/api/sessions", json=config | overrides)
    assert response.status_code == 200, response.text
    return response.json()


def play(client, session, move):
    response = client.post(f"/api/sessions/{session['id']}/move", json={"uci": move})
    assert response.status_code == 200, response.text
    return response.json()


def replay_transition(initial_fen, response, expected_moves):
    frames = response["board"]["transition"]
    assert [frame["uci"] for frame in frames] == expected_moves
    board = chess.Board(initial_fen)
    for frame in frames:
        move = chess.Move.from_uci(frame["uci"])
        assert move in board.legal_moves
        board.push(move)
        assert frame["fen"] == board.fen()
    assert board.fen() == response["board"]["fen"]


def test_full_line_auto_reply_and_progression_no_repeats(local):
    client, app, _ = local
    session = start(client, puzzle_id=PUZZLE["id"])
    assert "moves" not in session["challenge"]
    assert session["lives"] == 3 and session["remaining_seconds"] == 180
    assert session["board"]["transition"] == []
    initial_fen = session["board"]["fen"]
    session = play(client, session, "d1d8")
    replay_transition(initial_fen, session, ["d1d8", "f6e8"])
    board = chess.Board(PUZZLE["fen"])
    board.push_uci("d1d8")
    board.push_uci("f6e8")
    assert session["board"]["fen"] == board.fen()
    assert session["feedback"]["type"] == "correct"
    assert session["state"] == "playing" and session["solved"] == 0
    initial_fen = session["board"]["fen"]
    session = play(client, session, "d8e8")
    replay_transition(initial_fen, session, ["d8e8"])
    assert session["state"] == "between" and session["solved"] == 1
    assert chess.Board(session["board"]["fen"]).is_checkmate()
    session = client.post(f"/api/sessions/{session['id']}/next", json={}).json()
    assert session["state"] == "finished" and session["summary"]["reason"] == "Library complete"
    assert session["board"]["transition"] == []
    assert client.get("/api/status").json()["history"]["solved"] == 1

    session = start(client)
    seen = set()
    for _ in range(2):
        identifier = session["challenge"]["id"]
        assert identifier not in seen
        seen.add(identifier)
        for uci in app.state.store.puzzle(identifier)["moves"][::2]:
            session = play(client, session, uci)
        session = client.post(f"/api/sessions/{session['id']}/next", json={}).json()
        assert session["board"]["transition"] == []
    assert len(seen) == 2 and session["state"] == "finished"
    assert session["current_rating"] == 1000


def test_wrong_moves_cost_lives_but_preserve_board_illegal_does_not(local):
    client, _, _ = local
    session = start(client, puzzle_id=PUZZLE["id"])
    initial = session["board"]["fen"]
    session = play(client, session, "a1a8")
    assert session["feedback"]["type"] == "illegal"
    assert session["board"]["transition"] == []
    assert session["lives"] == 3 and session["failed"] == 0
    wrong = next(move for move in chess.Board(initial).legal_moves if move.uci() != "d1d8")
    for expected_lives in (2, 1, 0):
        session = play(client, session, wrong.uci())
        assert session["lives"] == expected_lives
        assert session["board"]["fen"] == initial
        assert session["board"]["transition"] == []
    assert session["state"] == "finished" and session["failed"] == 3
    assert session["summary"]["reason"] == "No lives remaining"
    client.post(f"/api/sessions/{session['id']}/stop", json={})
    assert client.get("/api/status").json()["history"]["sessions"] == 1


def test_timer_is_server_owned_and_free_is_unlimited(local):
    client, _, clock = local
    session = start(client)
    clock.now += 179.1
    assert client.get(f"/api/sessions/{session['id']}").json()["remaining_seconds"] == 1
    clock.now += 1
    expired = play(client, session, "d1d8")
    assert expired["state"] == "finished" and expired["solved"] == 0
    assert expired["summary"]["reason"] == "Time expired"
    assert expired["board"]["transition"] == []
    free = start(client, mode="free", puzzle_id=PUZZLE["id"])
    clock.now += 100000
    free = play(client, free, "h4h5")
    assert free["state"] == "playing" and free["lives"] == 3 and free["remaining_seconds"] == 0


def test_move_transitions_clear_after_wrong_illegal_and_expired_request(local):
    client, _, clock = local
    session = start(client, puzzle_id=PUZZLE["id"])
    session = play(client, session, "d1d8")
    assert len(session["board"]["transition"]) == 2
    original_fen = session["board"]["fen"]
    board = chess.Board(original_fen)
    wrong = next(move for move in board.legal_moves if move.uci() != "d8e8")
    session = play(client, session, wrong.uci())
    assert session["feedback"]["type"] == "wrong"
    assert session["board"]["fen"] == original_fen and session["board"]["transition"] == []

    session = start(client, puzzle_id=PUZZLE["id"])
    session = play(client, session, "d1d8")
    session = play(client, session, "a1a8")
    assert session["feedback"]["type"] == "illegal" and session["board"]["transition"] == []

    session = start(client, puzzle_id=PUZZLE["id"])
    session = play(client, session, "d1d8")
    assert len(session["board"]["transition"]) == 2
    clock.now += 181
    session = play(client, session, "d8e8")
    assert session["state"] == "finished" and session["board"]["transition"] == []


def test_library_filters_sort_and_hidden_solutions(local):
    client, _, _ = local
    response = client.get("/api/library", params={"theme": "mateIn2", "min_rating": 900, "max_rating": 1100}).json()
    assert response["total"] == 1 and response["items"][0]["id"] == "mate-black"
    assert "moves" not in response["items"][0]
    assert response["categories"] and all(category["count"] == 2 for category in response["categories"])
    items = client.get("/api/library", params={"sort": "rating_desc"}).json()["items"]
    assert [item["rating"] for item in items] == [1000, 800]
    assert client.get("/api/library", params={"theme": "unknown"}).json()["total"] == 0
    assert client.post("/api/sessions", json={"theme": "unknown"}).status_code == 409
    assert client.get("/api/library", params={"min_rating": 1000, "max_rating": 800}).status_code == 422


def lichess_csv():
    value = io.StringIO()
    writer = csv.DictWriter(value, fieldnames=["PuzzleId", "FEN", "Moves", "Rating", "Themes", "GameUrl"])
    writer.writeheader()
    writer.writerow({"PuzzleId": "fools-mate", "FEN": chess.STARTING_FEN, "Moves": "f2f3 e7e5 g2g4 d8h4",
                     "Rating": 850, "Themes": "mate mateIn2", "GameUrl": "https://lichess.org/example"})
    return value.getvalue().encode()


def test_csv_normalizes_opponent_setup_streaming_zst_and_duplicates(tmp_path):
    store = LibraryStore(tmp_path)
    try:
        first = import_puzzle_file(store, io.BytesIO(lichess_csv()), filename="pack.csv")
        assert first == {"imported": 1, "skipped": 0, "errors": []}
        puzzle = store.puzzle("fools-mate")
        board = chess.Board()
        board.push_uci("f2f3")
        assert puzzle["fen"] == board.fen()
        assert puzzle["moves"] == ["e7e5", "g2g4", "d8h4"]
        assert chess.Board(puzzle["fen"]).turn == chess.BLACK
        compressed = zstandard.ZstdCompressor().compress(lichess_csv())
        again = import_puzzle_file(store, io.BytesIO(compressed), filename="pack.csv.zst")
        assert again == {"imported": 0, "skipped": 1, "errors": []}
        invalid = import_puzzle_file(store, io.BytesIO(b"bad,csv\n1,2\n"), filename="bad.csv")
        assert invalid["errors"] and invalid["imported"] == 0
        bounded = import_puzzle_file(store, io.BytesIO(lichess_csv()), filename="big.csv", max_expanded_bytes=10)
        assert bounded["errors"] and "Expanded" in bounded["errors"][0]
    finally:
        store.close()


def test_json_import_duplicate_invalid_lines_and_empty_pack(local):
    client, _, _ = local
    response = client.post("/api/import/puzzles", files={"file": ("puzzles.json", json.dumps([PUZZLE, dict(PUZZLE, id="bad", moves=["e2e4"])]), "application/json")})
    assert response.status_code == 200
    result = response.json()
    assert result["imported"] == 0 and result["skipped"] == 2 and len(result["errors"]) == 1
    assert client.post("/api/import/puzzles", files={"file": ("pack.json", "[]", "application/json")}).json()["imported"] == 0


def test_local_only_same_origin_and_schema_validation(local):
    client, _, _ = local
    assert client.get("/api/status", headers={"host": "attacker.example"}).status_code == 403
    assert client.post("/api/sessions", json={}, headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/sessions", json={}, headers={"origin": "null"}).status_code == 403
    assert client.post("/api/sessions", json={}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/sessions", json={"duration_seconds": -5}).status_code == 422
    assert client.post("/api/sessions", json={"lives": 0}).status_code == 422
    assert client.post("/api/sessions", json={"min_eval": 300, "max_eval": 100}).status_code == 422
    assert client.get("/api/sessions/not-found").status_code == 404
    status = client.get("/api/status").json()
    assert not status["engine"]["available"]
    assert client.post("/api/sessions", json={"mode": "intuition"}).status_code == 503
    assert client.post("/api/import/pgn", files={"file": ("game.pgn", "", "application/x-chess-pgn")}).status_code == 503


def test_cross_site_root_document_navigation_only(local):
    client, _, _ = local
    navigation = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
    allowed = client.get("/", headers=navigation)
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith("text/html")
    assert "frame-ancestors 'none'" in allowed.headers["content-security-policy"]

    assert client.get("/", headers=navigation | {"sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}).status_code == 403
    assert client.get("/api/status", headers=navigation).status_code == 403
    assert client.post("/", headers=navigation).status_code == 403
    assert client.post("/api/sessions", json={}, headers=navigation).status_code == 403
    assert client.get("/", headers=navigation | {"sec-fetch-dest": "iframe"}).status_code == 403
    assert client.get("/", headers=navigation | {"origin": "https://attacker.example"}).status_code == 403
    assert client.get("/", headers=navigation | {"host": "attacker.example"}).status_code == 403


class DeterministicEngine:
    def ensure(self):
        return self

    def status(self):
        return {"available": True, "name": "test engine"}

    def close(self):
        pass

    def analyze(self, board, *, move=None, importing=False):
        return {"cp": 200 if move is None else 100, "depth": 16, "engine": "test engine",
                "best_move": next(iter(board.legal_moves)).uci(), "pv_san": [board.san(next(iter(board.legal_moves)))]}


def test_intuition_round_score_and_provenance(tmp_path):
    app = create_app(data_dir=tmp_path, seed=False, engine=DeterministicEngine())
    with app.state.store.lock, app.state.store.db:
        app.state.store.add_intuition({"id": "real-game", "fen": PUZZLE["fen"], "game_url": PUZZLE["game_url"],
                                     "source": "test real-game position", "cp": 200, "depth": 12, "engine": "test engine"})
    with TestClient(app, base_url="http://localhost") as client:
        session = start(client, mode="intuition", positions=1, duration_seconds=0)
        original_fen = session["board"]["fen"]
        session = play(client, session, "d1d8")
        replay_transition(original_fen, session, ["d1d8"])
        assert session["state"] == "finished"
        assert session["feedback"]["before_cp"] == 200 and session["feedback"]["after_cp"] == 100
        assert session["feedback"]["loss_cp"] == 100
        assert session["feedback"]["accuracy"] == round(100 * math.exp(-0.5), 1)
        assert session["summary"]["accuracy"] == 60.7
        assert session["results"][0]["fen"] == PUZZLE["fen"]
        assert client.get("/api/status").json()["history"]["average_accuracy"] == 60.7


@pytest.mark.skipif(not ENGINE_BINARY.is_file(), reason="Local Stockfish not installed")
def test_real_engine_pov_root_move_and_live_fixture_round(tmp_path):
    engine = EngineService(ROOT)
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    black_board = board.mirror()
    try:
        white = engine.analyze(board)
        black = engine.analyze(black_board)
        # Both equivalent winning sides get positive cp, including a Black-to-move root.
        assert white["cp"] > 0 and black["cp"] > 0
        white_root = engine.analyze(board, move=chess.Move.from_uci(white["best_move"]))
        black_root = engine.analyze(black_board, move=chess.Move.from_uci(black["best_move"]))
        assert white_root["cp"] > 0 and black_root["cp"] > 0
        assert white["depth"] > 0 and "Stockfish" in white["engine"]
    finally:
        engine.close()
    app = create_app(data_dir=tmp_path, seed=True)
    with TestClient(app, base_url="http://localhost") as client:
        status = client.get("/api/status").json()
        assert status["counts"]["puzzles"] >= 100 and status["counts"]["intuition"] >= 10
        session = start(client, mode="intuition", duration_seconds=0, positions=1)
        private = app.state.sessions.sessions[session["id"]]
        assert 100 <= private.baseline["cp"] <= 300
        assert session["challenge"]["game_url"].startswith("https://lichess.org/")
        assert "moves" not in session["challenge"] and "best_move" not in session["challenge"]
        evaluated = play(client, session, private.baseline["best_move"])
        assert evaluated["state"] == "finished" and evaluated["feedback"]["type"] == "evaluated"
        assert evaluated["feedback"]["loss_cp"] >= 0 and 0 <= evaluated["feedback"]["accuracy"] <= 100


@pytest.mark.skipif(not ENGINE_BINARY.is_file(), reason="Local Stockfish not installed")
def test_pgn_import_real_engine_bounded_provenance(tmp_path):
    store = LibraryStore(tmp_path)
    engine = EngineService(ROOT)
    text = (ROOT / "data" / "intuition-source.pgn").read_text(encoding="utf-8")
    try:
        result = import_pgn_file(store, engine, text, max_positions=20, max_games=2)
        assert result["inspected"] <= 20
        assert result["imported"] >= 1
        for item in store.intuition_pool():
            provenance = json.loads(item["provenance"])
            assert provenance["White"] != "?" and provenance["Black"] != "?" and provenance["ply"] >= 12
            assert 100 <= item["cp"] <= 300 and item["depth"] > 0
    finally:
        engine.close()
        store.close()
