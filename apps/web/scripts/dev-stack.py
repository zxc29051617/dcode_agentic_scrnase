from __future__ import annotations

import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = WEB_DIR.parent.parent
GATEWAY_DIR = ROOT_DIR / "services" / "gateway"


def value(name: str, default: str) -> str:
    return os.environ.get(name, default)


def absolute_path(raw: str) -> str:
    path = Path(raw).expanduser()
    return str(path if path.is_absolute() else (WEB_DIR / path).resolve())


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def gateway_command() -> list[str]:
    uvicorn = GATEWAY_DIR / ".venv" / "bin" / "uvicorn"
    if uvicorn.is_file():
        return [str(uvicorn), "app.main:app"]

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("gateway environment is missing: create services/gateway/.venv or install uv")
    interpreter = os.environ.get("GATEWAY_PYTHON") or sys.executable
    return [
        uv,
        "run",
        "--python",
        interpreter,
        "--with-requirements",
        str(GATEWAY_DIR / "requirements.txt"),
        "uvicorn",
        "app.main:app",
    ]


def wait_for_gateway(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"gateway exited before becoming ready (status {process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"gateway did not become ready at {url}")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is required; activate the copilotkit-web environment first")

    gateway_host = value("GATEWAY_HOST", "127.0.0.1")
    gateway_port = int(value("GATEWAY_PORT", "8010"))
    web_host = value("WEB_HOST", "127.0.0.1")
    web_port = int(value("WEB_PORT", "3000"))
    runs_root = absolute_path(value("GATEWAY_RUNS_ROOT", str(ROOT_DIR / "fixtures" / "synthetic_runs")))
    gateway_url = value("GATEWAY_URL", f"http://{gateway_host}:{gateway_port}")

    for label, host, port in (("gateway", gateway_host, gateway_port), ("web", web_host, web_port)):
        if not port_is_free(host, port):
            raise RuntimeError(f"{label} port {host}:{port} is already in use; choose another port")

    gateway_env = os.environ.copy()
    gateway_env["GATEWAY_RUNS_ROOT"] = runs_root
    web_env = os.environ.copy()
    web_env["GATEWAY_URL"] = gateway_url
    web_env.setdefault("COPILOTKIT_TELEMETRY_DISABLED", "true")
    web_mode = os.environ.get("WEB_MODE", "dev")
    if web_mode not in {"dev", "start"}:
        raise RuntimeError("WEB_MODE must be dev or start")
    web_command = [npm, "run", web_mode, "--", "--hostname", web_host, "--port", str(web_port)]

    gateway = subprocess.Popen(
        gateway_command() + ["--host", gateway_host, "--port", str(gateway_port)],
        cwd=GATEWAY_DIR,
        env=gateway_env,
        start_new_session=True,
    )
    web: subprocess.Popen[bytes] | None = None
    try:
        wait_for_gateway(gateway, f"{gateway_url}/healthz")
        print(f"gateway ready: {gateway_url}", flush=True)
        web = subprocess.Popen(web_command, cwd=WEB_DIR, env=web_env, start_new_session=True)
        print(f"web readying: http://{web_host}:{web_port}", flush=True)
        while True:
            if gateway.poll() is not None:
                return gateway.returncode or 1
            if web.poll() is not None:
                return web.returncode or 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        if web is not None:
            stop_process(web)
        stop_process(gateway)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"dev-stack: {error}", file=sys.stderr)
        raise SystemExit(1)
