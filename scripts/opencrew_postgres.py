from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path.home() / ".opencrew" / "postgres"
DEFAULT_LOG_PATH = DEFAULT_DATA_DIR / "postgres.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5433
DEFAULT_USER = "opencrew"
DEFAULT_PASSWORD = "opencrew"
DEFAULT_DATABASE = "opencrew"


def default_bin_dir() -> Path:
    candidates = [
        Path("/opt/homebrew/opt/postgresql@16/bin"),
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/opt/homebrew/opt/postgresql@15/bin"),
        Path("/opt/homebrew/bin"),
        Path("/usr/local/opt/postgresql@16/bin"),
        Path("/usr/local/opt/postgresql@15/bin"),
        Path("/usr/local/bin"),
        WORKSPACE_ROOT / "node_modules" / "@embedded-postgres" / "darwin-arm64" / "native" / "bin",
    ]
    for candidate in candidates:
        if (candidate / "pg_ctl").exists() and (candidate / "initdb").exists():
            return candidate
    return candidates[0]


def read_env(name: str, fallback: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or fallback


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage standalone PostgreSQL for OpenCrew")
    parser.add_argument("command", choices=["start", "stop", "status"])
    parser.add_argument("--bin-dir", default=read_env("OPENCREW_PG_BIN_DIR", str(default_bin_dir())))
    parser.add_argument("--data-dir", default=read_env("OPENCREW_PG_DATA_DIR", str(DEFAULT_DATA_DIR)))
    parser.add_argument("--log-path", default=read_env("OPENCREW_PG_LOG_PATH", str(DEFAULT_LOG_PATH)))
    parser.add_argument("--host", default=read_env("OPENCREW_PG_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(read_env("OPENCREW_PG_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--user", default=read_env("OPENCREW_PG_USER", DEFAULT_USER))
    parser.add_argument("--password", default=read_env("OPENCREW_PG_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--database", default=read_env("OPENCREW_PG_DATABASE", DEFAULT_DATABASE))
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, env=env)


def require_binary(bin_dir: Path, name: str) -> Path:
    path = bin_dir / name
    if not path.exists():
        raise SystemExit(f"Missing PostgreSQL binary: {path}")
    return path


def build_runtime(args: argparse.Namespace) -> dict[str, object]:
    bin_dir = Path(args.bin_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    log_path = Path(args.log_path).expanduser().resolve()
    return {
        "bin_dir": bin_dir,
        "data_dir": data_dir,
        "log_path": log_path,
        "host": args.host,
        "port": int(args.port),
        "user": args.user,
        "password": args.password,
        "database": args.database,
        "initdb": require_binary(bin_dir, "initdb"),
        "pg_ctl": require_binary(bin_dir, "pg_ctl"),
        "postgres": require_binary(bin_dir, "postgres"),
    }


def init_db(runtime: dict[str, object]) -> None:
    data_dir = runtime["data_dir"]
    assert isinstance(data_dir, Path)
    if (data_dir / "PG_VERSION").exists():
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    password = runtime["password"]
    initdb = runtime["initdb"]
    user = runtime["user"]
    assert isinstance(password, str)
    assert isinstance(initdb, Path)
    assert isinstance(user, str)
    fd, temp_pwfile = tempfile.mkstemp(prefix="opencrew-pg-", text=True)
    pwfile = Path(temp_pwfile)
    os.close(fd)
    pwfile.write_text(password, encoding="utf-8")
    try:
        result = run([
            str(initdb),
            "-D",
            str(data_dir),
            "--username",
            user,
            "--pwfile",
            str(pwfile),
            "--auth=md5",
            "--encoding=UTF8",
            "--locale=C",
        ])
        if result.returncode != 0:
            raise SystemExit(result.stderr or result.stdout or "initdb failed")
    finally:
        pwfile.unlink(missing_ok=True)


def ensure_database(runtime: dict[str, object]) -> None:
    backend_python = WORKSPACE_ROOT / "backend" / ".venv" / "bin" / "python"
    if not backend_python.exists():
        raise SystemExit(f"Missing backend Python: {backend_python}")
    host = runtime["host"]
    port = runtime["port"]
    user = runtime["user"]
    password = runtime["password"]
    database = runtime["database"]
    assert isinstance(host, str)
    assert isinstance(port, int)
    assert isinstance(user, str)
    assert isinstance(password, str)
    assert isinstance(database, str)
    script = (
        "import psycopg\n"
        f"conn=psycopg.connect('postgresql://{user}:{password}@{host}:{port}/postgres')\n"
        "conn.autocommit=True\n"
        "cur=conn.cursor()\n"
        f"cur.execute(\"SELECT 1 FROM pg_database WHERE datname = '{database}'\")\n"
        "exists=cur.fetchone() is not None\n"
        f"cur.execute(\"CREATE DATABASE {database}\") if not exists else None\n"
        "cur.close()\n"
        "conn.close()\n"
    )
    result = run([str(backend_python), "-c", script])
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout or "failed ensuring opencrew database")


def wait_until_ready(host: str, port: int, timeout_seconds: int = 20) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if port_open(host, port):
            return
        time.sleep(0.5)
    raise SystemExit(f"PostgreSQL did not become ready on {host}:{port}")


def start(runtime: dict[str, object]) -> None:
    host = runtime["host"]
    port = runtime["port"]
    user = runtime["user"]
    database = runtime["database"]
    data_dir = runtime["data_dir"]
    log_path = runtime["log_path"]
    pg_ctl = runtime["pg_ctl"]
    assert isinstance(host, str)
    assert isinstance(port, int)
    assert isinstance(user, str)
    assert isinstance(database, str)
    assert isinstance(data_dir, Path)
    assert isinstance(log_path, Path)
    assert isinstance(pg_ctl, Path)
    if port_open(host, port):
        print(f"PostgreSQL already running on {host}:{port}")
        print_connection_hint(runtime)
        return
    init_db(runtime)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        str(pg_ctl),
        "-D",
        str(data_dir),
        "-l",
        str(log_path),
        "-o",
        f"-p {port}",
        "start",
    ])
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout or "pg_ctl start failed")
    wait_until_ready(host, port)
    ensure_database(runtime)
    print(f"OpenCrew PostgreSQL started on {host}:{port}")
    print(f"data_dir={data_dir}")
    print(f"log_path={log_path}")
    print_connection_hint(runtime)


