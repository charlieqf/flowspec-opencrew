from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..adapters.npc import SkillResultError, detect_environment, ensure_default_skills, execute_skill, get_default_skill
from ..context import AppContext, now_ms
from ..schemas import NpcRunPayload, NpcSkillPayload

FIXED_NPC_SERVER_ADDR = "113.125.202.171:8024"
NPC_MULTI_ACCOUNT_PATH_ENV = "OPENCREW_NPC_MULTI_ACCOUNT_PATH"
LEGACY_NPC_BASIC_USERNAME = "11"
LEGACY_NPC_BASIC_PASSWORD = "3"


DEFAULT_NPC_CONFIG: dict[str, Any] = {
    "server_addr": FIXED_NPC_SERVER_ADDR,
    "public_base_url": "www.goldenstand.cn",
    "vkey": "Has1Password01",
    "conn_type": "tcp",
    "auto_reconnection": True,
    "max_conn": 1000,
    "flow_limit": 1000,
    "rate_limit": 1000,
    "basic_username": "",
    "basic_password": "",
    "crypt": True,
    "compress": True,
    "disconnect_timeout": 60,
    "mode": "tcp",
    "target_addr": "127.0.0.1:18080",
    "server_port": 10000,
    "multi_account_line": "npc=npc.pwd",
    "conf_text": "",
    "multi_account_text": "",
}


def normalize_npc_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    username = str(normalized.get("basic_username") or "").strip()
    password = str(normalized.get("basic_password") or "").strip()
    if username == LEGACY_NPC_BASIC_USERNAME and password == LEGACY_NPC_BASIC_PASSWORD:
        username = ""
        password = ""
    if not username or not password:
        username = ""
        password = ""
    normalized["basic_username"] = username
    normalized["basic_password"] = password
    return normalized


