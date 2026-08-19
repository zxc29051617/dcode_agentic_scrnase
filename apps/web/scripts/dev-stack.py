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
CONTROLLER_DIR = ROOT_DIR / "services" / "controller"


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


def wait_for_service(process: subprocess.Popen[bytes], url: str, label: str = "gateway") -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited before becoming ready (status {process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{label} did not become ready at {url}")


#: Kept as the old name so nothing outside this file has to change.
wait_for_gateway = wait_for_service


def controller_available() -> bool:
    """Is the write side installed?

    The controller is optional. Without it the stack is exactly what it was —
    a read-only site over a read-only gateway — and `/analysis/new` says so
    rather than rendering a form that cannot work.
    """
    return (CONTROLLER_DIR / ".venv" / "bin" / "uvicorn").is_file()


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

    # --- the optional write side ------------------------------------------
    # Started only when it is installed and not explicitly turned off, so the
    # existing read-only stack behaves exactly as it did. The controller's own
    # database goes beside the runs root, never inside it — the controller
    # refuses to start otherwise.
    controller_port = int(value("CONTROLLER_PORT", "8020"))
    controller_url = value("ANALYSIS_CONTROLLER_URL", f"http://{gateway_host}:{controller_port}")
    want_controller = (
        os.environ.get("CONTROLLER_ENABLED", "auto") != "false" and controller_available()
    )
    if want_controller and not port_is_free(gateway_host, controller_port):
        raise RuntimeError(
            f"controller port {gateway_host}:{controller_port} is already in use; "
            f"set CONTROLLER_PORT"
        )
    controller_env = os.environ.copy()
    if want_controller:
        controller_env["CONTROLLER_RUNS_ROOT"] = runs_root
        # Under `var/` at the repository root, not beside whatever `runs_root`
        # happens to be: derived from the runs root, this landed in a different
        # place for every `GATEWAY_RUNS_ROOT` anyone tried, so a request
        # confirmed against the fixtures could not be found again after
        # pointing the stack at the real `runs/`. One database, one location.
        controller_env.setdefault(
            "CONTROLLER_DB", str(ROOT_DIR / "var" / "controller" / "controller.sqlite")
        )
        controller_env.setdefault("CONTROLLER_DATA_ROOTS", str(ROOT_DIR / "data"))
        controller_env.setdefault("CONTROLLER_CATALOG", str(ROOT_DIR / "config" / "dataset_catalog.json"))
        Path(controller_env["CONTROLLER_DB"]).parent.mkdir(parents=True, exist_ok=True)
        web_env["ANALYSIS_CONTROLLER_URL"] = controller_url
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
    controller: subprocess.Popen[bytes] | None = None
    try:
        wait_for_service(gateway, f"{gateway_url}/healthz", "gateway")
        print(f"gateway ready: {gateway_url}", flush=True)

        if want_controller:
            controller = subprocess.Popen(
                [str(CONTROLLER_DIR / ".venv" / "bin" / "uvicorn"), "app.main:app",
                 "--host", gateway_host, "--port", str(controller_port)],
                cwd=CONTROLLER_DIR, env=controller_env, start_new_session=True,
            )
            wait_for_service(controller, f"{controller_url}/healthz", "controller")
            print(f"controller ready: {controller_url}", flush=True)
            # The worker is deliberately not started here. It runs in the
            # scientific environment, not this one, and starting it from a web
            # dev script would put a scanpy import inside the front end's
            # process tree. See services/controller/README.md.
            print("worker: start separately in dcode-scrna — "
                  "python -m services.controller.worker", flush=True)
        else:
            print("controller: not installed; the site will be read-only", flush=True)

        web = subprocess.Popen(web_command, cwd=WEB_DIR, env=web_env, start_new_session=True)
        print(f"web readying: http://{web_host}:{web_port}", flush=True)
        while True:
            if gateway.poll() is not None:
                return gateway.returncode or 1
            if controller is not None and controller.poll() is not None:
                return controller.returncode or 1
            if web.poll() is not None:
                return web.returncode or 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        if web is not None:
            stop_process(web)
        if controller is not None:
            stop_process(controller)
        stop_process(gateway)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"dev-stack: {error}", file=sys.stderr)
        raise SystemExit(1)
