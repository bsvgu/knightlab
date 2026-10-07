# Internal API contract

Local Python/FastAPI server serves `web/` and `/api/…`, same origin. All UI copy English. Board FEN and UCI moves use standard chess. Backend owns legal moves, solutions, timing and scoring; frontend renders responses and never embeds solutions.

## Status and library

- `GET /api/status`: `{engine:{available:bool,name:str},counts:{puzzles:int,intuition:int},categories:[{id,label,count}],history:{sessions:int,solved:int,best_score:int,average_accuracy:float|null},recent_sessions:[...]}`.
- `GET /api/library?theme=mateIn2&min_rating=0&max_rating=3000&sort=rating_asc&q=&page=1&limit=20`: `{items:[{id,fen,rating,themes:[str],game_url,source}],total,page,categories:[{id,label,count}]}`. `sort`: rating_asc/rating_desc/random. Empty theme = all.
- `POST /api/import/puzzles` multipart `file`: Lichess CSV/CSV.zst or JSON puzzle pack, returns `{imported,skipped,errors:[str]}`.
- `POST /api/import/pgn` multipart `file`: real game PGN; extract qualifying positions with Stockfish, returns `{imported,inspected,skipped,errors:[str]}`. Bounded analysis with honest engine-unavailable error.

## Sessions

- `POST /api/sessions` JSON `{mode:"mate"|"tactics"|"free"|"intuition",theme:"mateIn2",duration_seconds:180|300|0,lives:3,start_rating:800,positions:10,min_eval:100,max_eval:300,puzzle_id?:str}`. Free mode has no life or timer limits; optional puzzle_id starts selected library puzzle.
- `GET /api/sessions/{id}`: refresh current state, including timer expiration.
- `POST /api/sessions/{id}/move` JSON `{uci:"e2e4"}`. Legal wrong puzzle moves cost 1 life (except free), do not change puzzle board; illegal moves never cost a life. Correct moves apply automatic opponent reply, full line required. Any immediate checkmate accepted for mate puzzles.
- `POST /api/sessions/{id}/next` JSON `{}`: next challenge; solved challenges increase target rating, random without repeats until pool exhausted. The frontend calls this automatically after the final move animation in timed Mate/Tactics sprints, including a resumed solved position. The server clock and lives continue across challenges. Free practice and intuition use manual Next.
- `POST /api/sessions/{id}/stop` JSON `{}`: finish and persist history.

All return a Session object:

```json
{"id":"uuid","mode":"mate","state":"playing","lives":3,"solved":0,"failed":0,"current_rating":800,"remaining_seconds":180,"current_position":1,"total_positions":10,"board":{"fen":"...","turn":"w","player_color":"w","legal_moves":["e2e4"],"last_move":null,"transition":[]},"challenge":{"id":"...","rating":800,"themes":["mateIn2"],"game_url":"https://lichess.org/...","source":"Lichess"},"feedback":null,"results":[],"summary":null}
```

`board.transition`: chronological `{uci,fen}` frames for moves already accepted by the last move request, including an automatic opponent reply. Empty on new challenges, wrong/illegal/expired attempts. The frontend animates these frames only for its explicit move response, never on polling or resume. No future solution moves are exposed.

`state`: playing / between / finished. `feedback`: `{type:"correct"|"wrong"|"illegal"|"solved"|"evaluated",message:str,best_move?:str,played_move?:str,before_cp?:number,after_cp?:number,loss_cp?:number,accuracy?:number,pv_san?:[str]}`. `results` intuition per-position feedback. `summary` on finish: `{solved,failed,accuracy?:float,reason:str,duration_seconds}`. Session without available challenge returns 409, explaining filters/import. Errors FastAPI `{detail:"English message"}`.

Intuition: eligible real-game positions +100..+300 cp from side-to-move POV at engine analysis limit. Store engine/depth/provenance, recheck baseline live; evaluate user's legal move with Stockfish root_moves for same player's perspective. Centipawn loss=max(0,best-played); score transparently derived as `100*exp(-loss_cp/200)` (round 1 decimal). This is a training score, not FIDE Elo or Lichess accuracy. Actual engine required, no fake scores.

## Fixture schema

`data/puzzles.json`: JSON list `{id,fen,moves:[uci],rating,themes:[str],game_url,source}`; FEN already ready for solver, moves are solution (no opponent setup move). Validate legal full lines.

`data/intuition.json`: JSON list `{id,fen,game_url,source,cp,depth,engine}`; cp side-to-move; ensure every board valid, from real game, within 100..300. Optional PGN provenance in `data/intuition-source.pgn`.

Project local engine binary expected `.runtime/stockfish` (ignored), or env `STOCKFISH_PATH`. SQLite default `.data/trainer.sqlite3` (ignored), `CHESS_TRAINER_DATA_DIR` override. Python package `trainer`, launch `python -m trainer` binds 127.0.0.1:8765 by default. Tests use temporary dirs and fixtures.
