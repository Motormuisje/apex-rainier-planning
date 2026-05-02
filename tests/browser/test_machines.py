import re

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect


# Selector inventory:
# - button.tab-btn[onclick*="showTab('capacity'"]: Machines tab button. The
#   tab id is still "capacity" for backwards compatibility.
# - #capacity-tab: visible Machines tab panel.
# - #oeeTableBody: OEE / availability machine table body rendered by
#   renderOeeTable().
# - #machEditModeBtn: machine edit-mode toggle.
# - td.mach-edit[data-field]: machine edit cells for oee, shift_hours, and
#   availability.
# - #machinesResetBtn: machine reset button calling resetMachineEdits().


def _reset_machines(base_url: str) -> None:
    response = requests.post(base_url + "/api/machines/reset", timeout=60)
    if response.status_code == 400 and response.json().get("message") == "No baseline available":
        return
    response.raise_for_status()


def _open_machines_tab(page):
    expect(page.locator("#busyOverlay")).to_have_class("hidden", timeout=60000)
    page.locator("button.tab-btn[onclick*=\"showTab('capacity'\"]").click()
    expect(page.locator("#capacity-tab")).to_be_visible()
    expect(page.locator("#oeeTableBody tr").first).to_be_visible(timeout=60000)


def _expand_first_machine_group(page):
    first_group = page.locator("#oeeTableBody tr.cursor-pointer").first
    expect(first_group).to_be_visible(timeout=60000)
    try:
        first_group.click(timeout=60000)
    except PlaywrightTimeoutError:
        page.evaluate(
            """() => {
                const row = document.querySelector('#oeeTableBody tr.cursor-pointer');
                const arrow = row ? row.querySelector('span[id$="-arrow"]') : null;
                if (arrow && typeof window.toggleOeeGroup === 'function') {
                    window.toggleOeeGroup(arrow.id.replace(/-arrow$/, ''));
                }
            }"""
        )
    first_machine = page.locator("#oeeTableBody tr[data-oee-grp]").first
    expect(first_machine).to_be_visible()
    return first_machine


def _enable_machine_edit_mode(page):
    page.locator("#machEditModeBtn").click()
    expect(page.locator("body")).to_have_class(re.compile(r".*\bmachine-edit-mode\b.*"))


def _editable_machine_cell(page, field: str):
    cell = page.locator(f'#oeeTableBody tr[data-oee-grp] td.mach-edit[data-field="{field}"]').first
    expect(cell).to_be_visible()
    return cell


def _machine_column_index(page, header_text: str) -> int:
    headers = [text.strip() for text in page.locator("#oeeTableHead th").all_text_contents()]
    for idx, text in enumerate(headers):
        if header_text in text:
            return idx
    raise AssertionError(f"Could not find machines table header containing {header_text!r}: {headers}")


def _machine_row_cell(page, row, header_text: str):
    return row.locator("td").nth(_machine_column_index(page, header_text))


def _edit_machine_cell(page, field: str, replacement: str):
    cell = _editable_machine_cell(page, field)
    cell.click()
    expect(cell).to_have_attribute("contenteditable", "true")
    cell.fill(replacement)
    with page.expect_response(lambda response: "/api/machines/update" in response.url and response.ok):
        cell.press("Enter")
    page.wait_for_load_state("networkidle")
    _expand_first_machine_group(page)
    return _editable_machine_cell(page, field)


