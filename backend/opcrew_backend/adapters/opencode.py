from __future__ import annotations

import json
import platform
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable
from base64 import b64encode
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HEALTH_PATH = "/global/health"


@dataclass
class ProbeResult:
    ok: bool
    base_url: str
    health_url: str
    version: str | None
    probe_status: str
    http_status: int | None
    error: str | None


@dataclass
class CredentialResult:
    username: str
    password: str
    source: str


class OpenCodeAuthError(RuntimeError):
    pass


@dataclass
class Candidate:
    process: str
    pid: int | None
    listen: str
    host: str
    port: int
    base_url: str
    source: str
    healthy: bool = False
    version: str | None = None
    probe_status: str = "unknown"
    http_status: int | None = None
    username: str | None = None
    password: str | None = None
    auth_source: str | None = None
    error: str | None = None


def _run(command: list[str] | str, shell: bool = False) -> str:
    completed = subprocess.run(
        command,
        shell=shell,
        check=False,
        capture_output=True,
        timeout=8,
    )
    stdout = completed.stdout.decode("utf-8", errors="ignore") if isinstance(completed.stdout, bytes) else str(completed.stdout)
    stderr = completed.stderr.decode("utf-8", errors="ignore") if isinstance(completed.stderr, bytes) else str(completed.stderr)
    return f"{stdout}\n{stderr}".strip()


def normalize_base_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = f"http://{text}"
    return text.rstrip("/")


def probe_server(base_url: str, username: str | None = None, password: str | None = None) -> ProbeResult:
    normalized = normalize_base_url(base_url)
    if not normalized:
        return ProbeResult(False, "", "", None, "error", None, "Empty OpenCode server URL")
    health_url = f"{normalized}{HEALTH_PATH}"
    request = Request(health_url, headers={"Accept": "application/json"})
    if username and password:
        token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urlopen(request, timeout=3) as response:
            status_code = getattr(response, "status", 200)
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        if payload.get("healthy") is True:
            version = payload.get("version")
            return ProbeResult(True, normalized, health_url, version, "healthy", status_code, None)
        return ProbeResult(False, normalized, health_url, None, "unexpected_response", status_code, "Server responded but was not healthy")
    except HTTPError as exc:
        if exc.code == 401:
            return ProbeResult(False, normalized, health_url, None, "auth_required", exc.code, "HTTP 401 Unauthorized")
        return ProbeResult(False, normalized, health_url, None, "http_error", exc.code, f"HTTP {exc.code}")
    except URLError as exc:
        return ProbeResult(False, normalized, health_url, None, "unreachable", None, str(exc.reason))
    except Exception as exc:
        return ProbeResult(False, normalized, health_url, None, "error", None, str(exc))


def discover_opencode_servers() -> dict[str, Any]:
    system = platform.system().lower()
    if system == "windows":
        candidates, process_matches = _discover_windows()
    else:
        candidates, process_matches = _discover_unix()

    dedup: dict[str, Candidate] = {}
    for candidate in candidates:
        dedup.setdefault(candidate.base_url, candidate)

    all_candidates = list(dedup.values())
    for candidate in all_candidates:
        probe = probe_server(candidate.base_url)
        candidate.healthy = probe.ok
        candidate.version = probe.version
        candidate.probe_status = probe.probe_status
        candidate.http_status = probe.http_status
        candidate.error = probe.error

        if probe.probe_status == "auth_required":
            creds = discover_candidate_credentials(system, candidate, process_matches)
            if creds:
                candidate.username = creds.username
                candidate.password = creds.password
                candidate.auth_source = creds.source
                authed = probe_server(candidate.base_url, creds.username, creds.password)
                candidate.healthy = authed.ok
                candidate.version = authed.version
                candidate.probe_status = authed.probe_status if authed.ok else probe.probe_status
                candidate.http_status = authed.http_status if authed.ok else probe.http_status
                candidate.error = authed.error if authed.ok else probe.error

    all_candidates = sorted(all_candidates, key=_candidate_rank)
    selected = all_candidates[0] if all_candidates else None

    return {
        "ok": bool(all_candidates),
        "system": system,
        "selected": asdict(selected) if selected else None,
        "candidates": [asdict(item) for item in all_candidates],
    }