def sanitize_npc_conf_text(text: str, config: dict[str, Any]) -> str:
    raw = str(text or "")
    if not raw.strip():
        return build_npc_conf_text(config)
    normalized = normalize_npc_config(config)
    if normalized.get("basic_username") and normalized.get("basic_password"):
        return raw
    lines: list[str] = []
    for line in raw.splitlines():
        key = line.split("=", 1)[0].strip().lower()
        if key in {"basic_username", "basic_password"}:
            continue
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def build_npc_conf_text(config: dict[str, Any]) -> str:
    normalized = normalize_npc_config(config)
    lines = [
        "[common]",
        f"server_addr={normalized['server_addr']}",
        f"conn_type={normalized['conn_type']}",
        f"vkey={normalized['vkey']}",
        f"auto_reconnection={'true' if normalized['auto_reconnection'] else 'false'}",
        f"max_conn={normalized['max_conn']}",
        f"flow_limit={normalized['flow_limit']}",
        f"rate_limit={normalized['rate_limit']}",
    ]
    if normalized["basic_username"] and normalized["basic_password"]:
        lines.extend(
            [
                f"basic_username={normalized['basic_username']}",
                f"basic_password={normalized['basic_password']}",
            ]
        )
    lines.extend(
        [
            f"crypt={'true' if normalized['crypt'] else 'false'}",
            f"compress={'true' if normalized['compress'] else 'false'}",
            f"disconnect_timeout={normalized['disconnect_timeout']}",
            "",
            f"[{normalized['mode']}]",
            f"mode={normalized['mode']}",
            f"target_addr={normalized['target_addr']}",
            f"server_port={normalized['server_port']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_npc_multi_account_text(config: dict[str, Any]) -> str:
    return str(config.get("multi_account_line") or "npc=npc.pwd") + "\n"


def build_step2_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    ensure_default_skills(ctx.skill_repo, now_ms())

    def get_runtime() -> dict[str, Any]:
        return ctx.runtime_repo.get_runtime("npc") or {}

    def get_skill_content(kind: str) -> str:
        row = ctx.skill_repo.get("npc", kind)
        if row and row.get("content"):
            return str(row["content"])
        return get_default_skill(kind)["content"]

    def update_runtime(**fields: Any) -> None:
        if not fields:
            return
        ctx.runtime_repo.update_runtime("npc", **fields, updated_at=now_ms())

    def log_task(task_id: int, phase: str, level: str, message: str) -> None:
        timestamp = now_ms()
        ctx.task_repo.add_log(task_id, phase, level, message, timestamp)
        ctx.event(level if level in {"info", "warn", "error"} else "info", "npc", message, {"task_id": task_id, "phase": phase})

    def clean_log_line(text: str) -> str:
        return text.replace("\x1b[1;34m[I]\x1b[0m", "").replace("\x1b[1;31m[E]\x1b[0m", "").replace("\x1b[1;33m[W]\x1b[0m", "").strip()

    def npc_runtime_root() -> Path:
        root = ctx.data_dir / "npc"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def npc_conf_dir() -> Path:
        conf_dir = npc_runtime_root() / "conf"
        conf_dir.mkdir(parents=True, exist_ok=True)
        return conf_dir

    def npc_conf_path() -> Path:
        return npc_conf_dir() / "npc.conf"

    def npc_multi_account_path() -> Path:
        configured = os.getenv(NPC_MULTI_ACCOUNT_PATH_ENV, "").strip()
        path = Path(configured).expanduser() if configured else npc_conf_dir() / "multi_account.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def npc_log_path() -> Path:
        return npc_runtime_root() / "npc.log"

    def target_server_parts() -> tuple[str, int]:
        host, port_text = FIXED_NPC_SERVER_ADDR.rsplit(":", 1)
        return host, int(port_text)

    def list_npc_processes() -> list[dict[str, Any]]:
        try:
            completed = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=8, check=False)
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        for line in (completed.stdout or "").splitlines():
            raw = line.strip()
            if not raw:
                continue
            parts = raw.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            command = parts[1]
            executable = Path(command.split()[0]).name.lower() if command.split() else ""
            if executable != "npc":
                continue
            items.append({"pid": pid, "command": command})
        return items

    def detect_target_npc_processes() -> list[dict[str, Any]]:
        target_host, target_port = target_server_parts()
        target_marker = f"->{target_host}:{target_port}"
        matches: list[dict[str, Any]] = []
        for proc in list_npc_processes():
            try:
                completed = subprocess.run(
                    ["lsof", "-nP", "-a", "-p", str(proc["pid"]), "-iTCP", "-sTCP:ESTABLISHED"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
            except Exception:
                continue
            matched_lines = [line.strip() for line in (completed.stdout or "").splitlines() if target_marker in line]
            if matched_lines:
                matches.append({**proc, "connections": matched_lines, "server_addr": FIXED_NPC_SERVER_ADDR})
        return matches

    def pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def pid_state(pid: int) -> str:
        try:
            completed = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=8, check=False)
        except Exception:
            return ""
        return (completed.stdout or "").strip().upper()

    def pid_is_effectively_stopped(pid: int) -> bool:
        if not pid_exists(pid):
            return True
        state = pid_state(pid)
        return "Z" in state if state else False

    def pid_has_target_connection(pid: int) -> bool:
        target_host, target_port = target_server_parts()
        target_marker = f"->{target_host}:{target_port}"
        try:
            completed = subprocess.run(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:ESTABLISHED"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return False
        return any(target_marker in line for line in (completed.stdout or "").splitlines())

    def clear_managed_npc_process_if_needed(pid: int) -> None:
        process = getattr(ctx, "npc_process", None)
        if process and process.pid == pid:
            ctx.npc_process = None

    def terminate_pid(pid: int) -> str:
        clear_managed_npc_process_if_needed(pid)
        if pid_is_effectively_stopped(pid) or not pid_has_target_connection(pid):
            return "already_stopped"
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 3
        while time.time() < deadline:
            if pid_is_effectively_stopped(pid):
                return "terminated"
            if not pid_has_target_connection(pid):
                return "disconnected"
            time.sleep(0.1)
        if not pid_is_effectively_stopped(pid):
            os.kill(pid, signal.SIGKILL)
            deadline = time.time() + 2
            while time.time() < deadline:
                if pid_is_effectively_stopped(pid):
                    return "killed"
                if not pid_has_target_connection(pid):
                    return "disconnected"
                time.sleep(0.1)
        if pid_is_effectively_stopped(pid):
            return "killed"
        return "still_running" if pid_has_target_connection(pid) else "disconnected"

    def stop_target_npc_processes() -> dict[str, Any]:
        matches = detect_target_npc_processes()
        stopped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        seen: set[int] = set()
        for match in matches:
            pid = int(match["pid"])
            if pid in seen:
                continue
            seen.add(pid)
            status = terminate_pid(pid)
            item = {"pid": pid, "command": match["command"], "status": status}
            if status in {"terminated", "killed", "already_stopped", "disconnected"}:
                stopped.append(item)
            else:
                failed.append(item)
        return {
            "server_addr": FIXED_NPC_SERVER_ADDR,
            "found": matches,
            "stopped": stopped,
            "failed": failed,
            "stopped_count": len(stopped),
            "failed_count": len(failed),
            "ok": len(failed) == 0,
            "message": f"Stopped {len(stopped)} npc process(es) connected to {FIXED_NPC_SERVER_ADDR}." if matches else f"No npc process connected to {FIXED_NPC_SERVER_ADDR} was running.",
        }

    def get_npc_config() -> dict[str, Any]:
        config = dict(DEFAULT_NPC_CONFIG)
        for key in list(config.keys()):
            stored = ctx.get_setting(f"npc.{key}")
            if stored is not None:
                config[key] = stored
        config["server_addr"] = FIXED_NPC_SERVER_ADDR
        config = normalize_npc_config(config)
        config["conf_text"] = sanitize_npc_conf_text(str(config.get("conf_text") or ""), config)
        config["multi_account_text"] = str(config.get("multi_account_text") or build_npc_multi_account_text(config))
        return config

    def save_npc_config(config: dict[str, Any]) -> dict[str, Any]:
        merged = dict(DEFAULT_NPC_CONFIG)
        merged.update(config)
        merged["server_addr"] = FIXED_NPC_SERVER_ADDR
        merged = normalize_npc_config(merged)
        merged["conf_text"] = sanitize_npc_conf_text(str(merged.get("conf_text") or ""), merged)
        merged["multi_account_text"] = str(merged.get("multi_account_text") or build_npc_multi_account_text(merged))
        for key, value in merged.items():
            ctx.set_setting(f"npc.{key}", value)
        return merged

    def build_conf_text(config: dict[str, Any]) -> str:
        return build_npc_conf_text(config)

    def build_multi_account_text(config: dict[str, Any]) -> str:
        return build_npc_multi_account_text(config)

    def generate_conf(config: dict[str, Any]) -> dict[str, str]:
        conf_path = npc_conf_path()
        multi_path = npc_multi_account_path()
        config = normalize_npc_config(config)
        conf_text = sanitize_npc_conf_text(str(config.get("conf_text") or ""), config)
        multi_text = str(config.get("multi_account_text") or build_multi_account_text(config))
        conf_path.write_text(conf_text)
        multi_path.write_text(multi_text)
        return {"conf_path": str(conf_path), "multi_account_path": str(multi_path)}

    def stop_npc_process() -> None:
        process = getattr(ctx, "npc_process", None)
        if not process:
            return
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        ctx.npc_process = None

    def current_binary_path() -> str | None:
        env = detect_environment(ctx.data_dir)
        return env.get("command_path")

    def create_task(kind: str, skill_snapshot: str) -> int:
        task_id = ctx.task_repo.create(kind, "queued", skill_snapshot, now_ms(), now_ms())
        update_runtime(last_task_id=task_id, last_error=None)
        return task_id

    def start_opencode_task(kind: str, on_success: callable | None = None, pre_hook: callable | None = None) -> dict[str, Any]:
        skill_content = get_skill_content(kind)
        task_id = create_task(kind, skill_content)

        def runner() -> None:
            ctx.task_repo.update(task_id, status="running", started_at=now_ms())
            try:
                if pre_hook:
                    pre_hook(task_id)
                payload, session_id = execute_skill(
                    kind,
                    skill_content,
                    ctx.data_dir,
                    lambda phase, level, message: log_task(task_id, phase, level, message),
                    opencode_base_url=str(ctx.get_setting("opencode.base_url") or ""),
                    opencode_username=str(ctx.get_setting("opencode.username") or ""),
                    opencode_password=str(ctx.get_setting("opencode.password") or ""),
                )
                if on_success:
                    payload = on_success(payload, task_id)
                ctx.task_repo.update(
                    task_id,
                    session_id=session_id,
                    status="succeeded",
                    summary=json.dumps(payload, ensure_ascii=True),
                    error=None,
                    finished_at=now_ms(),
                )
                env = detect_environment(ctx.data_dir)
                runtime_fields = {
                    "platform": env["platform"],
                    "arch": env["arch"],
                    "brew_available": 1 if env["brew_available"] else 0,
                    "npc_installed": 1 if env["installed"] else 0,
                    "available": 1 if env["available"] else 0,
                    "command_path": env["command_path"],
                    "managed_path": env["managed_path"],
                    "version": env["version"],
                    "install_method": payload.get("install_method") or env.get("install_method"),
                    "installed_by_opencrew": 1 if (env.get("command_path") == env.get("managed_path") and env.get("installed")) else 0,
                    "last_result": json.dumps(payload, ensure_ascii=True),
                    "last_error": None,
                }
                if kind == "install":
                    runtime_fields["install_status"] = "installed"
                    runtime_fields["verify_status"] = "idle"
                elif kind == "uninstall":
                    runtime_fields["install_status"] = "idle"
                    runtime_fields["verify_status"] = "idle"
                    runtime_fields["npc_installed"] = 1 if env["installed"] else 0
                    runtime_fields["command_path"] = env["command_path"]
                    runtime_fields["version"] = env["version"]
                    runtime_fields["installed_by_opencrew"] = 0
                update_runtime(**runtime_fields)
                log_task(task_id, "result", "info", payload.get("message") or f"{kind} task completed")
            except SkillResultError as exc:
                error_payload = exc.payload or {}
                ctx.task_repo.update(
                    task_id,
                    status="failed",
                    summary=json.dumps(error_payload, ensure_ascii=True) if error_payload else None,
                    error=exc.message,
                    finished_at=now_ms(),
                )
                env = detect_environment(ctx.data_dir)
                update_runtime(
                    platform=env["platform"],
                    arch=env["arch"],
                    brew_available=1 if env["brew_available"] else 0,
                    npc_installed=1 if env["installed"] else 0,
                    available=1 if env["available"] else 0,
                    command_path=env["command_path"],
                    managed_path=env["managed_path"],
                    version=env["version"],
                    install_status="failed" if kind == "install" else get_runtime().get("install_status") or "idle",
                    verify_status="failed" if kind == "uninstall" else get_runtime().get("verify_status") or "idle",
                    last_error=exc.message,
                )
                log_task(task_id, "result", "error", exc.message)

        threading.Thread(target=runner, name=f"npc-{kind}-{task_id}", daemon=True).start()
        return {"ok": True, "task_id": task_id}

    def install_post_success(payload: dict[str, Any], task_id: int) -> dict[str, Any]:
        config = get_npc_config()
        paths = generate_conf(config)
        log_task(task_id, "config", "info", f"Generated npc.conf at {paths['conf_path']}")
        log_task(task_id, "config", "info", f"Generated multi_account.conf at {paths['multi_account_path']}")
        payload.update(paths)
        return payload

    def uninstall_prehook(task_id: int) -> None:
        stop_npc_process()
        log_task(task_id, "service", "info", "Stopped existing npc process before uninstall")

    def uninstall_post_success(payload: dict[str, Any], task_id: int) -> dict[str, Any]:
        conf_dir = npc_conf_dir()
        if conf_dir.exists():
            shutil.rmtree(conf_dir, ignore_errors=True)
            log_task(task_id, "config", "info", f"Removed generated config directory {conf_dir}")
        payload["conf_removed"] = True
        return payload

    def parse_run_result(lines: list[str], config: dict[str, Any], conf_paths: dict[str, str], command_path: str) -> dict[str, Any]:
        config_loaded = any("Loading configuration file" in line for line in lines)
        server_connected = any("Successful connection with server" in line for line in lines)
        eof_line = next((line for line in lines if "Accept server data error EOF" in line), None)
        proxy_error = next((line for line in lines if "The server returned an error" in line), None)
        reconnecting = any("Reconnecting..." in line or "reconnected" in line.lower() for line in lines)
        stable = config_loaded and server_connected and eof_line is None and proxy_error is None
        excerpt = " | ".join(lines[-8:]) if lines else ""
        failed_step = ""
        recommended_fix = ""
        message = "NPC config loaded, connected to the server, and the proxy registration stayed stable."
        error = ""
        connection_status = "connected" if stable else "not_run"
        ok = stable
        if not config_loaded:
            failed_step = "config_load"
            recommended_fix = "Check the generated npc.conf syntax and confirm the file is readable."
            message = "NPC did not load the generated configuration file."
            error = "Config load failed."
            connection_status = "connect_failed"
            ok = False
        elif proxy_error:
            failed_step = "proxy_registration"
            recommended_fix = "The server rejected the tcp mapping. Check server_port, target_addr, and whether that port is already occupied or disallowed on NPS."
            message = "NPC loaded the config and reached the server, but proxy registration was rejected by NPS."
            error = proxy_error
            connection_status = "server_reachable"
            ok = False
        elif eof_line:
            failed_step = "handshake"
            recommended_fix = "Server reachability is OK; correct handshake/auth settings on NPS side so the session is not closed with EOF."
            message = "NPC reached the server, but the handshake was closed with EOF."
            error = eof_line
            connection_status = "handshake_failed"
            ok = False
        elif not server_connected:
            failed_step = "server_connect"
            recommended_fix = "Check server_addr and the NPS bridge port, then rerun the service start."
            message = "NPC could not establish a successful connection to the NPS server."
            error = "No successful connection log line was observed."
            connection_status = "connect_failed"
            ok = False
        return {
            "ok": ok,
            "available": ok,
            "command_path": command_path,
            "config_path": conf_paths["conf_path"],
            "multi_account_path": conf_paths["multi_account_path"],
            "server_addr": config["server_addr"],
            "target_addr": config["target_addr"],
            "server_port": config["server_port"],
            "vkey_present": bool(config.get("vkey")),
            "config_loaded": config_loaded,
            "server_reachable": server_connected,
            "handshake_ok": eof_line is None and server_connected,
            "proxy_registration_ok": proxy_error is None and server_connected,
            "connection_status": connection_status,
            "connection_log_excerpt": excerpt,
            "failed_step": failed_step,
            "recommended_fix": recommended_fix,
            "message": message,
            "error": error,
            "reconnecting": reconnecting,
        }

    def start_run_task(config: dict[str, Any]) -> dict[str, Any]:
        config = save_npc_config(config)
        skill_snapshot = get_skill_content("run")
        task_id = create_task("run", skill_snapshot)

        def runner() -> None:
            ctx.task_repo.update(task_id, status="running", started_at=now_ms())
            try:
                env = detect_environment(ctx.data_dir)
                command_path = env.get("command_path")
                if not command_path:
                    raise SkillResultError("npc is not installed yet. Install NPC before running the service.")
                stop_result = stop_target_npc_processes()
                for item in stop_result["stopped"]:
                    log_task(task_id, "service", "info", f"Stopped npc pid={item['pid']} before reconnect ({item['status']})")
                for item in stop_result["failed"]:
                    log_task(task_id, "service", "error", f"Failed to stop npc pid={item['pid']} before reconnect ({item['status']})")
                if stop_result["failed"]:
                    raise SkillResultError(f"Failed to stop existing npc connection(s) to {FIXED_NPC_SERVER_ADDR} before reconnect.")
                conf_paths = generate_conf(config)
                log_task(task_id, "config", "info", f"Generated npc.conf at {conf_paths['conf_path']}")
                log_task(task_id, "config", "info", f"Generated multi_account.conf at {conf_paths['multi_account_path']}")
                log_path = npc_log_path()
                if log_path.exists():
                    log_path.unlink()
                with log_path.open("w", encoding="utf-8") as handle:
                    process = subprocess.Popen([command_path, "-config", conf_paths["conf_path"]], stdout=handle, stderr=subprocess.STDOUT, text=True)
                ctx.npc_process = process
                log_task(task_id, "run", "info", f"Started npc with -config {conf_paths['conf_path']}")
                seen = 0
                lines: list[str] = []
                deadline = time.time() + 12
                success_after = None
                while time.time() < deadline:
                    if log_path.exists():
                        content = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                        for line in content[seen:]:
                            clean = clean_log_line(line)
                            if clean:
                                lines.append(clean)
                                log_task(task_id, "run", "info", clean)
                        seen = len(content)
                    if any("Successful connection with server" in line for line in lines) and success_after is None:
                        success_after = time.time() + 3
                    if any("Accept server data error EOF" in line for line in lines) or any("The server returned an error" in line for line in lines):
                        break
                    if success_after and time.time() >= success_after and process.poll() is None:
                        break
                    if process.poll() is not None and not lines:
                        break
                    time.sleep(0.5)
                result = parse_run_result(lines, config, conf_paths, command_path)
                if not result["ok"]:
                    stop_npc_process()
                update_runtime(
                    platform=env["platform"],
                    arch=env["arch"],
                    brew_available=1 if env["brew_available"] else 0,
                    npc_installed=1 if env["installed"] else 0,
                    available=1 if env["available"] else 0,
                    command_path=command_path,
                    managed_path=env["managed_path"],
                    version=env["version"],
                    install_method=env.get("install_method"),
                    installed_by_opencrew=1 if (env.get("command_path") == env.get("managed_path") and env.get("installed")) else 0,
                    install_status="installed",
                    verify_status="available" if result["ok"] else "failed",
                    last_result=json.dumps(result, ensure_ascii=True),
                    last_error=None if result["ok"] else result["error"],
                )
                ctx.task_repo.update(
                    task_id,
                    status="succeeded",
                    summary=json.dumps(result, ensure_ascii=True),
                    error=None,
                    finished_at=now_ms(),
                )
                log_task(task_id, "result", "info", result["message"])
            except Exception as exc:
                stop_npc_process()
                error_text = str(exc)
                ctx.task_repo.update(task_id, status="failed", summary=None, error=error_text, finished_at=now_ms())
                update_runtime(verify_status="failed", last_error=error_text)
                log_task(task_id, "result", "error", error_text)

        threading.Thread(target=runner, name=f"npc-run-{task_id}", daemon=True).start()
        return {"ok": True, "task_id": task_id}

    @router.get("/api/setup/npc/config")
    async def npc_config() -> dict[str, Any]:
        config = get_npc_config()
        config.update({"conf_path": str(npc_conf_path()), "multi_account_path": str(npc_multi_account_path())})
        return config

    @router.put("/api/setup/npc/config")
    async def npc_config_save(payload: NpcRunPayload) -> dict[str, Any]:
        config = save_npc_config(payload.model_dump())
        paths = generate_conf(config)
        return {"ok": True, **config, **paths}

    @router.get("/api/setup/npc/status")
    async def npc_status() -> dict[str, Any]:
        return get_runtime()

    @router.post("/api/setup/npc/detect")
    async def npc_detect() -> dict[str, Any]:
        env = detect_environment(ctx.data_dir)
        update_runtime(
            environment_status="available" if env["available"] else "failed",
            platform=env["platform"],
            arch=env["arch"],
            brew_available=1 if env["brew_available"] else 0,
            npc_installed=1 if env["installed"] else 0,
            installed_by_opencrew=1 if env.get("command_path") == env.get("managed_path") and env.get("installed") else 0,
            available=1 if env["available"] else 0,
            command_path=env["command_path"],
            managed_path=env["managed_path"],
            version=env["version"],
            install_method=env.get("install_method"),
            last_error=None,
        )
        ctx.event("info", "npc", "NPC environment detected", env)
        return {"ok": True, "runtime": get_runtime(), "environment": env}

    @router.post("/api/setup/npc/install")
    async def npc_install() -> dict[str, Any]:
        update_runtime(install_status="installing")
        return start_opencode_task("install", on_success=install_post_success)

    @router.post("/api/setup/npc/run")
    async def npc_run(payload: NpcRunPayload) -> dict[str, Any]:
        update_runtime(verify_status="verifying")
        return start_run_task(payload.model_dump())

    @router.post("/api/setup/npc/reconnect")
    async def npc_reconnect(payload: NpcRunPayload) -> dict[str, Any]:
        update_runtime(verify_status="verifying")
        return start_run_task(payload.model_dump())

    @router.post("/api/setup/npc/stop")
    async def npc_stop() -> dict[str, Any]:
        result = stop_target_npc_processes()
        update_runtime(
            verify_status="idle",
            last_error=None if result["ok"] else f"Failed stopping one or more npc processes for {FIXED_NPC_SERVER_ADDR}",
            last_result=json.dumps(result, ensure_ascii=True),
        )
        if not result["ok"]:
            raise HTTPException(status_code=500, detail=result["message"])
        ctx.event("info", "npc", result["message"], {"stopped": result["stopped"], "server_addr": FIXED_NPC_SERVER_ADDR})
        return result

    @router.post("/api/setup/npc/uninstall")
    async def npc_uninstall() -> dict[str, Any]:
        update_runtime(install_status="uninstalling")
        return start_opencode_task("uninstall", pre_hook=uninstall_prehook, on_success=uninstall_post_success)

    @router.get("/api/setup/npc/tasks/{task_id}")
    async def npc_task(task_id: int) -> dict[str, Any]:
        task = ctx.task_repo.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @router.get("/api/setup/npc/tasks/{task_id}/logs")
    async def npc_task_logs(task_id: int) -> dict[str, Any]:
        task = ctx.task_repo.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        logs = ctx.task_repo.list_logs(task_id)
        return {"items": logs}

    @router.get("/api/setup/npc/skills/{kind}")
    async def npc_skill(kind: str) -> dict[str, Any]:
        if kind not in {"install", "run", "uninstall"}:
            raise HTTPException(status_code=404, detail="Skill not found")
        row = ctx.skill_repo.get("npc", kind)
        if not row:
            raise HTTPException(status_code=404, detail="Skill not found")
        row["default_content"] = get_default_skill(kind)["content"]
        return row

    @router.put("/api/setup/npc/skills/{kind}")
    async def npc_skill_save(kind: str, payload: NpcSkillPayload) -> dict[str, Any]:
        if kind not in {"install", "run", "uninstall"}:
            raise HTTPException(status_code=404, detail="Skill not found")
        title = get_default_skill(kind)["title"]
        ctx.skill_repo.upsert("npc", kind, title, payload.content.strip(), now_ms())
        ctx.event("info", "npc", "NPC skill updated", {"kind": kind})
        return {"ok": True}

    @router.post("/api/setup/npc/skills/{kind}/restore-default")
    async def npc_skill_restore(kind: str) -> dict[str, Any]:
        if kind not in {"install", "run", "uninstall"}:
            raise HTTPException(status_code=404, detail="Skill not found")
        default = get_default_skill(kind)
        ctx.skill_repo.upsert("npc", kind, default["title"], default["content"], now_ms())
        ctx.event("info", "npc", "NPC skill restored", {"kind": kind})
        return {"ok": True}

    return router
