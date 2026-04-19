## Checkpoint 2

Selectors used:

- `button.tab-btn[onclick*="showTab('planning'"]` targets the Planning tab button, found in `ui/templates/index.html:773`.
- `#planning-tab` targets the Planning panel, found in `ui/templates/index.html:958`.
- `#planning-tab .edit-mode-btn` targets the edit-mode toggle, found in `ui/templates/index.html:981`.
- `body.edit-mode` targets the active edit-mode state toggled by `toggleEditMode()`, found in `ui/static/sop_planning.js:384` and styled in `ui/templates/index.html:118`.
- `#planBody td.editable-cell[data-tt="val"][data-lt="01. Demand forecast"][data-period]` targets editable demand forecast period cells, emitted by `renderPlanningTable()` in `ui/templates/index.html:4595`.
- `[contenteditable="true"]` marks the active editable cell, set by the planning table click handler in `ui/templates/index.html:4792-4799`.
- `.cell-increased` is the stable heatmap class for an upward edit, defined in `ui/templates/index.html:92` and `ui/templates/index.html:160`, applied by `updateTableFromResults()` at `ui/templates/index.html:4973`.
- `#editSummaryBar` targets the pending-edit summary bar, styled in `ui/templates/index.html:423-428` and shown/hidden by `updateEditBadge()` at `ui/templates/index.html:5155-5163`.
- `#editSummaryCount` targets the pending-edit count text, found in `ui/templates/index.html:965` and updated by `updateEditBadge()` at `ui/templates/index.html:5160-5163`.
- `#undoBtn` targets the undo action, found in `ui/templates/index.html:967`.

Exact edit mode sequence as executed:

1. `_drain_edits(browser_page.server["base_url"])` runs first in every test.
2. Reload the page to sync the UI with the drained server state.
3. Wait for `#busyOverlay` to have class `hidden`.
4. Click `button.tab-btn[onclick*="showTab('planning'"]`.
5. Wait for `#planning-tab` to be visible.
6. Click `#planning-tab .edit-mode-btn`.
7. Wait for `body` to have class `edit-mode`.
8. Click the first editable demand forecast cell with `data-raw < 999`.
9. Wait for `contenteditable="true"` on that cell.
10. Fill `999`.
11. Press Enter.
12. Wait for `/api/update_volume` and `networkidle`.

This matches the requested sequence, with one extra wait before opening the tab: the test waits for the busy overlay to be hidden after reload so the tab click is deterministic.

Heatmap class observed on the edited cell:

- `cell-increased`, stable CSS class.

Undo behavior:

- DOM patch, not a full reload. `undoEdit()` calls `/api/undo`, then `_applyVolumeChangeResult(data)`, which calls `updateTableFromResults(...)`.

Whether `_drain_edits` was needed:

- Yes. Each test starts with `_drain_edits`, and the first two tests intentionally leave one edit behind. The following test drains that edit before reloading the UI, so test order and failed prior attempts do not leak state into assertions.

Tests:

- `test_cell_edit_updates_value_and_marks_pending` — passed.
- `test_edited_cell_has_heatmap_color_class` — passed.
- `test_undo_reverts_last_edit` — passed.

Flakiness:

- One initial prep timeout occurred on the Planning tab click before the test waited for `#busyOverlay.hidden` after reload. Root cause: the page/session restore could still be settling. After adding the explicit busy-overlay wait, `pytest tests/browser/test_edits.py -v` and full `pytest -v` passed.

Existing tests:

- Full `pytest -v` passed with 24 tests total. The 21 existing tests from Segment 1 still pass.

Branch:

- `test/browser-edits`, commit SHA reported in chat after commit and push.

Stop-worthy findings:

- none
