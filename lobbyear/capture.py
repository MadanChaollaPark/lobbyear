from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CaptureResult:
    capture_session_id: str
    completed: bool
    video_id: str | None
    error: str | None
    events: list[dict[str, Any]] = field(default_factory=list)


async def _run_capture(
    *,
    capture_session_id: str,
    client_token: str,
    include_screen: bool,
    include_mic: bool,
    include_system_audio: bool,
    duration_s: float | None,
    events_path: Path | None,
) -> CaptureResult:
    try:
        from videodb.capture import CaptureClient  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Install the hackathon VideoDB SDK first: "
            "pip install \"git+https://github.com/Video-DB/videodb-python.git@hackathon\""
        ) from exc

    events_file = events_path.open("w", encoding="utf-8") if events_path else None
    collected: list[dict[str, Any]] = []
    video_id: str | None = None
    error: str | None = None
    completed = False

    client = CaptureClient(client_token=client_token)
    try:
        if include_mic:
            await client.request_permission("microphone")
        if include_screen:
            await client.request_permission("screen_capture")
        if include_system_audio:
            try:
                await client.request_permission("system_audio")
            except Exception:  # noqa: BLE001 — best effort, some platforms refuse
                pass

        channels = await client.list_channels()
        selected: list[Any] = []
        mic = getattr(channels.mics, "default", None) if include_mic else None
        display = None
        if include_screen:
            display = getattr(channels.displays, "primary", None)
            if display is None and len(channels.displays) > 0:
                display = channels.displays[0]
        system_audio = (
            getattr(channels.system_audio, "default", None) if include_system_audio else None
        )
        for ch in (mic, display, system_audio):
            if ch is not None:
                selected.append(ch)
        if display is not None:
            display.is_primary = True

        if not selected:
            raise RuntimeError(
                "No capture channels selected — enable at least screen, mic, or system audio"
            )

        await client.start_session(
            capture_session_id=capture_session_id,
            channels=selected,
        )

        async def consume_events() -> None:
            nonlocal video_id, completed, error
            async for event in client.events():
                ev_dict: dict[str, Any] = {
                    "event": getattr(event, "event", None),
                    "payload": getattr(event, "payload", None),
                }
                collected.append(ev_dict)
                print(f"  [capture] {ev_dict['event']}: "
                      f"{json.dumps(ev_dict['payload'], default=str)[:240]}")
                if events_file is not None:
                    events_file.write(json.dumps(ev_dict, default=str) + "\n")
                    events_file.flush()

                payload = ev_dict["payload"] or {}
                if isinstance(payload, dict) and not video_id:
                    candidate = (
                        payload.get("video_id")
                        or payload.get("videoId")
                        or (payload.get("video") or {}).get("id")
                        if isinstance(payload.get("video"), dict) else None
                    )
                    if candidate:
                        video_id = str(candidate)

                if ev_dict["event"] == "recording-complete":
                    completed = True
                    return
                if ev_dict["event"] == "error":
                    error = json.dumps(payload, default=str)
                    return

        consumer = asyncio.create_task(consume_events())
        if duration_s is not None:
            try:
                await asyncio.wait_for(consumer, timeout=duration_s + 30)
            except asyncio.TimeoutError:
                consumer.cancel()
                error = error or f"capture exceeded {duration_s}s without recording-complete"
        else:
            await consumer

        try:
            await client.stop_session()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await client.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if events_file is not None:
            events_file.close()

    return CaptureResult(
        capture_session_id=capture_session_id,
        completed=completed,
        video_id=video_id,
        error=error,
        events=collected,
    )


def capture_session(
    *,
    capture_session_id: str,
    client_token: str,
    include_screen: bool = True,
    include_mic: bool = True,
    include_system_audio: bool = True,
    duration_s: float | None = None,
    events_path: Path | None = None,
) -> CaptureResult:
    return asyncio.run(
        _run_capture(
            capture_session_id=capture_session_id,
            client_token=client_token,
            include_screen=include_screen,
            include_mic=include_mic,
            include_system_audio=include_system_audio,
            duration_s=duration_s,
            events_path=events_path,
        )
    )
