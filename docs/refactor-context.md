# UI Refactor Context

Deze notitie hoort bij de stapsgewijze opsplitsing van `ui/app.py` naar
kleinere route-blueprints en helpermodules. Doel: leesbaarheid verbeteren
zonder rekenlogica, contracts of sessiegedrag te veranderen.

Lees bij codewijzigingen ook `AGENTS.md`. Vooral de zes sync/rebuild-punten
blijven leidend voor alles wat sessie-, config- of engine-state raakt.

## Huidige routegroepen

Alle Flask routefuncties zijn uit `ui/app.py` verplaatst naar blueprints:
- Top-level workflow/upload/calculate routes: `ui/routes/workflow.py`
- License routes: `ui/routes/license.py`
- Config/folder routes: `ui/routes/config.py`
- Read-only result routes: `ui/routes/read.py`
- Machine routes: `ui/routes/machines.py`
- PAP routes: `ui/routes/pap.py`
- Session routes: `ui/routes/sessions.py`
- Scenario routes: `ui/routes/scenarios.py`
- Export/MoM routes: `ui/routes/exports.py`
- Edit metadata/pending-edit routes: `ui/routes/edit_state.py`
- Edit/cascade routes: `ui/routes/edits.py`

`ui/app.py` blijft bewust de composition root:
- Flask app aanmaken en blueprints registreren.
- Module-globals beheren: `sessions`, `active_session_id`, `_global_config`.
- Folder/config/session store bootstrap.
- Engine rebuild/replay/snapshot wrappers.
- Edit cascade helpers zoals `_apply_volume_change`, `_recalc_one_material`,
  `_recalc_material_subtree`, `_recalculate_capacity_and_values`.
- Export highlighting helper `_apply_edit_highlights`.
- Auto-save `after_request` hook.

Belangrijk: de routefiles mogen de cascade helpers aanroepen via dependency
injection, maar mogen de numerieke cascade niet stil herschrijven. De routefiles
zijn bedoeld als request/response-laag.

## Sessions

Routes:
- `POST /api/sessions/snapshot`
- `GET /api/sessions`
- `POST /api/sessions/rename`
- `POST /api/sessions/switch`
- `DELETE /api/sessions/<session_id>`

State die geraakt wordt:
- `sessions`
- `active_session_id`
- `_global_config`
- per-session `engine`
- `reset_baseline`
- `pending_edits`
- `machine_overrides`
- `value_aux_overrides`
- `valuation_params`
- `parameters`

Risico's:
- Verkeerde active session na switch/delete.
- `_global_config` toont waarden van vorige instance.
- Rebuild na restart mist sessie-specifieke config.
- Pending edits worden niet opnieuw gereplayed.
- Reset baseline hoort bij de verkeerde engine.

Helpers/contracten om te behouden:
- `_sync_global_config_from_engine`
- `_get_session_config_overrides`
- `_build_clean_engine_for_session`
- `_install_clean_engine_baseline`
- `_replay_pending_edits`
- `_save_sessions_to_disk`
- `_load_sessions_from_disk`
- `_ensure_reset_baseline`

Minimale test:
- Route-map bevat dezelfde session endpoints.
- Nieuwe session maken/snapshotten.
- Session switchen en terug switchen.
- Rename en delete.
- `python main.py --test`.

## Scenarios

Routes:
- `GET /api/scenarios`
- `POST /api/scenarios/save`
- `POST /api/scenarios/load`
- `DELETE /api/scenarios/<scenario_id>`
- `POST /api/scenarios/compare`
- `GET /api/scenarios/compare/export`

State die geraakt wordt:
- `scenarios`
- `sessions[active_session_id]`
- `engine.results`
- `engine.value_results`
- `pending_edits`
- `value_aux_overrides`
- `machine_overrides`
- `purchased_and_produced`

Risico's:
- Scenario load herstelt tabellen maar niet alle afgeleide grafiekdata.
- Pending edits snapshot wijkt af van live/replaygedrag.
- PAP of machine overrides komen wel in scenario, maar niet in rebuild terecht.
- Compare/export gebruikt oude of verkeerde scenarioselectie.

Helpers/contracten om te behouden:
- `_snapshot_engine_state`
- `restore_engine_state`
- `_build_pending_edits_from_results_snapshot`
- `_planning_value_payload`
- `_moq_warnings_payload`
- `_parse_purchased_and_produced`
- `_format_purchased_and_produced`

Minimale test:
- Scenario opslaan, laden en verwijderen.
- Scenario vergelijken.
- Scenario compare export oproepen.
- Na scenario load controleren dat table, dashboard en value results overeenkomen.
- `python main.py --test`.

## Edits

Routes:
- `GET /api/editable_line_types`
- `POST /api/sessions/edits/persist`
- `POST /api/sessions/edits/sync`
- `POST /api/update_volume`
- `POST /api/update_value_aux`
- `POST /api/reset_value_planning_edits`
- `POST /api/undo`
- `POST /api/redo`
- `GET /api/edits/export`
- `POST /api/edits/import`
- `POST /api/reset_edits`

State die geraakt wordt:
- `engine.results`
- `engine.value_results`
- `pending_edits`
- `value_aux_overrides`
- `reset_baseline`
- `undo_stack`
- `redo_stack`
- machine/capacity/value downstream results

Risico's:
- Live edit en replay edit geven niet exact hetzelfde resultaat.
- Line 01/06 edit cascade mist dependent demand, inventory of capacity.
- Undo/redo past table aan maar niet value results of grafieken.
- Import/export van edits wijzigt order of oorspronkelijke baseline.
- Reset lijkt te werken maar laat een nieuw stateveld staan.