def invoke_smoke(base_url: str | None, username: str | None = None, password: str | None = None) -> tuple[bool, str]:
    probe = probe_server(base_url or "", username=username, password=password)
    if probe.ok:
        return True, f"OpenCode server reachable ({probe.version or 'unknown'})"
    return False, probe.error or "OpenCode server unavailable"


def _opencode_event_session_id(value: Any, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(value, dict):
        for key in ("sessionID", "sessionId", "session_id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        for key in ("properties", "info", "message", "part", "status"):
            candidate = _opencode_event_session_id(value.get(key), depth + 1)
            if candidate:
                return candidate
        parts = value.get("parts")
        if isinstance(parts, list):
            for part in parts:
                candidate = _opencode_event_session_id(part, depth + 1)
                if candidate:
                    return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _opencode_event_session_id(item, depth + 1)
            if candidate:
                return candidate
    return ""


class OpenCodeSessionClient:
    def __init__(self, base_url: str, username: str, password: str, directory: str) -> None:
        self.base_url = normalize_base_url(base_url)
        self.directory = directory
        token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.auth_header = f"Basic {token}"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> Any:
        query_string = f"?{urlencode(query)}" if query else ""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(
            f"{self.base_url}{path}{query_string}",
            data=data,
            method=method,
            headers={
                "Authorization": self.auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="ignore")
                if not raw:
                    return None
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 401:
                raise OpenCodeAuthError("OpenCode authentication failed (HTTP 401). Refresh the OpenCode connection and try again.") from exc
            raise

    def create_session(self, title: str, *, timeout: int = 30) -> dict[str, Any]:
        session = self._request("POST", "/session", {"title": title}, query={"directory": self.directory}, timeout=timeout)
        if not session or "id" not in session:
            raise RuntimeError("Failed to create OpenCode session")
        return session

    def delete_session(self, session_id: str, *, timeout: int = 30) -> bool:
        if not str(session_id or "").strip():
            return False
        result = self._request("DELETE", f"/session/{quote(str(session_id), safe='')}", None, query={"directory": self.directory}, timeout=timeout)
        return result is not False

    def providers(self, *, timeout: int = 30) -> dict[str, Any]:
        return self._request("GET", "/provider", None, query={"directory": self.directory}, timeout=timeout) or {}

    def prompt_async(
        self,
        session_id: str,
        prompt: str,
        *,
        model: dict[str, str] | None = None,
        agent: str | None = None,
        system: str | None = None,
        tools: dict[str, bool] | None = None,
        parts: list[dict[str, Any]] | None = None,
        timeout: int = 30,
    ) -> None:
        payload: dict[str, Any] = {
            "parts": parts if parts is not None else [{"type": "text", "text": prompt}],
        }
        if model:
            payload["model"] = {
                "providerID": model.get("providerID") or "",
                "modelID": model.get("modelID") or "",
            }
        if agent:
            payload["agent"] = agent
        if system:
            payload["system"] = system
        if tools is not None:
            payload["tools"] = tools
        self._request(
            "POST",
            f"/session/{session_id}/prompt_async",
            payload,
            query={"directory": self.directory},
            timeout=timeout,
        )

    def messages(self, session_id: str, limit: int = 50, *, timeout: int = 30) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            f"/session/{session_id}/message",
            None,
            query={"directory": self.directory, "limit": str(limit)},
            timeout=timeout,
        ) or []

    def todo(self, session_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/session/{session_id}/todo", None, query={"directory": self.directory}, timeout=30) or []

    def abort(self, session_id: str) -> None:
        self._request("POST", f"/session/{session_id}/abort", None, query={"directory": self.directory}, timeout=30)

    def collect_events(
        self,
        session_id: str,
        stop_event: threading.Event,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        req = Request(f"{self.base_url}/global/event", headers={"Authorization": self.auth_header, "Accept": "text/event-stream"})
        try:
            with urlopen(req, timeout=600) as response:
                while not stop_event.is_set():
                    raw = response.readline().decode("utf-8", errors="ignore").strip()
                    if not raw or not raw.startswith("data: "):
                        continue
                    try:
                        event = json.loads(raw[6:])
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload") or {}
                    candidate_session_id = _opencode_event_session_id(payload)
                    if candidate_session_id != session_id:
                        continue
                    on_event(payload)
        except Exception as exc:
            if not stop_event.is_set():
                on_event({"type": "session.stream.error", "properties": {"message": str(exc)}})


def _candidate_rank(item: Candidate) -> tuple[int, int, int]:
    probe_rank = {
        "healthy": 0,
        "auth_required": 1,
        "unexpected_response": 2,
        "http_error": 3,
        "unreachable": 4,
        "error": 5,
        "unknown": 6,
    }
    return (
        probe_rank.get(item.probe_status, 99),
        item.port,
        item.pid or 0,
    )


def _discover_unix() -> tuple[list[Candidate], dict[int, str]]:
    ps_output = _run(["ps", "-axo", "pid=,command="])

    process_matches: dict[int, str] = {}
    for line in ps_output.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(None, 1)
        if len(parts) != 2:
            continue
        if not _is_opencode_process(parts[1]):
            continue
        try:
            process_matches[int(parts[0])] = parts[1]
        except ValueError:
            continue

    candidates: list[Candidate] = []
    for pid, command_line in process_matches.items():
        listen_output = _run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"])
        for line in listen_output.splitlines():
            row = line.strip()
            if not row or row.startswith("COMMAND"):
                continue
            parts = row.split()
            if len(parts) < 9:
                continue
            listen = parts[-2] if parts[-1].startswith("(") else parts[-1]
            host, port = _parse_listen_name(listen)
            if host is None or port is None:
                continue
            candidates.append(
                Candidate(
                    process=command_line,
                    pid=pid,
                    listen=listen,
                    host=host,
                    port=port,
                    base_url=f"http://{host}:{port}",
                    source="process",
                )
            )
    return candidates, process_matches


def _discover_windows() -> tuple[list[Candidate], dict[int, str]]:
    task_output = _run("tasklist | findstr /I opencode", shell=True)
    command_output = _run(
        'wmic process where "name like \'%opencode%\' or commandline like \'%OpenCode%\' or commandline like \'%opencode%\'" get ProcessId,CommandLine',
        shell=True,
    )
    netstat_output = _run("netstat -ano | findstr LISTENING", shell=True)

    process_matches: dict[int, str] = {}
    for line in task_output.splitlines():
        row = line.strip()
        if not row:
            continue
        parts = row.split()
        if len(parts) < 2:
            continue
        try:
            process_matches[int(parts[1])] = parts[0]
        except ValueError:
            continue

    for line in command_output.splitlines():
        row = line.strip()
        if not row or row.lower().startswith("commandline"):
            continue
        match = re.search(r"(\d+)\s*$", row)
        if not match:
            continue
        pid = int(match.group(1))
        command_line = row[: match.start()].strip()
        if not _is_opencode_process(command_line):
            continue
        process_matches[pid] = command_line or process_matches.get(pid, "opencode")

    candidates: list[Candidate] = []
    for line in netstat_output.splitlines():
        row = line.strip()
        if not row or "LISTENING" not in row.upper():
            continue
        parts = row.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid not in process_matches:
            continue
        host, port = _parse_windows_listen(local_addr)
        if host is None or port is None:
            continue
        candidates.append(
            Candidate(
                process=process_matches.get(pid, "opencode"),
                pid=pid,
                listen=local_addr,
                host=host,
                port=port,
                base_url=f"http://{host}:{port}",
                source="process",
            )
        )
    return candidates, process_matches


def _parse_listen_name(value: str) -> tuple[str | None, int | None]:
    text = value.strip()
    if "->" in text:
        return None, None
    if text.startswith("*:"):
        host = "127.0.0.1"
        port_text = text.split(":", 1)[1]
        return host, _safe_port(port_text)
    if text.startswith("["):
        match = re.match(r"\[(.+)\]:(\d+)$", text)
        if not match:
            return None, None
        host = match.group(1)
        if host in {"::", "::1"}:
            host = "127.0.0.1"
        return host, _safe_port(match.group(2))
    if ":" not in text:
        return None, None
    host, port_text = text.rsplit(":", 1)
    if host in {"*", "0.0.0.0", "::", "::1"}:
        host = "127.0.0.1"
    return host, _safe_port(port_text)


def _parse_windows_listen(value: str) -> tuple[str | None, int | None]:
    text = value.strip()
    if text.startswith("["):
        match = re.match(r"\[(.+)\]:(\d+)$", text)
        if not match:
            return None, None
        host = match.group(1)
        if host in {"::", "::1"}:
            host = "127.0.0.1"
        return host, _safe_port(match.group(2))
    if ":" not in text:
        return None, None
    host, port_text = text.rsplit(":", 1)
    if host in {"0.0.0.0", "*", "::", "::1"}:
        host = "127.0.0.1"
    return host, _safe_port(port_text)


def _safe_port(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _is_opencode_process(command: str) -> bool:
    lowered = command.lower()
    return any(
        marker in lowered
        for marker in (
            "opencode serve",
            "opencode-cli",
            "/macos/opencode",
            "\\opencode.exe",
            "opencode.exe",
            "opencode --",
            " opencode ",
            "opencode.app",
        )
    )


def discover_candidate_credentials(
    system: str, candidate: Candidate, process_matches: dict[int, str]
) -> CredentialResult | None:
    if candidate.pid is not None:
        direct = _credentials_from_pid(system, candidate.pid)
        if direct:
            return direct

    by_port = _credentials_from_related_process(candidate.port, process_matches)
    if by_port:
        return by_port

    return None


def _credentials_from_pid(system: str, pid: int) -> CredentialResult | None:
    if system == "windows":
        command_line = _run(
            f'powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \'ProcessId = {pid}\').CommandLine"',
            shell=True,
        )
        return _credentials_from_command(command_line, "process_args")

    env_output = _run(["ps", "eww", "-p", str(pid)])
    env_creds = _credentials_from_env_output(env_output)
    if env_creds:
        return env_creds

    command_line = _run(["ps", "-p", str(pid), "-o", "command="])
    return _credentials_from_command(command_line, "process_args")


def _credentials_from_related_process(port: int, process_matches: dict[int, str]) -> CredentialResult | None:
    port_pattern = re.compile(rf"--opencode-port\s+{port}(?:\s|$)")
    for command in process_matches.values():
        if not port_pattern.search(command):
            continue
        creds = _credentials_from_command(command, "related_process_args")
        if creds:
            return creds
    return None


def _credentials_from_env_output(text: str) -> CredentialResult | None:
    user = _extract_env_var(text, "OPENCODE_SERVER_USERNAME") or "opencode"
    password = _extract_env_var(text, "OPENCODE_SERVER_PASSWORD")
    if password:
        return CredentialResult(username=user, password=password, source="process_env")
    return None


def _credentials_from_command(text: str, source: str) -> CredentialResult | None:
    username = _extract_flag(text, "--opencode-username") or _extract_flag(text, "--username") or "opencode"
    password = _extract_flag(text, "--opencode-password") or _extract_flag(text, "--password")
    if password:
        return CredentialResult(username=username, password=password, source=source)
    return None


def _extract_env_var(text: str, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]+)", text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_flag(text: str, flag: str) -> str | None:
    patterns = [
        rf"{re.escape(flag)}\s+([^\s\"]+)",
        rf'{re.escape(flag)}\s+"([^\"]+)"',
        rf"{re.escape(flag)}=([^\s\"]+)",
        rf'{re.escape(flag)}="([^\"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None
