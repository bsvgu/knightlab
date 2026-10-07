# Third-party notices

Knightlab source code is licensed under GPL-3.0-or-later. See `LICENSE`.

- **python-chess** by Niklas Fiekas and contributors — GPL-3.0-or-later. Chess rules, FEN/PGN parsing and UCI engine communication. https://github.com/niklasf/python-chess
- **Stockfish 19** by the Stockfish developers — GPL-3.0. Downloaded separately from official release assets to ignored `.runtime/`. Upstream matching source: https://github.com/official-stockfish/Stockfish/tree/sf_19. The setup script verifies the asset's official SHA-256 digest and copies the license. Engine binaries are not committed to this repository.
- **Lichess open database** — CC0. Embedded puzzle fixtures and exported game records retain IDs and source links; see `data/README.md`. https://database.lichess.org/
- **FastAPI** — MIT, **Starlette** — BSD-3-Clause, **Uvicorn** — BSD-3-Clause, **Pydantic** — MIT, **python-multipart** — Apache-2.0, **HTTPX** — BSD-3-Clause, **python-zstandard** — BSD-3-Clause. Installed through pip; their distribution license files remain in the environment.

The app uses original code-native chess piece drawings and system fonts. No external images or fonts are required at runtime. Imported user puzzle packs and PGN files retain their own source rights; uploading into the local library does not change their licenses.
