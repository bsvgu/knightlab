# Starter training data

This small offline library contains **317 genuine Lichess puzzles** and **36 intuition positions from 23 real Lichess games**. It is included so the trainer is usable before importing a larger library.

## Sources and licensing

- Puzzle rows: [official Lichess puzzle database](https://database.lichess.org/#puzzles), downloaded from `https://database.lichess.org/lichess_db_puzzle.csv.zst` on **2026-10-07**. The builder streams only the first 20,000 CSV rows and closes the connection; it does not download the full archive.
- Games: [official Lichess game export API](https://lichess.org/api#tag/Games/operation/gamePgn), using the original game IDs linked by the selected puzzles. Twenty-four complete public games are preserved in `intuition-source.pgn`; 23 supplied qualifying positions. Every game retains its original `Site`, player, date and result headers. The export includes published engine annotations as a prefilter.
- Lichess publishes its [game and puzzle database exports under CC0](https://database.lichess.org/). The source data and normalized data retain **CC0-1.0** status; the newly calculated intuition evaluation data is also dedicated to CC0. See the [CC0 legal code](https://creativecommons.org/publicdomain/zero/1.0/legalcode).
- Intuition evaluations were calculated locally with **Stockfish 19**, single thread, 32 MB hash, cleared before each position, at **depth 12**. The binary is a separate GPL-licensed dependency, not part of these data files.

`provenance.json` records the build timestamp, download source, exact game URLs, analysis depth and validation summary. `puzzle-source.csv` preserves the selected original Lichess rows, including the opponent's setup move. No synthetic positions or invented engine evaluations are included.

## Puzzle selection and meaning

Ratings and themes are **the actual Lichess puzzle metadata**, not estimates made by this trainer. Included ratings range from **538 to 2491**. Rows were selected in source order using theme quotas within five rating bands starting at 500, 900, 1300, 1700 and 2100, with a larger quota for mate in two. This is a useful starter sample rather than a statistically representative sample. Several themes may apply to one puzzle, so counts overlap.

| Theme | Puzzles |
|---|---:|
| Mate in one | 26 |
| Mate in two | 93 |
| Mate in three | 31 |
| Fork | 42 |
| Pin | 42 |
| Skewer | 30 |
| Discovered attack | 30 |
| Sacrifice | 58 |
| Promotion | 30 |
| Defensive move | 33 |

The source FEN describes the board **before the opponent's move**. The builder applies that first UCI move and stores the resulting solver-ready FEN in `puzzles.json`; `moves` contains only the remaining solution. Every remaining move was checked for legality, and every included mate-in-one/two/three line ends in checkmate. Source IDs and game URLs are preserved. Different legal mating moves can exist, especially in mate-in-one puzzles.

The sample has fewer difficult mate-in-one positions; each theme does not span every rating band equally. Puzzle rating describes puzzle-solving difficulty rather than the player's FIDE rating. Import a larger official CSV or CSV.zst pack for a deeper progression and more variety.

## Intuition selection and evaluation

Every `intuition.json` FEN is an exact board position reached by replaying a complete game in `intuition-source.pgn`. Records preserve the source game URL with its ply anchor, engine name, actual reported depth, search limit and engine options. The starter pack has 17 positions with White to move and 19 with Black to move.

The stored `cp` is the score **from the side-to-move perspective**. All 36 positions evaluated between **+100 and +281 centipawns** at depth 12; no mate scores were admitted. Published PGN annotations only help locate promising candidates. Fresh Stockfish analysis determines inclusion and the stored score. The builder limits each game to at most three positions and uses round-robin selection across games.

These evaluations are finite-depth training baselines. Greater search depth or another engine version may change them. The running trainer rechecks the current position and evaluates the user's move with its live engine. The resulting training accuracy is a transparent centipawn-loss score, not an official Lichess accuracy statistic or Elo estimate.

## Reproduce or validate

Run these commands from the project directory after installing its dependencies and Stockfish:

```sh
# Check uniqueness, legal full puzzle lines, mate endings and exact game provenance.
python scripts/build_demo_data.py --validate

# Rebuild from the retained CSV and PGNs without any network download.
python scripts/build_demo_data.py --engine .runtime/stockfish

# Replace the source sample with a newly downloaded bounded sample, then rebuild.
python scripts/build_demo_data.py --download --engine .runtime/stockfish
```

The online builder also accepts `--max-rows`, `--games` and `--intuition`. Lichess is a live database, so a new download may have different ratings or selection. The retained sources permit offline rebuilding; the supplied Stockfish 19 binary reproduced all 36 records identically in a second independent build. An engine upgrade may legitimately produce different qualifying positions and scores. Validation checks stored provenance and legal chess data without launching an engine.
