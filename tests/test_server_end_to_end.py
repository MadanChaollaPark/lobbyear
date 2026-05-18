from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from server.app import app
from server.registry import REGISTRY


YAML_PATH = Path(__file__).resolve().parent.parent / "clients" / "example_acme_tobacco.yaml"
ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    REGISTRY._runs.clear()
    yield
    # Clean up artifact directories created by completed/errored runs.
    for run in list(REGISTRY._runs.values()):
        for entry in ARTIFACTS_DIR.glob(f"*{run.id}*"):
            shutil.rmtree(entry, ignore_errors=True)
    REGISTRY._runs.clear()


@pytest.fixture
def server_env(monkeypatch: pytest.MonkeyPatch, patch_videodb, patch_agent_loop) -> dict:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    return {"agent_calls": patch_agent_loop}


async def _wait_for_status(run_id: str, target: set[str], timeout: float = 5.0) -> str:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        run = REGISTRY.get(run_id)
        if run and run.status in target:
            return run.status
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {target}")


@pytest.mark.asyncio
async def test_full_run_lifecycle_with_inline_profile(server_env, tmp_path) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/runs",
            json={
                "profile": {
                    "name": "ACME",
                    "sector": "tobacco",
                    "one_liner": "test",
                    "interests": ["heated tobacco"],
                    "mention_triggers": ["IQOS"],
                },
                "url": "https://example.com/v.mp4",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["events_url"] == f"/runs/{run_id}/events"

        await _wait_for_status(run_id, {"finished"})

        run = REGISTRY.get(run_id)
        assert run.status == "finished"
        assert run.briefing_snapshot is not None
        assert run.briefing_snapshot["client_name"] == "ACME"
        assert len(run.briefing_snapshot["mentions"]) == 1
        assert run.briefing_snapshot["distinct_query_count"] == 3
        kinds = {e["kind"] for e in run.events}
        # status updates + agent trace must both be present
        assert "status" in kinds
        assert {"tool_use", "tool_result"} <= kinds


@pytest.mark.asyncio
async def test_run_with_yaml_path_and_video_id(server_env) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/runs",
            json={"client": str(YAML_PATH), "videoId": "vid_existing_001"},
        )
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        await _wait_for_status(run_id, {"finished"})

        r2 = await ac.get(f"/runs/{run_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["status"] == "finished"
        assert data["briefing"]["video_id"] == "vid_fake_001"
        assert data["briefing"]["profile"]["name"].startswith("ACME")


@pytest.mark.asyncio
async def test_run_fails_when_anthropic_key_missing(monkeypatch, patch_videodb) -> None:
    # Note: no patch_agent_loop, no ANTHROPIC_API_KEY → must fail with a clear status event.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/runs",
            json={"client": str(YAML_PATH), "url": "https://example/v"},
        )
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        await _wait_for_status(run_id, {"error"})
        run = REGISTRY.get(run_id)
        assert run.status == "error"
        assert "ANTHROPIC_API_KEY" in (run.error or "")


@pytest.mark.asyncio
async def test_sse_stream_emits_events_and_done(server_env) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10) as ac:
        r = await ac.post(
            "/runs",
            json={"client": str(YAML_PATH), "url": "https://example/v"},
        )
        run_id = r.json()["run_id"]
        await _wait_for_status(run_id, {"finished"})

        async with ac.stream("GET", f"/runs/{run_id}/events") as stream:
            collected: list[str] = []
            done_seen = False
            async for line in stream.aiter_lines():
                collected.append(line)
                if "event: done" in line or line.startswith("event: done"):
                    done_seen = True
                if done_seen and not line:
                    break
                if len(collected) > 200:
                    break

        text = "\n".join(collected)
        assert "event: done" in text
        # We should see at least one regular message before done.
        assert "data:" in text


@pytest.mark.asyncio
async def test_list_runs_reflects_active_run(server_env) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/runs",
            json={"client": str(YAML_PATH), "url": "https://example/v"},
        )
        run_id = r.json()["run_id"]
        await _wait_for_status(run_id, {"finished"})

        r2 = await ac.get("/runs")
        runs = r2.json()["runs"]
        assert any(item["id"] == run_id for item in runs)


@pytest.mark.asyncio
async def test_artifact_files_are_written(server_env, tmp_path: Path) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/runs",
            json={"client": str(YAML_PATH), "url": "https://example/v"},
        )
        run_id = r.json()["run_id"]
        await _wait_for_status(run_id, {"finished"})

    repo_root = Path(__file__).resolve().parent.parent
    artifact_root = repo_root / "artifacts"
    matching = [p for p in artifact_root.glob("*") if run_id in p.name]
    assert matching, "expected a run-specific artifact directory"
    art_dir = matching[0]
    briefing_path = art_dir / "briefing.json"
    trace_path = art_dir / "trace.jsonl"
    assert briefing_path.exists()
    assert trace_path.exists()
    data = json.loads(briefing_path.read_text())
    assert data["client_name"].startswith("ACME")
    assert data["distinct_query_count"] == 3
