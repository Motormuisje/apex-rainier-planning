from playwright.sync_api import expect


# Selector inventory:
# - #planTableScroll: stable planning table viewport in ui/templates/index.html.
#   ui/static/sop_planning.js also uses it as a walkthrough fallback target.
# - #planBody: stable planning tbody used by ui/static/sop_planning.js for
#   dependency highlighting and active edit lookups.
# - #planBody tr[data-material][data-linetype]: rows created by
#   renderPlanningTable in ui/templates/index.html; the same data attributes are
#   read by ui/static/sop_planning.js when resolving row/cell dependencies.
# - #planBody td[data-tt="val"][data-period]: period value cells created by
#   renderPlanningTable; ui/static/sop_planning.js reads data-period for
#   dependency highlighting and edit targeting.
# - #planHead th: stable planning header target from renderPlanningTable. Header
#   cells do not carry data-period attributes, so tests compare visible text.
# - #calcBtn: calculate button ID from ui/templates/index.html. Segment 1 uses
#   API pre-seeding in the server fixture, so the test does not click it.
# - #busyOverlay: app-level loading overlay declared in ui/templates/index.html.
#   It is controlled by setBusy() while calculate/load operations run.
# - button.tab-btn[onclick*="showTab('planning'"]: stable Planning tab button
#   from ui/templates/index.html; role/name matching also catches Values Planning.


def _open_planning_tab(page):
    page.locator("button.tab-btn[onclick*=\"showTab('planning'\"]").click()
    expect(page.locator("#planning-tab")).to_be_visible()


def _planning_rows(page):
    return page.locator("#planBody tr[data-material][data-linetype]")


def test_page_loads_without_errors(browser_page):
    page = browser_page

    expect(page).to_have_title("Apex Rainier Planning")
    expect(page.locator("#busyOverlay")).to_have_class("hidden")
    _open_planning_tab(page)
    expect(page.locator("#planTableScroll")).to_be_visible()
    expect(_planning_rows(page).nth(0)).to_be_visible(timeout=60000)

    assert page.js_errors == []


def test_calculate_renders_planning_table(browser_page):
    page = browser_page
    _open_planning_tab(page)
    rows = _planning_rows(page)
    demand_rows = page.locator('#planBody tr[data-linetype="01. Demand forecast"]')

    expect(rows.nth(0)).to_be_visible(timeout=60000)
    assert rows.count() > 0
    assert demand_rows.count() > 0
    expect(
        page.locator('#planBody td[data-tt="val"][data-period]').nth(0)
    ).to_be_visible()


def test_period_headers_match_planning_month(browser_page, browser_report):
    page = browser_page
    expected_periods = browser_page.server["expected_periods"]
    _open_planning_tab(page)

    expect(_planning_rows(page).nth(0)).to_be_visible(timeout=60000)
    header_texts = [text.strip() for text in page.locator("#planHead th").all_text_contents()]
    rendered_periods = header_texts[6:]

    browser_report["periods_rendered"] = rendered_periods
    browser_report["periods_expected"] = expected_periods

    assert rendered_periods == expected_periods
