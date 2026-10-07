"""Exercise Windows ZIP installation without running a Windows executable."""

import hashlib
import io
import os
import zipfile

import httpx
import pytest

from scripts import install_stockfish


@pytest.mark.parametrize("token", [None, "ci-test-token"])
def test_windows_archive_install_keeps_metadata_token_out_of_download(tmp_path, monkeypatch, token):
    project = tmp_path / "Knightlab folder with spaces"
    project.mkdir()
    binary = b"MZ-test-Windows-binary"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("stockfish/stockfish-windows-x86-64-universal.exe", binary)
        bundle.writestr("stockfish/Copying.txt", "GPL-3.0")
    payload = archive.getvalue()
    name = "stockfish-windows-x86-64-universal.zip"
    download = f"https://github.com/official-stockfish/Stockfish/releases/download/sf_19/{name}"
    requests = []

    def respond(request):
        requests.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"assets": [{
                "name": name, "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "browser_download_url": download,
            }]})
        assert str(request.url) == download
        return httpx.Response(200, content=payload)

    original_client = httpx.Client
    monkeypatch.setattr(install_stockfish, "ROOT", project)
    monkeypatch.setattr(install_stockfish, "asset_name", lambda: name)
    monkeypatch.setattr(install_stockfish.httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    if token:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    else:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    destination = install_stockfish.install()
    assert destination.name == ("stockfish.exe" if os.name == "nt" else "stockfish")
    assert destination.read_bytes() == binary
    assert (project / ".runtime" / "Stockfish-COPYING.txt").read_text() == "GPL-3.0"
    assert requests == [("api.github.com", f"Bearer {token}" if token else None), ("github.com", None)]
