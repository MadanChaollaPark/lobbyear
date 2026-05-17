# LobbyEar — agentic lobbying mention scanner over VideoDB

LobbyEar watches a video (uploaded URL, file, or live capture) on behalf of a
paying lobbying client and finds every moment a speaker says something the
client should care about — competitor mentions, regulatory signals, key actors,
risks. It then **explains why each mention matters**, in its own words, with a
playable evidence clip.

This is the 5th project in `hackathon/`. Unlike the earlier ones, the pipeline
is genuinely agentic: a Claude tool-use loop decides which searches to run,
when to compile evidence, and when the brief is good enough to ship. Same
input twice → different (but defensible) output.

## What's actually agentic about this

A non-agentic version would be: "run the user's keyword once → return hits".
LobbyEar instead:

1. **Plans** 4–8 search angles from the client profile (interests, risks,
   competitors, key actors).
2. **Issues** searches one at a time across both the **scene** index (slides,
   lower-thirds, name plates) and the **spoken-word** index (transcript).
3. **Adapts** — every search result feeds the next decision. Got a strong
   competitor mention? Widen with a transcript window. Empty result? Drop
   that angle.
4. **Records** mentions with a model-written `why_it_matters` grounded in the
   actual transcript quote — not a template fill.
5. **Terminates** when coverage is good. The runtime enforces a minimum of
   3 distinct search queries before `finalize_briefing` can succeed.

The loop scaffolding lives in `../agent_kit/anthropic_loop.py` (shared with the
other projects). Per-run state, VideoDB wrappers, and the finalize-guard live
in `lobbyear/tools.py`.

## VideoDB features used

| Hackathon requirement | Where in this project |
| --- | --- |
| CaptureSession / RTStream | `lobbyear/capture.py` — wraps `videodb.capture.CaptureClient` with mic + screen + system-audio channels, streams events, hands off to analyze mode on `recording-complete` |
| Spoken-word index | `_index_spoken` in `lobbyear/run.py` — `video.index_spoken_words()`, optional language hint |
| Scene index | `_index_video` — `index_scenes` with a custom prompt tuned to surface on-screen text (slides, lower-thirds, vote tallies, name plates) |
| Multimodal search | Agent tools `search_scenes` + `search_spoken` — agent decides which index per query |
| Compile / clip | `compile_clip` tool — generates playable VideoDB URLs for evidence shots |
| Transcript window | `get_transcript_window` — exact wording around a hit |

## Layout

```
videodb5/
├── lobbyear/
│   ├── profile.py     # ClientProfile dataclass + YAML loader
│   ├── briefing.py    # Mention + Briefing dataclasses
│   ├── tools.py       # VideoDB tool wrappers + LobbySession state + finalize guard
│   ├── agent.py       # System prompt + run_lobby_agent (uses agent_kit)
│   ├── capture.py     # CaptureSession wrapper
│   └── run.py         # CLI: `analyze` and `capture` subcommands
├── clients/
│   └── example_acme_tobacco.yaml
├── web/
│   └── viewer.html    # zero-build single-file dashboard
├── artifacts/         # per-run outputs land here
├── requirements.txt
└── .env.example
```

## Setup

```bash
cd videodb5
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# then fill in ANTHROPIC_API_KEY + VIDEO_DB_API_KEY
```

## Run on an existing video

```bash
python -m lobbyear.run analyze \
  --client clients/example_acme_tobacco.yaml \
  --url "https://www.youtube.com/watch?v=<ID>"
```

Or against a local file:

```bash
python -m lobbyear.run analyze \
  --client clients/example_acme_tobacco.yaml \
  --file ~/Downloads/eu-envi-hearing.mp4
```

Output lands in `artifacts/<client-slug>-<timestamp>/`:

- `briefing.json` — mentions, executive summary, recommended actions, full agent trace, search-call log
- `trace.jsonl` — line-delimited events (reasoning + tool calls + tool results)
- `viewer.html` — open in a browser, no build step needed

## Run live (server + browser UI)

The static viewer shows a finished briefing. For demo-grade live agent
visibility, use the server:

```bash
uvicorn server.app:app --port 8765 --reload
```

Open <http://localhost:8765/> in a browser. Paste a video URL (or a local
absolute path, or an existing VideoDB video id), click **Start run**, and watch:

- Claude's reasoning stream into the left column.
- Each tool call appear as a chip in the rail at the top — blue dot for
  `search_scenes`, amber for `search_spoken` (so multimodal use is visible
  at a glance).
- The **distinct queries** counter tick up to 3+; the pill turns green
  when the agentic acceptance threshold is met.
- **Mention cards** materialize in the centre column as the agent commits
  to each one — severity pill, transcript quote, why-it-matters.
- **Evidence clips** embed in the right column the moment `compile_clip`
  returns — newest first.

Internals:

- `server/app.py` — FastAPI app, mounts `/web` and `/artifacts`.
- `server/runs.py` — `POST /runs` schedules an `asyncio.Task` that runs
  the full analyze pipeline and pushes each agent event into a per-run
  `asyncio.Queue`.
- `server/sse.py` — `GET /runs/{id}/events` drains the queue and emits
  SSE. Catches up late joiners on already-queued events.
- `server/registry.py` — in-process `RunRegistry`; restart wipes state
  (demo-grade).
- `web/live.html` — single-file UI, no build step. Pass `?run=<id>` to
  reattach to an existing stream after a refresh.

The standalone CLI (`python -m lobbyear.run …`) still works and writes
the same artifacts under `artifacts/`. Each run also writes a
`viewer.html` copy alongside its `briefing.json` for static replay.

## Run live capture

```bash
python -m lobbyear.run capture \
  --session-id $(uuidgen) \
  --token "$VIDEODB_CAPTURE_TOKEN" \
  --duration 600 \
  --client clients/example_acme_tobacco.yaml
```

This starts a local CaptureSession (screen + mic + system audio by default —
disable any with `--no-screen`, `--no-mic`, `--no-system-audio`), waits for
`recording-complete`, and if `--client` is set, automatically runs `analyze`
on the resulting video id.

## Writing a client profile

A profile is a YAML file. See `clients/example_acme_tobacco.yaml` for the full
shape. The agent reads the whole thing into its system prompt — every field
shapes which searches it generates. The two highest-leverage fields:

- `mention_triggers`: literal phrases the agent will turn into spoken-word
  searches first
- `risks`: what the agent should escalate to `high` severity if it finds them

## How to know the agent is actually agentic

After a run, check `artifacts/<run>/briefing.json`:

- `distinct_query_count` should be ≥ 3, with queries you can read and tell
  were generated by a model (not just the watchlist verbatim).
- Each `mentions[*].why_it_matters` should reference the actual quote, not a
  template phrase like "this is relevant to client interests".
- Two runs on the same video should produce different (but coherent) briefings.

That's the acceptance test from `../AGENTIC_REWRITE_BRIEF.md`. If a run fails
it, the project isn't agentic — investigate the trace.

## Submission checklist

- [x] Uses VideoDB CaptureSession / RTStream
- [x] Uses VideoDB search across scene + spoken indexes
- [x] Uses VideoDB compile for evidence clips
- [x] Agent loop with ≥3 model-generated tool calls per run
- [x] Working demo path (CLI → JSON + HTML viewer)
- [x] Single-page README
