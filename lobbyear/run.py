from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any

from .agent import run_lobby_agent, trace_to_dicts
from .briefing import Briefing
from .capture import capture_session
from .profile import load_profile


DEFAULT_SCENE_PROMPT = (
    "Describe the visible scene in 1-2 sentences. If you can read any text on "
    "screen — slide titles, lower-thirds, name plates, agenda items, chart "
    "labels, vote counts — include it verbatim. Identify any speaker by their "
    "on-screen label if shown."
)


def _connect_videodb() -> Any:
    from videodb import connect  # type: ignore

    api_key = os.getenv("VIDEO_DB_API_KEY") or os.getenv("VIDEODB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VIDEODB_API_KEY (or VIDEO_DB_API_KEY) must be set — see .env.example"
        )
    os.environ["VIDEO_DB_API_KEY"] = api_key
    return connect()


def _wait_for_scene_records(video: Any, index_id: str, timeout_s: int, poll_s: int) -> list[dict[str, Any]]:
    from .tools import _jsonable

    deadline = time.time() + timeout_s
    last: list[dict[str, Any]] = []
    while time.time() < deadline:
        records = video.get_scene_index(index_id) or []
        last = [_jsonable(r) for r in records]
        if last:
            return last
        time.sleep(poll_s)
    return last


def _fetch_transcript(video: Any) -> list[dict[str, Any]]:
    from .tools import _jsonable

    try:
        raw = video.get_transcript() or []
    except Exception:  # noqa: BLE001
        return []
    return [_jsonable(seg) for seg in raw]


def _index_video(video: Any, scene_seconds: int, scene_timeout_s: int, poll_s: int) -> tuple[str, list[dict[str, Any]]]:
    from videodb import SceneExtractionType  # type: ignore

    print(f"[index] starting scene index (time_based, every {scene_seconds}s)…")
    index_id = video.index_scenes(
        extraction_type=SceneExtractionType.time_based,
        extraction_config={"time": scene_seconds, "select_frames": ["middle"]},
        prompt=DEFAULT_SCENE_PROMPT,
        name="lobbyear-scene-index",
    )
    if not index_id:
        raise RuntimeError("VideoDB did not return a scene index id")
    records = _wait_for_scene_records(video, index_id, timeout_s=scene_timeout_s, poll_s=poll_s)
    print(f"[index] scene records ready: {len(records)} entries")
    return str(index_id), records


def _index_spoken(video: Any, language_code: str | None) -> str | None:
    print("[index] starting spoken-word index…")
    try:
        if language_code:
            video.index_spoken_words(language_code=language_code)
        else:
            video.index_spoken_words()
    except Exception as exc:  # noqa: BLE001
        print(f"[index] spoken-word index failed: {exc}")
        return None
    # The hackathon SDK manages a single spoken-word index per video; passing
    # IndexType.spoken_word into search is enough — we keep a truthy marker.
    return "spoken"


def _resolve_video_from_id(coll: Any, video_id: str) -> Any:
    return coll.get_video(video_id)


def _resolve_video_from_url_or_file(coll: Any, *, url: str | None, file: str | None, name: str | None) -> Any:
    if url:
        print(f"[upload] URL → {url}")
        return coll.upload(url=url, name=name)
    if file:
        path = Path(file).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"video file not found: {path}")
        print(f"[upload] file → {path}")
        return coll.upload(file_path=str(path), name=name or path.name)
    raise ValueError("Provide --video-id, --url, or --file")


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48] or "run"


def _write_viewer(artifact_dir: Path, briefing_path: Path, trace_path: Path) -> Path:
    here = Path(__file__).resolve().parent.parent
    src = here / "web" / "viewer.html"
    if not src.exists():
        return src
    dst = artifact_dir / "viewer.html"
    html = src.read_text(encoding="utf-8")
    html = html.replace("./briefing.json", briefing_path.name)
    html = html.replace("./trace.jsonl", trace_path.name)
    dst.write_text(html, encoding="utf-8")
    return dst


