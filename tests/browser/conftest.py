import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests


_REPORT = {
    "startup_seconds": None,
    "console_errors": [],
    "periods_rendered": [],
    "periods_expected": [],
}


def pytest_terminal_summary(terminalreporter):
    if _REPORT["startup_seconds"] is None:
        return
    terminalreporter.write_line(
        f"BROWSER_SERVER_STARTUP_SECONDS={_REPORT['startup_seconds']:.2f}"
    )
    terminalreporter.write_line(
        "BROWSER_CONSOLE_ERRORS="
        + (" | ".join(_REPORT["console_errors"]) if _REPORT["console_errors"] else "none")
    )
    terminalreporter.write_line(
        "BROWSER_PERIODS_RENDERED=" + ",".join(_REPORT["periods_rendered"])
    )
    terminalreporter.write_line(
        "BROWSER_PERIODS_EXPECTED=" + ",".join(_REPORT["periods_expected"])
    )


@pytest.fixture(scope="session")
def golden_fixture_path() -> Path:
    fixture = os.environ.get("SOP_GOLDEN_FIXTURE")
    if not fixture:
        pytest.skip(
            "SOP_GOLDEN_FIXTURE env var not set. "
            "Point it at a local golden MS_RECONC .xlsm file."
        )
    path = Path(fixture)
    if not path.exists():
        pytest.skip(f"Golden fixture not found at {path}")
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _poll_home(base_url: str, process: subprocess.Popen, log_path: Path) -> float:
    started = time.monotonic()
    deadline = started + 30
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = log_path.read_text(encoding="utf-8", errors="replace")
            pytest.fail(f"Server exited before startup completed:\n{output}")
        try:
            response = requests.get(base_url + "/", timeout=1)
            if response.status_code == 200:
                return time.monotonic() - started
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.25)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    pytest.fail(f"Server did not return 200 within 30s: {last_error}\n{output}")


def _ensure_license(base_url: str) -> None:
    status = requests.get(base_url + "/api/license/status", timeout=10)
    status.raise_for_status()
    if status.json().get("status") == "ok":
        return
    activated = requests.post(base_url + "/api/license/activate", timeout=10)
    activated.raise_for_status()
    payload = activated.json()
    if not payload.get("success"):
        pytest.fail(f"Could not activate temporary test license: {payload}")


def _upload_and_calculate(base_url: str, golden_fixture_path: Path) -> dict:
    with golden_fixture_path.open("rb") as workbook:
        upload = requests.post(
            base_url + "/api/upload",
            files={"file": (golden_fixture_path.name, workbook)},
            data={
                "custom_name": "Browser load test",
                "planning_month": "2025-12",
                "months_actuals": "11",
                "months_forecast": "12",
            },
            timeout=120,
        )
    upload.raise_for_status()
    upload_payload = upload.json()
    if not upload_payload.get("success"):
        pytest.fail(f"Upload failed: {upload_payload}")

    calculate = requests.post(
        base_url + "/api/calculate",
        json={
            "planning_month": "2025-12",
            "months_actuals": 11,
            "months_forecast": 12,
        },
        timeout=180,
    )
    calculate.raise_for_status()
    calculate_payload = calculate.json()
    if not calculate_payload.get("success"):
        pytest.fail(f"Calculate failed: {calculate_payload}")

    return {
        "session_id": upload_payload["session_id"],
        "periods": calculate_payload["summary"]["period_list"],
    }


@pytest.fixture(scope="session")
def server(golden_fixture_path):
    app_data_dir = Path(tempfile.mkdtemp(prefix="sop-browser-app-data-"))
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = app_data_dir / "server.log"
    log_file = log_path.open("w", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "SOP_APP_DATA_DIR": str(app_data_dir),
            "SOP_HOST": "127.0.0.1",
            "SOP_PORT": str(port),
            "SOP_DISABLE_AUTORUN": "1",
            "SOP_NO_BROWSER": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )

    process = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        startup_seconds = _poll_home(base_url, process, log_path)
        if startup_seconds > 60:
            pytest.fail(f"Server startup exceeded 60s: {startup_seconds:.2f}s")
        _ensure_license(base_url)
        calculation = _upload_and_calculate(base_url, golden_fixture_path)
        _REPORT["startup_seconds"] = startup_seconds
        _REPORT["periods_expected"] = calculation["periods"]
        yield {
            "base_url": base_url,
            "session_id": calculation["session_id"],
            "startup_seconds": startup_seconds,
            "expected_periods": calculation["periods"],
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log_file.close()
        shutil.rmtree(app_data_dir, ignore_errors=True)


@pytest.fixture
def browser_report():
    return _REPORT


@pytest.fixture
def browser_page(page, server):
    console_errors = []
    js_errors = []

    def collect_console_error(message):
        if message.type == "error":
            console_errors.append(message.text)
            _REPORT["console_errors"].append(message.text)
            if not message.text.startswith("Failed to load resource:"):
                js_errors.append(message.text)

    page.on("console", collect_console_error)
    response = page.goto(server["base_url"], wait_until="networkidle")
    assert response is not None
    assert response.ok
    page.console_errors = console_errors
    page.js_errors = js_errors
    page.server = server
    return page