def stop(runtime: dict[str, object]) -> None:
    data_dir = runtime["data_dir"]
    pg_ctl = runtime["pg_ctl"]
    host = runtime["host"]
    port = runtime["port"]
    assert isinstance(data_dir, Path)
    assert isinstance(pg_ctl, Path)
    assert isinstance(host, str)
    assert isinstance(port, int)
    if not (data_dir / "PG_VERSION").exists():
        print(f"No PostgreSQL data directory at {data_dir}")
        return
    if not port_open(host, port):
        print(f"No PostgreSQL server listening on {host}:{port}")
        return
    result = run([str(pg_ctl), "-D", str(data_dir), "stop"])
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout or "pg_ctl stop failed")
    print(f"OpenCrew PostgreSQL stopped on {host}:{port}")


def status(runtime: dict[str, object]) -> None:
    host = runtime["host"]
    port = runtime["port"]
    data_dir = runtime["data_dir"]
    log_path = runtime["log_path"]
    assert isinstance(host, str)
    assert isinstance(port, int)
    assert isinstance(data_dir, Path)
    assert isinstance(log_path, Path)
    initialized = (data_dir / "PG_VERSION").exists()
    listening = port_open(host, port)
    print(f"initialized={'yes' if initialized else 'no'}")
    print(f"listening={'yes' if listening else 'no'}")
    print(f"data_dir={data_dir}")
    print(f"log_path={log_path}")
    print_connection_hint(runtime)


def print_connection_hint(runtime: dict[str, object]) -> None:
    host = runtime["host"]
    port = runtime["port"]
    user = runtime["user"]
    password = runtime["password"]
    database = runtime["database"]
    assert isinstance(host, str)
    assert isinstance(port, int)
    assert isinstance(user, str)
    assert isinstance(password, str)
    assert isinstance(database, str)
    print(
        "DATABASE_URL="
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
    )


def main() -> None:
    args = parse_args()
    runtime = build_runtime(args)
    if args.command == "start":
        start(runtime)
        return
    if args.command == "stop":
        stop(runtime)
        return
    status(runtime)


if __name__ == "__main__":
    main()
