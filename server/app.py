"""FastAPI sidecar that turns LobbyEar into a live web demo.

Run with:
    uvicorn server.app:app --reload --port 8765
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is importable so `lobbyear` and `agent_kit` resolve
# regardless of where uvicorn is launched from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


from .runs import router as runs_router
from .sse import router as sse_router

app = FastAPI(title="LobbyEar", version="0.1.0")
app.include_router(runs_router)
app.include_router(sse_router)

# Permissive CORS — this is a local demo server; lock down for prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the static viewer at /viewer.html and the artifacts dir at /artifacts/.
_WEB_DIR = _REPO_ROOT / "web"
_ARTIFACTS_DIR = _REPO_ROOT / "artifacts"
if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")
if _ARTIFACTS_DIR.exists():
    app.mount("/artifacts", StaticFiles(directory=str(_ARTIFACTS_DIR)), name="artifacts")


@app.get("/")
def root() -> FileResponse | dict[str, str]:
    viewer = _WEB_DIR / "viewer.html"
    if viewer.exists():
        return FileResponse(str(viewer))
    return {"message": "LobbyEar server is up. /web/viewer.html will appear once it is built."}