def test_machine_reset_waits_for_active_cell_save_and_restores_value(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    cell = _editable_machine_cell(page, "availability")
    baseline_display = cell.get_attribute("data-current-display")
    baseline_edit_value = cell.get_attribute("data-edit-value")
    assert baseline_display
    assert baseline_edit_value
    replacement = "70.0" if abs(float(baseline_edit_value) - 70.0) > 0.01 else "80.0"

    cell.click()
    expect(cell).to_have_attribute("contenteditable", "true")
    cell.fill(replacement)

    page.once("dialog", lambda dialog: dialog.accept())
    request_count = 0

    def count_update_request(request):
        nonlocal request_count
        if "/api/machines/update" in request.url:
            request_count += 1

    page.on("request", count_update_request)
    with page.expect_response(lambda response: "/api/machines/reset" in response.url and response.ok):
        page.locator("#machinesResetBtn").click()
    page.wait_for_load_state("networkidle")

    _expand_first_machine_group(page)
    cell = _editable_machine_cell(page, "availability")
    expect(cell).to_have_attribute("data-current-display", baseline_display)
    expect(cell).to_have_attribute("data-edit-value", baseline_edit_value)
    expect(cell).not_to_have_class(re.compile(r".*\bcell-(increased|decreased|edited)\b.*"))
    expect(page.locator("#oeeTableBody tr.cursor-pointer").first).not_to_contain_text("bewerkt")
    assert request_count == 0


def test_machine_edit_refreshes_table_without_tab_switch(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    first_machine = _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    util_cell = _machine_row_cell(page, first_machine, "Util % avg")
    before_util = util_cell.inner_text().strip()
    oee_cell = _editable_machine_cell(page, "oee")
    original_oee = float(oee_cell.get_attribute("data-edit-value") or "0")
    replacement = f"{max(1.0, original_oee * 0.5):.1f}"

    oee_cell.click()
    expect(oee_cell).to_have_attribute("contenteditable", "true")
    oee_cell.fill(replacement)
    with page.expect_response(lambda response: "/api/machines/update" in response.url and response.ok):
        oee_cell.press("Enter")
    page.wait_for_load_state("networkidle")

    oee_cell = _editable_machine_cell(page, "oee")
    expect(oee_cell).not_to_have_class(re.compile(r".*\bopacity-50\b.*"), timeout=60000)
    expect(oee_cell).to_have_attribute("data-edit-value", replacement, timeout=60000)
    expect(oee_cell).to_have_class(re.compile(r".*\bcell-(increased|decreased|edited)\b.*"))
    expect(util_cell).not_to_contain_text(before_util, timeout=60000)


def test_machine_undo_redo_updates_values_and_button_depths(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    oee_cell = _editable_machine_cell(page, "oee")
    baseline_value = oee_cell.get_attribute("data-edit-value")
    assert baseline_value
    replacement = f"{max(1.0, float(baseline_value) * 0.5):.1f}"

    expect(page.locator("#machinesUndoBtn")).to_be_disabled()
    expect(page.locator("#machinesRedoBtn")).to_be_disabled()

    oee_cell = _edit_machine_cell(page, "oee", replacement)
    expect(oee_cell).to_have_attribute("data-edit-value", replacement)
    expect(page.locator("#machinesUndoBtn")).to_be_enabled()
    expect(page.locator("#machinesRedoBtn")).to_be_disabled()

    with page.expect_response(lambda response: "/api/machines/undo" in response.url and response.ok):
        page.locator("#machinesUndoBtn").click()
    page.wait_for_load_state("networkidle")
    _expand_first_machine_group(page)
    oee_cell = _editable_machine_cell(page, "oee")
    expect(oee_cell).to_have_attribute("data-edit-value", baseline_value)
    expect(page.locator("#machinesUndoBtn")).to_be_disabled()
    expect(page.locator("#machinesRedoBtn")).to_be_enabled()

    with page.expect_response(lambda response: "/api/machines/redo" in response.url and response.ok):
        page.locator("#machinesRedoBtn").click()
    page.wait_for_load_state("networkidle")
    _expand_first_machine_group(page)
    oee_cell = _editable_machine_cell(page, "oee")
    expect(oee_cell).to_have_attribute("data-edit-value", replacement)
    expect(page.locator("#machinesUndoBtn")).to_be_enabled()
    expect(page.locator("#machinesRedoBtn")).to_be_disabled()


def test_machine_save_inflight_guard_sends_one_update_request(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    oee_cell = _editable_machine_cell(page, "oee")
    original_oee = float(oee_cell.get_attribute("data-edit-value") or "0")
    replacement = f"{max(1.0, original_oee * 0.5):.1f}"
    request_count = 0

    def count_update_request(request):
        nonlocal request_count
        if "/api/machines/update" in request.url:
            request_count += 1

    page.on("request", count_update_request)
    oee_cell.click()
    oee_cell.fill(replacement)
    handle = oee_cell.element_handle()
    assert handle is not None
    page.evaluate(
        """async td => {
            await Promise.all([saveMachineEdit(td), saveMachineEdit(td)]);
        }""",
        handle,
    )
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(200)

    assert request_count == 1


def test_machine_escape_cancels_edit_without_saving(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    oee_cell = _editable_machine_cell(page, "oee")
    baseline_value = oee_cell.get_attribute("data-edit-value")
    assert baseline_value
    request_count = 0

    def count_update_request(request):
        nonlocal request_count
        if "/api/machines/update" in request.url:
            request_count += 1

    page.on("request", count_update_request)
    oee_cell.click()
    expect(oee_cell).to_have_attribute("contenteditable", "true")
    oee_cell.fill("50.0" if baseline_value != "50.0" else "60.0")
    oee_cell.press("Escape")
    page.wait_for_timeout(300)

    oee_cell = _editable_machine_cell(page, "oee")
    expect(oee_cell).to_have_attribute("data-edit-value", baseline_value)
    assert request_count == 0
