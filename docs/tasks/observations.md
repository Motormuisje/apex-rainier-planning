"- ui/app.py has UTF-8 BOM at start of file. Works at runtime (Python accepts it), but breaks ast.parse. Fix with one command: (Get-Content ui/app.py -Raw -Encoding UTF8) | Set-Content ui/app.py -Encoding UTF8. Separate commit when convenient." | Add-Content docs/observations.md

# Observations

Running log of code observations noticed during refactors and reviews.
These are NOT tasks — they're signals worth preserving so future-you or
an agent can decide whether to investigate. Each entry: date, severity,
description, context where it was noticed.

---

## 2026-04-18 — UTF-8 BOM in `ui/app.py`

**Severity:** low (cosmetic; works at runtime)

`ui/app.py` starts with a UTF-8 BOM (U+FEFF). Python's runtime accepts
it, but `ast.parse` and some static analysers reject it. Likely introduced
by an agent or editor that saved the file as "UTF-8 with BOM" at some
point.

**Fix when convenient** (separate commit, not part of any refactor):
```powershell
(Get-Content ui/app.py -Raw -Encoding UTF8) | Set-Content ui/app.py -Encoding UTF8
```

Check other Python files too — a scan in this session found only
`ui/app.py`, but a new agent contribution could reintroduce it.

---

## 2026-04-18 — Mojibake in `ui/app.py` comments

**Severity:** cosmetic

Several comments contain `â€"` and `â€™` artefacts — double-encoded
em-dashes and apostrophes. Likely a side effect of the same editor round
that introduced the BOM. Will clean up when the BOM is fixed; the same
UTF-8 round-trip above should resolve the visible ones if the file is
opened in a BOM-aware editor first.

---

## 2026-04-18 — `pending_edits` has two writers

**Severity:** medium (no observed bug, but fragile)

`pending_edits` is written from two places:

1. **Server-side, inline in `_apply_volume_change`** (`ui/app.py`).
   Writes a key of the form `"{line_type}||{material_number}||{aux_column}||{period}"`
   with value `{'original': <baseline>, 'new_value': <value>}`.
2. **Client-driven via `/api/sessions/edits/persist`** in
   `ui/routes/edit_state.py`. Writes under the same schema, triggered
   by the frontend.

If the client-side persist call and the server-side inline write ever
construct the key differently (e.g., one trims `aux_column`, the other
doesn't; one stringifies `material_number`, the other leaves it as int),
duplicate or ghost entries can accumulate in `pending_edits`. Because
`pending_edits` is what `replay_pending_edits` consumes after a restart,
this would silently change restart behaviour.

**Investigation task for later:** verify both paths produce byte-identical
keys for the same logical edit. If they diverge, designate one as
canonical and have the other funnel through it.

---

## 2026-04-18 — `reset_machine_params` clears state rather than rebuilding it

**Severity:** low (current behaviour is correct; future-refactor hazard)

In `ui/routes/machines.py`, `reset_machine_params` sets
`sess['machine_overrides'] = {}` explicitly, whereas every other route
that mutates machine state assigns
`sess['machine_overrides'] = machine_overrides_from_engine(sess, current_engine)`.

This asymmetry is intentional right now — after reset there's nothing
left to derive — but it means a future refactor that "normalises" how
machine_overrides is kept in sync could easily replace the empty-dict
assignment with the `machine_overrides_from_engine` call, and subtly
break reset.

**If refactoring this area:** preserve the explicit empty assignment,
or document why the derive-from-engine path returns empty after reset.

---

## 2026-04-18 — `_recalc_pap_material` and `_recalc_material_subtree` live in `ui/app.py`

**Severity:** low (style)

These helpers contain real recalculation logic (not thin wrappers) and
live in `ui/app.py` rather than a sibling module. They would fit
`ui/engine_rebuild.py` or a new `ui/cascade.py`. Moving them would
reduce `ui/app.py` further and make the cascade logic independently
testable.

**Out of scope for current refactor;** revisit once state-model tests
are in place to verify behaviour preservation.

"2026-04-19 — _replay_pending_edits blijft als wrapper. Andere helpers zijn geëxtraheerd omdat ze thin wrappers waren met onthulbare afhankelijkheden. _replay_pending_edits injecteert drie callbacks en is daarmee een factory callback, geen indirection voor een global. Verwijderen zou cosmetisch zijn. Kan mee in een toekomstige bredere herstructurering die factory-params voor alle blueprint injecties invoert, niet nu."

## 2026-04-19 — Ruff pre-commit hook uitgesteld

Severity: low (tooling gap, geen runtime impact)

Eerste ruff-run (F-only ruleset) rapporteerde 40 errors over 13 bestanden.
Errors zijn een mix van unused imports en redefined names die deels
false positives zijn in de context van Flask blueprints en side-effect
imports. Ruff toevoegen vereist eerst een bewuste clean-up pass, niet
als bijproduct van een hook-installatie. Pre-commit hook draait
voorlopig alleen pytest. Ruff toevoegen als apart chore-ticket wanneer
er bandbreedte is.

---

## 2026-04-19 — QA Layer 1 sprint afgerond

Severity: low (QA infrastructure; no runtime impact)

QA Layer 1 now has three pieces of infrastructure: a local pytest
pre-commit hook, GitHub Actions CI for the fixture-free `no_fixture` test
subset, and coverage measurement with a recorded baseline.

The current coverage baseline is 48% overall: `modules` is stronger at 59%
because the golden pipeline exercises the core engines, while `ui` is 36%
because Flask route behavior is mostly outside Layer 1. The expected next
QA gaps are Layer 2 Flask route tests, targeted tests for route error paths,
and a dedicated Ruff cleanup pass before Ruff is reintroduced into
pre-commit or CI.

---

## 2026-04-19 — QA Layer 2 segment 1 workflow route findings

Severity: low (test coverage note; no runtime impact)

Workflow route tests cover the current `/api/upload` and `/api/calculate`
HTTP behavior. The Layer 2 sprint spec mentioned `/api/status`, but the
current workflow blueprint does not define that route.

The current upload route creates a session with metadata and marks it active,
but it does not attach an engine or run the full planning pipeline. The
pipeline is run by `/api/calculate`. Tests document this current split rather
than changing production behavior.

---

## 2026-04-19 — QA Layer 2 segment 2 edit route boundaries

Severity: low (test coverage note; no runtime impact)

Edit route tests cover HTTP orchestration across three blueprints:
`/api/update_volume`, `/api/machines/reset`, and
`/api/sessions/edits/persist`.

The `/api/update_volume` test intentionally verifies request-body extraction,
callback invocation, response propagation, and autosave integration only. It
does not assert `pending_edits` key schema, undo-stack format, or cascade
correctness because the real `_apply_volume_change` callback still lives in
`ui/app.py` and cannot be imported without initializing app-level globals.
Those behavior details remain covered by the state-model tests until
`_apply_volume_change` is extracted into a standalone helper module.
