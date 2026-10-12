"""Tests unitarios del mapeo de errores core -> MCP."""

from __future__ import annotations

from whisper_mcp.core_client import CoreError, _map_error


def test_map_401():
    e = _map_error(401, "")
    assert isinstance(e, CoreError)
    assert e.status == 401
    assert "api_key" in str(e)


def test_map_404():
    e = _map_error(404, "")
    assert "job_id" in str(e)


def test_map_429():
    e = _map_error(429, "")
    assert "cuota" in str(e)


def test_map_5xx():
    e = _map_error(500, "")
    assert e.status == 500
    assert "interno" in str(e)


def test_map_other():
    e = _map_error(418, "teapot")
    assert "418" in str(e)
