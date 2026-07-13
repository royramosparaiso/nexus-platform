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
