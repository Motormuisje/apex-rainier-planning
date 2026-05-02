import re

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect


# Selector inventory:
# - button.tab-btn[onclick*="showTab('planning'"]: Planning tab button in
#   ui/templates/index.html:773. Segment 1 found role/name also matches
#   "Values Planning", so the onclick selector is used.
# - #planning-tab: Planning tab panel in ui/templates/index.html:958.
# - #planning-tab .edit-mode-btn: edit-mode toggle button in
#   ui/templates/index.html:981. toggleEditMode() updates every .edit-mode-btn.
# - body.edit-mode: state class toggled by toggleEditMode() in
#   ui/static/sop_planning.js:384 and used by CSS in ui/templates/index.html:118.
# - #planBody td.editable-cell[data-tt="val"][data-lt="01. Demand forecast"][data-period]:
#   editable demand forecast value cells emitted by renderPlanningTable() in
#   ui/templates/index.html:4595.
# - [contenteditable="true"]: set on clicked editable cells by the #planBody
#   click handler in ui/templates/index.html:4792-4799.
# - .cell-increased / .cell-decreased / .cell-edited: stable heatmap classes in
#   ui/templates/index.html:92-93 and 159-161; updateTableFromResults() applies
#   .cell-increased / .cell-decreased after successful edits at
#   ui/templates/index.html:4973 and 4978.
# - #editSummaryBar: pending-edit summary container styled in
#   ui/templates/index.html:423-428 and shown/hidden by updateEditBadge() at
#   ui/templates/index.html:5155-5163.
# - #editSummaryCount: pending-edit text button in ui/templates/index.html:965,
#   updated by updateEditBadge() at ui/templates/index.html:5160-5163.
# - #undoBtn: undo button in ui/templates/index.html:967; it calls undoEdit().
#   It is not disabled in markup and is hidden with #editSummaryBar until edits exist.


CELL_SELECTOR = (
    '#planBody td.editable-cell[data-tt="val"]'
    '[data-lt="01. Demand forecast"][data-period]'
)

_DRAIN_COUNTS = []
_HEATMAP_CLASSES = []


def pytest_terminal_summary(terminalreporter):
    if _DRAIN_COUNTS:
        terminalreporter.write_line(
            "BROWSER_EDIT_DRAIN_COUNTS=" + ",".join(str(count) for count in _DRAIN_COUNTS)
        )
    if _HEATMAP_CLASSES:
        terminalreporter.write_line("BROWSER_EDIT_HEATMAP_CLASSES=" + ",".join(_HEATMAP_CLASSES))


def _drain_edits(base_url: str, max_iterations: int = 50) -> None:
    drained = 0
    for _ in range(max_iterations):
        r = requests.post(base_url + "/api/undo", timeout=10)
        if not r.ok or not r.json().get("success"):
            break
        drained += 1
    _DRAIN_COUNTS.append(drained)


def _open_planning_tab(page):
    expect(page.locator("#busyOverlay")).to_have_class("hidden", timeout=60000)
    tab = page.locator("button.tab-btn[onclick*=\"showTab('planning'\"]")
    try:
        tab.click(timeout=60000)
    except PlaywrightTimeoutError:
        page.evaluate(
            """() => {
                const btn = document.querySelector("button.tab-btn[onclick*=\\"showTab('planning'\\"]");
                window.showTab('planning', btn);
            }"""
        )
    expect(page.locator("#planning-tab")).to_be_visible()


def _enable_edit_mode(page):
    page.locator("#planning-tab .edit-mode-btn").click()
    expect(page.locator("body")).to_have_class(re.compile(r".*edit-mode.*"))


def _first_demand_cell_below(page, threshold: float = 999.0):
    cells = page.locator(CELL_SELECTOR)
    expect(cells.first).to_be_visible(timeout=60000)
    for idx in range(cells.count()):
        cell = cells.nth(idx)
        raw = float(cell.get_attribute("data-raw") or "0")
        if raw < threshold:
            return cell, raw
    raise AssertionError(f"No editable demand forecast cell below {threshold}")


def _prepare_clean_planning_page(page, base_url):
    page.reload(wait_until="networkidle")
    _open_planning_tab(page)
    _enable_edit_mode(page)


def _edit_first_demand_cell_to(page, new_value: str):
    cell, original_raw = _first_demand_cell_below(page)
    original_text = cell.inner_text().strip()
    cell.click()
    expect(cell).to_have_attribute("contenteditable", "true")
    cell.fill(new_value)
    with page.expect_response(lambda response: "/api/update_volume" in response.url and response.ok):
        cell.press("Enter")
    page.wait_for_load_state("networkidle")
    return cell, original_raw, original_text


def test_cell_edit_updates_value_and_marks_pending(browser_page):
    _drain_edits(browser_page.server["base_url"])
    page = browser_page
    _prepare_clean_planning_page(page, browser_page.server["base_url"])

    cell, _, _ = _edit_first_demand_cell_to(page, "999")

    expect(cell).to_contain_text("999")
    expect(page.locator("#editSummaryBar")).to_be_visible()
    expect(page.locator("#editSummaryCount")).to_contain_text("1")


def test_edited_cell_has_heatmap_color_class(browser_page):
    _drain_edits(browser_page.server["base_url"])
    page = browser_page
    _prepare_clean_planning_page(page, browser_page.server["base_url"])

    cell, _, _ = _edit_first_demand_cell_to(page, "999")

    expect(cell).to_have_class(re.compile(r".*\bcell-increased\b.*"))
    _HEATMAP_CLASSES.append("cell-increased")


def test_undo_reverts_last_edit(browser_page):
    _drain_edits(browser_page.server["base_url"])
    page = browser_page
    _prepare_clean_planning_page(page, browser_page.server["base_url"])

    cell, _, original_text = _edit_first_demand_cell_to(page, "999")
    expect(page.locator("#editSummaryBar")).to_be_visible()

    with page.expect_response(lambda response: "/api/undo" in response.url and response.ok):
        page.locator("#undoBtn").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#editSummaryBar")).not_to_be_visible()
    expect(cell).to_contain_text(original_text)
