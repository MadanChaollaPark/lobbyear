"""LobbyEar — agentic lobbying mention scanner over VideoDB."""

import sys
from pathlib import Path

# Make the vendored agent_kit/ package importable. It sits next to lobbyear/
# at the repo root, so we add the parent directory once at import time.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from .profile import ClientProfile, load_profile  # noqa: E402
from .briefing import Briefing, Mention  # noqa: E402

__all__ = ["ClientProfile", "load_profile", "Briefing", "Mention"]
