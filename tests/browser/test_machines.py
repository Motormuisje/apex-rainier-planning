import re

import requests
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
    first_group.click()
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
    with page.expect_response(lambda response: "/api/machines/update" in response.url and response.ok):
        with page.expect_response(lambda response: "/api/machines/reset" in response.url and response.ok):
            page.locator("#machinesResetBtn").click()
    page.wait_for_load_state("networkidle")

    _expand_first_machine_group(page)
    cell = _editable_machine_cell(page, "availability")
    expect(cell).to_have_attribute("data-current-display", baseline_display)
    expect(cell).to_have_attribute("data-edit-value", baseline_edit_value)
    expect(cell).not_to_have_class(re.compile(r".*\bcell-(increased|decreased|edited)\b.*"))
    expect(page.locator("#oeeTableBody tr.cursor-pointer").first).not_to_contain_text("bewerkt")


def test_machine_edit_refreshes_table_without_tab_switch(browser_page):
    base_url = browser_page.server["base_url"]
    _reset_machines(base_url)

    page = browser_page
    page.reload(wait_until="networkidle")
    _open_machines_tab(page)
    first_machine = _expand_first_machine_group(page)
    _enable_machine_edit_mode(page)

    util_cell = first_machine.locator("td").nth(5)
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
    expect(oee_cell).to_have_attribute("data-edit-value", replacement)
    expect(oee_cell).to_have_class(re.compile(r".*\bcell-(increased|decreased|edited)\b.*"))
    expect(util_cell).not_to_contain_text(before_util)
