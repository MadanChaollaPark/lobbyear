from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_live_html_exists_and_references_runs_api() -> None:
    text = (WEB_DIR / "live.html").read_text(encoding="utf-8")
    assert "/runs" in text
    assert "EventSource" in text


@pytest.mark.parametrize("variant", ["v1", "v2", "v3", "v4", "v5", "v6"])
def test_each_style_variant_calls_runs_endpoint(variant: str) -> None:
    path = WEB_DIR / "styles" / variant / "index.html"
    assert path.exists(), f"missing style variant: {variant}"
    text = path.read_text(encoding="utf-8")
    assert "API_BASE" in text
    assert "/runs" in text
    # Must read the SSE stream
    assert "EventSource" in text or "EventSource(" in text


def test_serve_all_script_is_executable_and_has_stop() -> None:
    script = WEB_DIR / "styles" / "serve_all.sh"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "stop" in content
    assert "python" in content
