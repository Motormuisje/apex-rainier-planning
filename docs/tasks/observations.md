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

---

## 2026-04-19 — Cascade extraction deferred

**Severity:** medium (architectural; no runtime impact)

Attempted to extract `_recalc_pap_material`, `_recalc_material_subtree`,
and `_finish_pap_recalc` from `ui/app.py` into `ui/cascade.py`. Stopped
during planning.

**Reason:** `_recalc_one_material` has 9 parameters and
`_recalc_material_subtree` has 6. A clean extraction requires more than
four parameters per function, which violates the `AGENTS.md` stop-rule for
mechanical extractions.

**Dependency facts uncovered:**

- `_recalc_one_material` is called by `_recalc_material_subtree`,
  `_recalc_pap_material`, and `_apply_volume_change`. It cannot move in
  isolation.
- `_finish_pap_recalc` reads `sessions` and `active_session_id` globals.
  Moving it would require pulling those into the new module or changing its
  signature to accept `sess` directly.

**Possible future approaches, roughly in order of scope:**

1. Defer until `_apply_volume_change` is refactored, so
   `_recalc_one_material` can move with its main caller instead of being
   split across modules.
2. Design a `CascadeContext` object that bundles dependencies
   (`_recalculate_capacity_and_values`, `_recalculate_value_results`,
   active session resolution) so each cascade function takes one context
   parameter instead of many.
3. Restructure cascade logic itself so that high parameter counts are not
   inherent to the functions.

No immediate action. Revisit after `_apply_volume_change` work or when
cascade design is reviewed holistically.