def cmd_analyze(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY must be set — see .env.example")

    profile = load_profile(args.client)
    conn = _connect_videodb()
    coll = conn.get_collection()

    if args.video_id:
        video = _resolve_video_from_id(coll, args.video_id)
    else:
        video = _resolve_video_from_url_or_file(coll, url=args.url, file=args.file, name=args.name)
    video_id = str(getattr(video, "id", "unknown"))
    video_title = getattr(video, "name", None) or getattr(video, "title", None)
    video_length_s = getattr(video, "length", None)
    print(f"[video] id={video_id} title={video_title!r} length={video_length_s}")

    scene_index_id, scene_records = _index_video(
        video,
        scene_seconds=args.scene_seconds,
        scene_timeout_s=args.scene_timeout,
        poll_s=args.poll_interval,
    )
    spoken_marker = _index_spoken(video, language_code=args.language_code)
    transcript_segments = _fetch_transcript(video) if spoken_marker else []
    print(f"[index] transcript segments: {len(transcript_segments)}")

    briefing = Briefing(
        client_name=profile.name,
        video_id=video_id,
        video_title=video_title,
        video_length_s=float(video_length_s) if video_length_s is not None else None,
        source=args.url or args.file or args.video_id or "(unknown)",
    )

    run_slug = _slugify(f"{profile.name}-{int(time.time())}")
    artifact_dir = Path(args.artifacts_dir).resolve() / run_slug
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trace_path = artifact_dir / "trace.jsonl"
    briefing_path = artifact_dir / "briefing.json"
    print(f"[artifacts] writing to {artifact_dir}")

    started = time.time()
    result, session = run_lobby_agent(
        profile=profile,
        video=video,
        scene_index_id=scene_index_id,
        spoken_index_id=spoken_marker,
        scene_records=scene_records,
        transcript_segments=transcript_segments,
        briefing=briefing,
        max_turns=args.max_turns,
        trace_path=trace_path,
    )
    briefing.finish_reason = result.finish_reason
    briefing.elapsed_s = round(time.time() - started, 2)

    payload = briefing.to_dict()
    payload["agent_trace"] = trace_to_dicts(result.trace)
    payload["search_calls"] = session.search_calls
    payload["distinct_query_count"] = len(
        {c["query"].strip().lower() for c in session.search_calls}
    )
    payload["profile"] = dataclasses.asdict(profile)
    briefing_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[done] finish_reason={result.finish_reason} mentions={len(briefing.mentions)} "
          f"distinct_queries={payload['distinct_query_count']}")
    print(f"[done] briefing → {briefing_path}")

    viewer = _write_viewer(artifact_dir, briefing_path, trace_path)
    if viewer.exists():
        print(f"[done] viewer  → {viewer}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")
    token = args.token or os.getenv("VIDEODB_CAPTURE_TOKEN")
    if not token:
        raise RuntimeError(
            "Capture requires --token or env VIDEODB_CAPTURE_TOKEN (issued by VideoDB console)"
        )

    artifact_dir = Path(args.artifacts_dir).resolve() / f"capture-{args.session_id}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    events_path = artifact_dir / "capture-events.jsonl"

    result = capture_session(
        capture_session_id=args.session_id,
        client_token=token,
        include_screen=not args.no_screen,
        include_mic=not args.no_mic,
        include_system_audio=not args.no_system_audio,
        duration_s=args.duration,
        events_path=events_path,
    )
    print(f"[capture] completed={result.completed} video_id={result.video_id} error={result.error}")
    print(f"[capture] events → {events_path}")
    if result.video_id and args.client:
        print(f"[capture] handing off to analyze on video_id={result.video_id}")
        forward = argparse.Namespace(
            client=args.client,
            video_id=result.video_id,
            url=None,
            file=None,
            name=None,
            language_code=args.language_code,
            scene_seconds=args.scene_seconds,
            scene_timeout=args.scene_timeout,
            poll_interval=args.poll_interval,
            max_turns=args.max_turns,
            artifacts_dir=args.artifacts_dir,
        )
        return cmd_analyze(forward)
    if result.video_id:
        print("[capture] no --client passed; skipping analyze. Re-run with:")
        print(f"          python -m lobbyear.run analyze --client <profile.yaml> --video-id {result.video_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lobbyear", description="Agentic lobbying mention scanner over VideoDB.")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="Index a video (URL/file/id) and run the agent.")
    a.add_argument("--client", required=True, help="Path to client profile YAML.")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="URL to upload (YouTube, mp4, etc.)")
    src.add_argument("--file", help="Local video file to upload.")
    src.add_argument("--video-id", help="Existing VideoDB video id to reuse.")
    a.add_argument("--name", help="Optional display name for newly uploaded videos.")
    a.add_argument("--language-code", help="Force a transcript language (e.g. 'en', 'fr', 'hi').")
    a.add_argument("--scene-seconds", type=int, default=10)
    a.add_argument("--scene-timeout", type=int, default=600)
    a.add_argument("--poll-interval", type=int, default=4)
    a.add_argument("--max-turns", type=int, default=18)
    a.add_argument("--artifacts-dir", default="artifacts")
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("capture", help="Run a VideoDB CaptureSession, then optionally analyze.")
    c.add_argument("--session-id", required=True)
    c.add_argument("--token", help="VideoDB capture client token (or env VIDEODB_CAPTURE_TOKEN).")
    c.add_argument("--duration", type=float, help="Cap capture length in seconds (safety timeout).")
    c.add_argument("--no-screen", action="store_true")
    c.add_argument("--no-mic", action="store_true")
    c.add_argument("--no-system-audio", action="store_true")
    c.add_argument("--client", help="If set, auto-run analyze on the produced video.")
    c.add_argument("--language-code")
    c.add_argument("--scene-seconds", type=int, default=10)
    c.add_argument("--scene-timeout", type=int, default=600)
    c.add_argument("--poll-interval", type=int, default=4)
    c.add_argument("--max-turns", type=int, default=18)
    c.add_argument("--artifacts-dir", default="artifacts")
    c.set_defaults(func=cmd_capture)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[abort] interrupted by user")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
