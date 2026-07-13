"""Tests for /_voice/health and /_voice/stream WebSocket."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from app.main import create_app
from app.api import voice as voice_api


@pytest.fixture
def tc():
    return TestClient(create_app())


def test_voice_health_unconfigured(tc, monkeypatch):
    monkeypatch.delenv("KOKORO_URL", raising=False)
    r = tc.get("/_voice/health")
    assert r.status_code == 200
    j = r.json()
    assert j["configured"] is False
    assert j["backend_url"] is None


def test_voice_health_configured(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")
    r = tc.get("/_voice/health")
    assert r.status_code == 200
    j = r.json()
    assert j["configured"] is True
    assert j["backend_url"] == "http://kokoro:8880"


def _mock_stream(chunks: list[bytes]):
    async def _gen(req, *, base_url=None, chunk_size=4096):
        for c in chunks:
            yield c
    return _gen


def test_voice_stream_happy_path(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")
    fake_chunks = [b"\x00\x01" * 512, b"\x02\x03" * 512, b"\x04\x05" * 128]

    with patch.object(voice_api, "stream_from_backend", _mock_stream(fake_chunks)):
        with tc.websocket_connect("/_voice/stream") as ws:
            ws.send_text(json.dumps({"text": "Hola mundo", "voice": "af_bella"}))
            start = json.loads(ws.receive_text())
            assert start["event"] == "start"
            assert start["voice"] == "af_bella"

            received = []
            while True:
                msg = ws.receive()
                if "bytes" in msg and msg["bytes"] is not None:
                    received.append(msg["bytes"])
                elif "text" in msg and msg["text"] is not None:
                    end = json.loads(msg["text"])
                    assert end["event"] == "end"
                    assert end["chunks"] == 3
                    assert end["bytes"] == sum(len(c) for c in fake_chunks)
                    break
            assert received == fake_chunks


def test_voice_stream_rejects_empty_text(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")
    with tc.websocket_connect("/_voice/stream") as ws:
        ws.send_text(json.dumps({"text": ""}))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "rejected"
        assert "text is required" in msg["reason"]


def test_voice_stream_rejects_too_long(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")
    with tc.websocket_connect("/_voice/stream") as ws:
        ws.send_text(json.dumps({"text": "x" * 4001}))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "rejected"


def test_voice_stream_backend_unavailable(tc, monkeypatch):
    monkeypatch.delenv("KOKORO_URL", raising=False)
    with tc.websocket_connect("/_voice/stream") as ws:
        ws.send_text(json.dumps({"text": "hola"}))
        # start frame first
        start = json.loads(ws.receive_text())
        assert start["event"] == "start"
        # then unavailable
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "unavailable"


# ---------------------------------------------------------------------------
# /_voice/voices catalogue
# ---------------------------------------------------------------------------


async def _fake_list(ids):
    async def _inner(*, base_url=None, timeout=10.0):
        return list(ids)
    return _inner


def test_voices_backend_string_list(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")

    async def fake(*, base_url=None, timeout=10.0):
        return ["af_bella", "am_adam", "ef_dora", "jf_alpha"]

    with patch.object(voice_api, "list_backend_voices", fake):
        r = tc.get("/_voice/voices")

    assert r.status_code == 200
    j = r.json()
    assert j["source"] == "backend"
    assert j["default"] == "af_bella"
    ids = [v["id"] for v in j["voices"]]
    assert ids == ["af_bella", "am_adam", "ef_dora", "jf_alpha"]
    # Language + gender inference from the voice-id prefix.
    lookup = {v["id"]: v for v in j["voices"]}
    assert lookup["af_bella"]["language"] == "en-US"
    assert lookup["af_bella"]["gender"] == "female"
    assert lookup["am_adam"]["gender"] == "male"
    assert lookup["ef_dora"]["language"] == "es"
    assert lookup["jf_alpha"]["language"] == "ja"


def test_voices_backend_dict_list(tc, monkeypatch):
    """Newer Kokoro builds return ``[{"id": "..."}]`` — must normalise."""
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")

    async def fake_service(*, base_url=None, timeout=10.0):
        # list_backend_voices already flattens; here we just exercise the api.
        return ["af_heart", "bm_george"]

    with patch.object(voice_api, "list_backend_voices", fake_service):
        r = tc.get("/_voice/voices")

    assert r.status_code == 200
    assert [v["id"] for v in r.json()["voices"]] == ["af_heart", "bm_george"]


def test_voices_unconfigured_fallback(tc, monkeypatch):
    monkeypatch.delenv("KOKORO_URL", raising=False)
    r = tc.get("/_voice/voices")
    assert r.status_code == 200
    j = r.json()
    assert j["source"] == "fallback-unconfigured"
    assert j["default"] == "af_bella"
    ids = [v["id"] for v in j["voices"]]
    assert "af_bella" in ids
    assert len(ids) >= 8


def test_voices_backend_transport_error_falls_back(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")

    async def boom(*, base_url=None, timeout=10.0):
        import httpx
        raise httpx.ConnectError("backend down")

    with patch.object(voice_api, "list_backend_voices", boom):
        r = tc.get("/_voice/voices")

    assert r.status_code == 200
    assert r.json()["source"] == "fallback-error"


def test_voices_backend_empty_falls_back(tc, monkeypatch):
    monkeypatch.setenv("KOKORO_URL", "http://kokoro:8880")

    async def empty(*, base_url=None, timeout=10.0):
        return []

    with patch.object(voice_api, "list_backend_voices", empty):
        r = tc.get("/_voice/voices")

    assert r.status_code == 200
    assert r.json()["source"] == "fallback-empty"


# ---------------------------------------------------------------------------
# list_backend_voices — direct unit tests against the service function.
# ---------------------------------------------------------------------------


def test_list_backend_voices_flattens_shapes(monkeypatch):
    """Both ``["..."]`` and ``[{"id": "..."}]`` shapes must produce ids."""
    import asyncio

    import httpx

    from app.services import voice as voice_service

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"voices": ["af_bella", {"id": "am_adam"}, {"name": "jf_alpha"}, 123]},
        )

    transport = httpx.MockTransport(handler)
    original = voice_service.httpx.AsyncClient

    def patched(**kwargs):
        kwargs["transport"] = transport
        return original(**kwargs)

    monkeypatch.setattr(voice_service.httpx, "AsyncClient", patched)

    ids = asyncio.run(voice_service.list_backend_voices(base_url="http://kokoro:8880"))
    assert ids == ["af_bella", "am_adam", "jf_alpha"]


def test_list_backend_voices_raises_when_unconfigured(monkeypatch):
    import asyncio

    from app.services import voice as voice_service

    monkeypatch.delenv("KOKORO_URL", raising=False)
    with pytest.raises(voice_service.KokoroUnavailable):
        asyncio.run(voice_service.list_backend_voices())