Helpers/contracten om te behouden:
- `_apply_volume_change`
- `_recalc_one_material`
- `_recalculate_value_results`
- `_recalculate_capacity_and_values`
- `_replay_pending_edits`
- `_ensure_reset_baseline`
- `_planning_value_payload`
- `_value_results_payload`

Minimale test:
- Edit Line 01 en controleer downstream volume/capacity/value.
- Edit Line 06 en controleer inventory/capacity/value.
- Undo, redo, reset.
- Export edits en import edits.
- Restart/rebuild pad met pending edits.
- `python main.py --test`.

## Exports And MoM

Routes:
- `GET /api/export`
- `POST /api/export_db`
- `GET /api/mom`

State die geraakt wordt:
- `engine.results`
- `engine.value_results`
- `_cycle_manager`
- `APP_EXPORTS_DIR`
- optional DB export payload

Risico's:
- Runtime bestanden worden in repo geschreven in plaats van app-data.
- Export gebruikt oude active session.
- MoM snapshot of compare gebruikt verkeerde cycle folder.
- Export mist value planning of consolidation rows.

Helpers/contracten om te behouden:
- `PlanningEngine.to_excel_with_values`
- `DatabaseExporter`
- `MoMComparisonEngine`
- `_cycle_manager`
- `_json_safe`

Minimale test:
- Planning export downloaden.
- DB export uitvoeren.
- MoM endpoint oproepen.
- Controleren dat output in de juiste exportmap komt.
- `python main.py --test`.

## Start, Upload And Calculate

Routes:
- `GET /`
- `POST /api/upload`
- `POST /api/calculate`

State die geraakt wordt:
- `sessions`
- `active_session_id`
- `_global_config`
- uploaded workbook path
- per-session engine
- reset baseline
- runtime folders

Risico's:
- Nieuwe upload overschrijft verkeerde session.
- Calculate bouwt engine met globale config in plaats van sessieconfig.
- Foutclassificatie voor upload/calculate wordt minder duidelijk.
- Initial baseline wordt te vroeg of te laat genomen.

Helpers/contracten om te behouden:
- `_classify_upload_exception`
- `_get_session_config_overrides`
- `_sync_global_config_from_engine`
- `_ensure_reset_baseline`
- `_save_sessions_to_disk`
- `_moq_warnings_payload`

Minimale test:
- Web app opent.
- Bestand uploaden.
- Calculate uitvoeren.
- Nieuwe session verschijnt en is actief.
- `python main.py --test`.

## Refactor Checklist Per Routegroep

Voor elke nieuwe routewijziging:
- Verplaats routefuncties naar `ui/routes/<groep>.py`.
- Injecteer dependencies via `create_<groep>_blueprint(...)`.
- Laat numerieke/cascade helpers staan tot een aparte, bewuste refactor.
- Behoud endpoint paths, methods en response-shapes.
- Controleer route-map.
- Run `python -m py_compile` op gewijzigde modules.
- Run `python main.py --test`.
- Commit als kleine aparte stap.

Bij nieuwe of gewijzigde state expliciet beantwoorden:
- Wordt het gesnapshotted in reset baseline?
- Wordt het gekopieerd naar `_global_config` bij session switch?
- Wordt het toegepast bij rebuild?
- Wordt het gereplayed of opnieuw berekend na rebuild?
- Triggert het de juiste downstream recalculatie?
- Wordt het geserialized en opnieuw geladen?

## Huidige Blueprint Map

Deze endpoints horen bij de routefiles:

- `ui/routes/workflow.py`
  - `GET /`
  - `POST /api/upload`
  - `POST /api/calculate`
- `ui/routes/config.py`
  - `GET/POST /api/config/folders`
  - `GET /api/config`
  - `POST /api/config/master-file`
  - `POST /api/config/settings`
  - `POST /api/config/reset_vp_params`
- `ui/routes/read.py`
  - `GET /api/results`
  - `GET /api/value_results`
  - `GET /api/dashboard`
  - `GET /api/capacity`
  - `GET /api/inventory`
  - `GET /api/inventory_quality`
- `ui/routes/machines.py`
  - `GET /api/machines`
  - `POST /api/machines/update`
  - `POST /api/machines/undo`
  - `POST /api/machines/reset`
  - `POST /api/machines/redo`
- `ui/routes/pap.py`
  - `GET/POST /api/pap`
  - `DELETE /api/pap/<material_number>`
- `ui/routes/sessions.py`
  - `GET /api/sessions`
  - `POST /api/sessions/snapshot`
  - `POST /api/sessions/rename`
  - `POST /api/sessions/switch`
  - `DELETE /api/sessions/<session_id>`
- `ui/routes/scenarios.py`
  - `GET /api/scenarios`
  - `POST /api/scenarios/save`
  - `POST /api/scenarios/load`
  - `DELETE /api/scenarios/<scenario_id>`
  - `POST /api/scenarios/compare`
  - `GET /api/scenarios/compare/export`
- `ui/routes/exports.py`
  - `GET /api/export`
  - `POST /api/export_db`
  - `GET /api/mom`
- `ui/routes/edit_state.py`
  - `GET /api/editable_line_types`
  - `POST /api/sessions/edits/persist`
  - `POST /api/sessions/edits/sync`
- `ui/routes/edits.py`
  - `POST /api/update_volume`
  - `POST /api/update_value_aux`
  - `POST /api/reset_value_planning_edits`
  - `POST /api/undo`
  - `POST /api/redo`
  - `GET /api/edits/export`
  - `POST /api/edits/import`
  - `POST /api/reset_edits`
