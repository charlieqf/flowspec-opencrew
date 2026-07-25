from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path


def create_tool_use_session_id() -> str:
    # UUIDv7 is not available in every supported Python runtime yet. Prefixing
    # UUIDv4 with milliseconds keeps directories sortable while preserving uniqueness.
    return f"tus_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class ToolSessionPaths:
    workspace_dir: Path
    tool_use_session_id: str

    @property
    def root(self) -> Path:
        return self.workspace_dir / "tool_use_sessions" / self.tool_use_session_id

    @property
    def context_dir(self) -> Path:
        return self.root / "0_SessionContext"

    @property
    def session_report_dir(self) -> Path:
        return self.root / "SessionReport"

    @property
    def session_output_dir(self) -> Path:
        return self.root / "SessionOutput"

    def tool_dir(self, step_index: int, tool_name: str) -> Path:
        return self.root / f"S{step_index}_{tool_name}"


def ensure_tool_session_layout(paths: ToolSessionPaths) -> None:
    paths.context_dir.mkdir(parents=True, exist_ok=True)
    paths.session_report_dir.mkdir(parents=True, exist_ok=True)
    for part in ("manifests", "schemes", "reports", "media", "subtitles", "json", "packages"):
        (paths.session_output_dir / part).mkdir(parents=True, exist_ok=True)
