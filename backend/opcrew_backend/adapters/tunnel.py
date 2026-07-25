from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass


TRY_CLOUDFLARE_PATTERN = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


@dataclass
class TunnelCheckResult:
    ok: bool
    command_path: str | None
    message: str


def detect_cloudflared(command_path: str | None = None) -> TunnelCheckResult:
    resolved = command_path.strip() if command_path else shutil.which("cloudflared")
    if not resolved:
        return TunnelCheckResult(
            ok=False,
            command_path=None,
            message="cloudflared not found. Install cloudflared to start tunnel.",
        )
    return TunnelCheckResult(ok=True, command_path=resolved, message="cloudflared found")


def start_cloudflared(
    command_path: str, local_url: str, timeout_seconds: int = 25
) -> tuple[subprocess.Popen[str], str | None, str | None]:
    proc = subprocess.Popen(
        [command_path, "tunnel", "--url", local_url, "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    public_url: str | None = None
    error_msg: str | None = None
    started_at = time.time()

    while time.time() - started_at < timeout_seconds:
        if proc.poll() is not None:
            break

        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            time.sleep(0.2)
            continue

        match = TRY_CLOUDFLARE_PATTERN.search(line)
        if match:
            public_url = match.group(0)
            break

        if "error" in line.lower():
            error_msg = line.strip()

    if not public_url and proc.poll() is not None:
        tail = ""
        if proc.stdout:
            tail = proc.stdout.read().strip()
        if tail:
            error_msg = tail.splitlines()[-1]

    return proc, public_url, error_msg
