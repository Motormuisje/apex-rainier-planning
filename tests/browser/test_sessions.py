import pytest
import requests
from playwright.sync_api import expect


# Selector inventory:
# - #sessionList: session list container in ui/templates/index.html:744.
#   Populated by renderSessionList() called from loadSessions() at
#   ui/templates/index.html:7903.
# - .session-item: each session entry in ui/templates/index.html:501,508.
#   Gets class "active" when the session is the current active one (line 508).
# - .session-name-edit: editable name span inside each .session-item at
#   ui/templates/index.html:515,7949. data-session-id holds the session ID;
#   text content is the session's custom_name or filename.
# - .session-badge.calculated: "Ready" badge on calculated sessions at
#   ui/templates/index.html:509-510.
# - .session-delete: delete button inside each .session-item at
#   ui/templates/index.html:512-514,7961. Triggers deleteSession() which calls
#   window.confirm() before DELETE /api/sessions/<id>.
# - #planningMonth: toolbar input updated by switchSession() at
#   ui/templates/index.html:7992-7993 to reflect the switched-to session's
#   planning month. Used here to distinguish session A (2025-12) from session B
#   (2026-01) after a switch.
# - button.tab-btn[onclick*="showTab('planning'"]: Planning tab button in
#   ui/templates/index.html:773. Needed before asserting planning rows are
#   visible because inactive tab content has class "hidden".
# - #planBody tr[data-material][data-linetype]: planning table rows, present only
#   after a calculated session has loaded. Used to confirm initial page load
#   completed the auto-switch before the test clicks a second session.
# - #busyOverlay: loading overlay; hidden with class "hidden" when idle.

ORIGINAL_SESSION_NAME = "Browser load test"
SECOND_SESSION_NAME = "Browser sessions test"
SECOND_PLANNING_MONTH = "2026-01"
THROWAWAY_SESSION_NAME = "Throwaway delete test"
THROWAWAY_PLANNING_MONTH = "2024-01"


def _switch_to_session_via_api(base_url: str, session_id: str) -> None:
    r = requests.post(
        base_url + "/api/sessions/switch",
        json={"session_id": session_id},
        timeout=10,
    )
    r.raise_for_status()
    assert r.json().get("success"), f"API switch failed: {r.json()}"


def _upload_session(base_url: str, golden_fixture_path, custom_name: str, planning_month: str) -> str:
    with golden_fixture_path.open("rb") as workbook:
        upload = requests.post(
            base_url + "/api/upload",
            files={"file": (golden_fixture_path.name, workbook)},
            data={
                "custom_name": custom_name,
                "planning_month": planning_month,
                "months_actuals": "11",
                "months_forecast": "12",
            },
            timeout=120,
        )
    upload.raise_for_status()
    payload = upload.json()
    assert payload.get("success"), f"Upload failed: {payload}"
    return payload["session_id"]


def _open_planning_tab(page) -> None:
    page.locator("button.tab-btn[onclick*=\"showTab('planning'\"]").click()
    expect(page.locator("#planning-tab")).to_be_visible()


@pytest.fixture(scope="session")
def second_session(server, golden_fixture_path):
    """Pre-calculate a second session with a different planning month."""
    base_url = server["base_url"]
    session_id = _upload_session(base_url, golden_fixture_path, SECOND_SESSION_NAME, SECOND_PLANNING_MONTH)

    calculate = requests.post(
        base_url + "/api/calculate",
        json={
            "planning_month": SECOND_PLANNING_MONTH,
            "months_actuals": 11,
            "months_forecast": 12,
        },
        timeout=180,
    )
    calculate.raise_for_status()
    calc_payload = calculate.json()
    assert calc_payload.get("success"), f"Calculate failed: {calc_payload}"

    yield {
        "session_id": session_id,
        "custom_name": SECOND_SESSION_NAME,
        "planning_month": SECOND_PLANNING_MONTH,
    }


def test_session_list_shows_uploaded_session(browser_page):
    base_url = browser_page.server["base_url"]
    original_session_id = browser_page.server["session_id"]

    _switch_to_session_via_api(base_url, original_session_id)
    page = browser_page
    page.reload(wait_until="networkidle")

    expect(page.locator(".session-item").first).to_be_visible(timeout=60000)

    active_item = page.locator(".session-item.active")
    expect(active_item).to_be_visible()
    expect(
        active_item.locator(".session-name-edit", has_text=ORIGINAL_SESSION_NAME)
    ).to_be_visible()
    expect(active_item.locator(".session-badge.calculated")).to_be_visible()


def test_switch_session_updates_table(browser_page, second_session):
    base_url = browser_page.server["base_url"]
    original_session_id = browser_page.server["session_id"]

    _switch_to_session_via_api(base_url, original_session_id)
    page = browser_page
    page.reload(wait_until="networkidle")
    _open_planning_tab(page)

    expect(
        page.locator("#planBody tr[data-material][data-linetype]").nth(0)
    ).to_be_visible(timeout=60000)
    expect(page.locator("#busyOverlay")).to_have_class("hidden", timeout=60000)

    session_b_item = page.locator(".session-item").filter(
        has=page.locator(".session-name-edit", has_text=second_session["custom_name"])
    )
    expect(session_b_item).to_be_visible()
    with page.expect_response(lambda response: "/api/sessions/switch" in response.url and response.ok) as switch_response:
        session_b_item.locator(".session-badge.calculated").click()
    switch_payload = switch_response.value.json()
    assert switch_payload.get("active_session_id") == second_session["session_id"]

    page.wait_for_load_state("networkidle")
    expect(page.locator("#busyOverlay")).to_have_class("hidden", timeout=60000)
    expect(page.locator("#planningMonth")).to_have_value(SECOND_PLANNING_MONTH, timeout=60000)

    expect(
        page.locator(".session-item.active").locator(
            ".session-name-edit", has_text=second_session["custom_name"]
        )
    ).to_be_visible(timeout=60000)


def test_delete_session_removes_from_sidebar(browser_page, golden_fixture_path):
    base_url = browser_page.server["base_url"]

    _upload_session(base_url, golden_fixture_path, THROWAWAY_SESSION_NAME, THROWAWAY_PLANNING_MONTH)

    page = browser_page
    page.reload(wait_until="networkidle")

    throwaway_item = page.locator(".session-item").filter(
        has=page.locator(".session-name-edit", has_text=THROWAWAY_SESSION_NAME)
    )
    expect(throwaway_item).to_be_visible(timeout=60000)

    page.once("dialog", lambda d: d.accept())
    throwaway_item.locator(".session-delete").click()

    page.wait_for_load_state("networkidle")

    expect(
        page.locator(".session-item").filter(
            has=page.locator(".session-name-edit", has_text=THROWAWAY_SESSION_NAME)
        )
    ).to_have_count(0)
