from __future__ import annotations

from pathlib import Path


class LocalWorkspaceStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def sessions_root(self) -> Path:
        root = self.data_dir / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def create_session_workspace(self, session_ref: str | int) -> Path:
        workspace_dir = self.sessions_root() / str(session_ref) / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir
