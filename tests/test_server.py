from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.registry import REGISTRY


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    REGISTRY._runs.clear()
    yield
    REGISTRY._runs.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


YAML_PATH = Path(__file__).resolve().parent.parent / "clients" / "example_acme_tobacco.yaml"


def test_health(client: httpx.Client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_favicon(client: httpx.Client) -> None:
    r = client.get("/favicon.ico")
    assert r.status_code == 204


def test_root_returns_live_html(client: httpx.Client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_static_web_mount(client: httpx.Client) -> None:
    r = client.get("/web/live.html")
    assert r.status_code == 200
    r2 = client.get("/web/styles/v6/index.html")
    assert r2.status_code == 200


def test_runs_validation_requires_source(client: httpx.Client) -> None:
    r = client.post("/runs", json={"client": str(YAML_PATH)})
    assert r.status_code == 400
    assert "url" in r.json()["detail"]


def test_runs_validation_requires_profile(client: httpx.Client) -> None:
    r = client.post("/runs", json={"url": "https://example.com/v"})
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_get_run_404(client: httpx.Client) -> None:
    r = client.get("/runs/no-such-run")
    assert r.status_code == 404


def test_list_runs_initially_empty(client: httpx.Client) -> None:
    r = client.get("/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_sse_events_404(client: httpx.Client) -> None:
    r = client.get("/runs/no-such-run/events")
    assert r.status_code == 404


def test_profiles_resolve_yaml_path(client: httpx.Client) -> None:
    r = client.post("/profiles/resolve", json={"mode": "yaml_path", "path": str(YAML_PATH)})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["name"].startswith("ACME")


def test_profiles_resolve_yaml_path_missing(client: httpx.Client) -> None:
    r = client.post("/profiles/resolve", json={"mode": "yaml_path"})
    assert r.status_code == 400


def test_profiles_resolve_json_with_warnings(client: httpx.Client) -> None:
    r = client.post(
        "/profiles/resolve",
        json={"mode": "json", "profile": {"name": "Y", "sector": ""}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["name"] == "Y"
    assert body["warnings"]


def test_profiles_resolve_text_heuristic(client: httpx.Client) -> None:
    r = client.post(
        "/profiles/resolve",
        json={"mode": "text",
              "text": "ACME is a tobacco company. The interests are heated tobacco. "
                      "The risks are flavor ban."},
    )
    assert r.status_code == 200
    body = r.json()
    assert "heated tobacco" in (body["profile"]["interests"] or [""])[0].lower() or \
           any("heated" in t for t in body["profile"]["interests"])


def test_profiles_resolve_text_rejects_blank(client: httpx.Client) -> None:
    r = client.post("/profiles/resolve", json={"mode": "text", "text": "   "})
    assert r.status_code == 400


def test_profiles_resolve_unknown_mode(client: httpx.Client) -> None:
    r = client.post("/profiles/resolve", json={"mode": "snake"})
    assert r.status_code == 400


def test_sources_discover_requires_profile(client: httpx.Client) -> None:
    r = client.post("/sources/discover", json={"query": "tobacco"})
    assert r.status_code == 400


def test_sources_discover_yaml_path(client: httpx.Client, block_network) -> None:
    r = client.post(
        "/sources/discover",
        json={"client": str(YAML_PATH), "query": "tobacco", "limit": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert "candidates" in body
    assert len(body["candidates"]) <= 3
