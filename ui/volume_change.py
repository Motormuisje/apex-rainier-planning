"""Volume edit cascade helpers for the Flask UI."""

from flask import jsonify

from modules.models import LineType
from ui.pending_edits import pending_edit_key
from ui.replay import recalculate_value_results
from ui.state_snapshot import ensure_reset_baseline


# Line types that users are permitted to edit directly.
# Computed lines (03, 04, 07-12) are intentionally excluded.
EDITABLE_LINE_TYPES = {
    '01. Demand forecast',
    '05. Minimum target stock',
    '06. Production plan',
    '06. Purchase receipt',
}

# Value-planning rows whose Aux Column acts as the editable financial factor.
VALUE_AUX_EDITABLE_LINE_TYPES = {
    '01. Demand forecast',
    '03. Total demand',
    '04. Inventory',
    '06. Purchase receipt',
    '07. Capacity utilization',
    '12. FTE requirements',
}

def SHIFT_HOURS_LOOKUP_FALLBACK(machine, data):
    """Resolve shift hours for a machine, tolerating minor API drift."""
    if machine is None:
        return 520.0
    sho = getattr(machine, 'shift_hours_override', None)
    if sho is not None:
        return float(sho)
    from modules.models import SHIFT_HOURS, ShiftSystem
    try:
        key = machine.shift_system.value if hasattr(machine.shift_system, 'value') else machine.shift_system
        # shift_hours dict in data_loader uses human-readable keys ('3-shift system' etc.)
        if isinstance(key, str) and key in data.shift_hours:
            return data.shift_hours[key]
    except Exception:
        pass
    return SHIFT_HOURS.get(machine.shift_system, 520.0)

def fixed_manual_values(row):
    if not row or not getattr(row, 'manual_edits', None):
        return {}
    return {
        period: float(edit.get('new', row.values.get(period, 0.0) or 0.0))
        for period, edit in row.manual_edits.items()
    }

def recalc_one_material(
    current_engine,
    mat,
    inv_eng,
    bom_eng,
    periods_list,
    override_forecast=False,
    override_target_stock=None,
    override_target_stock_values=None,
    preserve_l05=True,
):
    """Recalculate inventory + BOM for one material. Updates results in-place.
    Returns {child_mat: child_period_demand} so the caller can cascade further."""
    fc_row = next(
        (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
         if r.material_number == mat), None
    )
    forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

    mat_l02 = [r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
               if r.material_number == mat]
    dep_demand_agg = {p: 0.0 for p in periods_list}
    dep_demand_by_parent = {}
    for r in mat_l02:
        parent = r.aux_column
        if parent:
            dep_demand_by_parent[parent] = dict(r.values)
            for p in periods_list:
                dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

    l05_row = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    l05_saved_values = dict(l05_row.values) if l05_row else {}
    l05_saved_edits = dict(l05_row.manual_edits) if l05_row else {}

    # Preserve manually edited periods for production plan and purchase receipt so
    # cascade recalculations (demand change, BOM cascade) honour sticky overrides â€”
    # identical to the direct-edit path in apply_volume_change.
    prod_row_pre = next(
        (r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
         if r.material_number == mat), None
    )
    purch_row_pre = next(
        (r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
         if r.material_number == mat), None
    )
    prior_prod_edits  = dict(prod_row_pre.manual_edits)  if prod_row_pre  and getattr(prod_row_pre,  'manual_edits', None) else {}
    prior_purch_edits = dict(purch_row_pre.manual_edits) if purch_row_pre and getattr(purch_row_pre, 'manual_edits', None) else {}
    fixed_prod  = fixed_manual_values(prod_row_pre)  or None
    fixed_purch = fixed_manual_values(purch_row_pre) or None

    kwargs = {}
    if override_forecast:
        kwargs['override_forecast'] = forecast_vals
    if override_target_stock_values is not None:
        kwargs['override_target_stock_values'] = override_target_stock_values
    if override_target_stock is not None:
        kwargs['override_target_stock'] = override_target_stock
    if fixed_prod:
        kwargs['fixed_production_plan'] = fixed_prod
    if fixed_purch:
        kwargs['fixed_purchase_receipt'] = fixed_purch
    inv_result = inv_eng.calculate_for_material(
        mat, forecast_vals, dep_demand_agg, dep_demand_by_parent, **kwargs
    )

    inv_line_types = [
        LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
        LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
        LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
    ]
    for lt in inv_line_types:
        current_engine.results[lt] = [
            r for r in current_engine.results.get(lt, []) if r.material_number != mat
        ]
    for row in inv_result['rows']:
        if row.line_type in current_engine.results:
            current_engine.results[row.line_type].append(row)

    if inv_result.get('purch_raw_need'):
        current_engine.all_purch_raw_needs[mat] = inv_result['purch_raw_need']
    else:
        current_engine.all_purch_raw_needs.pop(mat, None)

    new_l05 = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    if new_l05 and preserve_l05:
        new_l05.values = l05_saved_values
        new_l05.manual_edits = l05_saved_edits

    # Restore manual_edits markers on the rebuilt L06 rows so the UI can still
    # tell which periods were manually set (edit indicators, undo stack, etc.).
    new_prod_row = next(
        (r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
         if r.material_number == mat), None
    )
    if new_prod_row and prior_prod_edits:
        new_prod_row.manual_edits = prior_prod_edits
    new_purch_row = next(
        (r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
         if r.material_number == mat), None
    )
    if new_purch_row and prior_purch_edits:
        new_purch_row.manual_edits = prior_purch_edits

    if inv_result['production_plan'] is not None:
        current_engine.all_production_plans[mat] = inv_result['production_plan']
    else:
        current_engine.all_production_plans.pop(mat, None)
    if inv_result['purchase_receipt'] is not None:
        current_engine.all_purchase_receipts[mat] = inv_result['purchase_receipt']
    else:
        current_engine.all_purchase_receipts.pop(mat, None)

    # Compute dependent requirements and push updated L02/L03 to children
    current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
        r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
        if r.material_number != mat
    ]
    children_demand = {}
    if inv_result['production_plan'] is not None:
        children_demand = bom_eng.compute_dependent_requirements(mat, inv_result['production_plan'])
        if children_demand:
            dr_rows = bom_eng.create_dependent_requirements_rows(mat, children_demand)
            current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

    for child_mat, child_period_demand in children_demand.items():
        current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
            if not (r.material_number == child_mat and r.aux_column == mat)
        ]
        child_l02_new = bom_eng.create_dependent_demand_rows(
            child_mat, {mat: child_period_demand}
        )
        current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

        child_l01_row = next(
            (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
             if r.material_number == child_mat), None
        )
        child_l03_row = next(
            (r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
             if r.material_number == child_mat), None
        )
        if child_l03_row:
            child_all_l02 = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if r.material_number == child_mat
            ]
            for p in periods_list:
                fc_val = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                child_l03_row.values[p] = fc_val + dep_val

    return children_demand

def recalc_material_subtree(
    current_engine,
    root_material,
    override_root_forecast=False,
    root_override_target_stock=None,
    root_override_target_stock_values=None,
    preserve_root_l05=True,
):
    """Recalculate one edited material and recursively all impacted descendants."""
    from modules.inventory_engine import InventoryEngine
    from modules.bom_engine import BOMEngine

    inv_eng = InventoryEngine(current_engine.data)
    bom_eng = BOMEngine(current_engine.data)
    periods_list = current_engine.data.periods

    root_children = recalc_one_material(
        current_engine,
        root_material,
        inv_eng,
        bom_eng,
        periods_list,
        override_forecast=override_root_forecast,
        override_target_stock=root_override_target_stock,
        override_target_stock_values=root_override_target_stock_values,
        preserve_l05=preserve_root_l05,
    )

    queue = list(root_children.keys())
    visited = {root_material}
    while queue:
        child_mat = queue.pop(0)
        if child_mat in visited:
            continue
        visited.add(child_mat)
        grandchildren = recalc_one_material(
            current_engine,
            child_mat,
            inv_eng,
            bom_eng,
            periods_list,
            override_forecast=False,
            preserve_l05=True,
        )
        queue.extend(gc for gc in grandchildren if gc not in visited)

def recalculate_capacity_and_values(current_engine, sess):
    """Run capacity + value planning after volume cascades."""
    from modules.capacity_engine import CapacityEngine

    _all_line_data = {
        lt: {r.material_number: r.values for r in rows}
        for lt, rows in current_engine.results.items() if rows
    }
    cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
    cap_results = cap_eng.calculate()
    for lt, cap_rows in cap_results.items():
        current_engine.results[lt] = cap_rows
    recalculate_value_results(current_engine, sess)

def apply_volume_change(sess, current_engine, line_type, material_number, period, new_value,
                          aux_column='',
                          push_undo=True):
    """Internal helper: apply a volume change + cascade and return jsonify result.

    Used by /api/update_volume (via direct code), /api/undo, /api/redo.
    """
    if line_type not in EDITABLE_LINE_TYPES:
        return jsonify({'error': f'Line type "{line_type}" is not editable'}), 403
    rows = current_engine.results.get(line_type, [])
    material_number = str(material_number)
    aux_column = str(aux_column or '').strip()
    material_rows = [r for r in rows if str(getattr(r, 'material_number', '')) == material_number]
    target_row = next(
        (r for r in material_rows
         if str(getattr(r, 'aux_column', '') or '').strip() == aux_column),
        None
    )
    if target_row is None and len(material_rows) == 1:
        # Backward-compatible fallback for older clients and harmless formatting drift.
        target_row = material_rows[0]
    if target_row is None:
        available_aux = sorted({str(getattr(r, 'aux_column', '') or '').strip() for r in material_rows})
        detail = f'Row not found for {line_type} / {material_number}'
        if aux_column:
            detail += f' / aux "{aux_column}"'
        if available_aux:
            detail += f'. Available aux: {", ".join(available_aux[:6])}'
        return jsonify({'error': detail}), 404
    ensure_reset_baseline(sess, current_engine, SHIFT_HOURS_LOOKUP_FALLBACK)

    # Enforce ceiling rounding for L06 line types so the stored value always
    # respects the configured lot multiple (BOM header qty for production plan,
    # MOQ for purchase receipt). Only applies when new_value > 0; setting to 0
    # is allowed unconditionally (manual clearance).
    if new_value > 0 and line_type in (LineType.PRODUCTION_PLAN.value, LineType.PURCHASE_RECEIPT.value):
        from modules.inventory_engine import ceiling_multiple as _ceil_mult
        if line_type == LineType.PRODUCTION_PLAN.value:
            _multiple = current_engine.data.get_production_ceiling(material_number)
        else:
            _multiple = current_engine.data.get_purchase_moq(material_number)
        if _multiple and _multiple > 0:
            new_value = _ceil_mult(new_value, _multiple)

    old_value = target_row.get_value(period)

    if push_undo:
        undo_stack = sess.setdefault('undo_stack', [])
        sess.setdefault('redo_stack', []).clear()
        undo_stack.append({'line_type': line_type, 'material_number': material_number,
                           'aux_column': str(getattr(target_row, 'aux_column', '') or ''),
                           'period': period, 'old_value': old_value, 'new_value': new_value})
        if len(undo_stack) > 50:
            undo_stack.pop(0)

    # Update manual_edits tracking
    if period not in target_row.manual_edits:
        target_row.manual_edits[period] = {'original': old_value, 'new': new_value}
    else:
        target_row.manual_edits[period]['new'] = new_value
    # If restored to original, remove the edit tracking entry
    original_val = target_row.manual_edits[period].get('original', old_value)
    if new_value == original_val:
        target_row.manual_edits.pop(period, None)

    target_row.set_value(period, new_value)

    # Keep pending_edits in sync server-side so the edit survives a server restart
    # without needing the frontend's separate /api/sessions/edits/persist call.
    _edit_key = pending_edit_key(line_type, material_number, aux_column, period)
    _pending = sess.setdefault('pending_edits', {})
    if new_value == original_val:
        _pending.pop(_edit_key, None)
    else:
        # Preserve the original baseline from any existing entry so repeated edits
        # of the same cell don't overwrite the true pre-edit value.
        _baseline = _pending.get(_edit_key, {}).get('original', original_val)
        _pending[_edit_key] = {'original': _baseline, 'new_value': new_value}

    if line_type == LineType.MIN_TARGET_STOCK.value:
        target_stock_values = dict(target_row.values)
        recalc_material_subtree(
            current_engine,
            material_number,
            override_root_forecast=False,
            root_override_target_stock_values=target_stock_values,
            preserve_root_l05=True,
        )
        recalculate_capacity_and_values(current_engine, sess)

    elif line_type == LineType.DEMAND_FORECAST.value:
        recalc_material_subtree(
            current_engine,
            material_number,
            override_root_forecast=True,
            root_override_target_stock=None,
            preserve_root_l05=True,
        )
        recalculate_capacity_and_values(current_engine, sess)

    elif line_type in (LineType.PRODUCTION_PLAN.value, LineType.PURCHASE_RECEIPT.value):
        from modules.bom_engine import BOMEngine
        from modules.inventory_engine import InventoryEngine

        periods_list = current_engine.data.periods

        # Gather current rows (target_row already has new_value applied)
        prod_row  = next((r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
                          if r.material_number == material_number), None)
        purch_row = next((r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
                          if r.material_number == material_number), None)
        l05_row   = next((r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
                          if r.material_number == material_number), None)

        # Recalculate this material's own inventory and L06/L07 using fixed manual overrides for edited periods.
        # This avoids silently overwriting the user's manual edit while still refreshing later periods.
        inv_eng = InventoryEngine(current_engine.data)
        fc_row = next(
            (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
             if r.material_number == material_number), None
        )
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

        mat_l02 = [r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                   if r.material_number == material_number]
        dep_demand_agg = {p: 0.0 for p in periods_list}
        dep_demand_by_parent = {}
        for r in mat_l02:
            parent = r.aux_column
            if parent:
                dep_demand_by_parent[parent] = dict(r.values)
                for p in periods_list:
                    dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

        inv_result = inv_eng.calculate_for_material(
            material_number,
            forecast_vals,
            dep_demand_agg,
            dep_demand_by_parent,
            override_target_stock_values=dict(l05_row.values) if l05_row else None,
            fixed_production_plan=fixed_manual_values(prod_row),
            fixed_purchase_receipt=fixed_manual_values(purch_row),
        )

        inv_line_types = [
            LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
            LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
            LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
        ]
        # Preserve manual_edits markers for L06/L07 across the rebuild â€” otherwise
        # a subsequent edit would forget prior edited months and recompute them heuristically.
        prior_prod_edits = dict(prod_row.manual_edits) if prod_row and getattr(prod_row, 'manual_edits', None) else {}
        prior_purch_edits = dict(purch_row.manual_edits) if purch_row and getattr(purch_row, 'manual_edits', None) else {}
        for lt in inv_line_types:
            current_engine.results[lt] = [
                r for r in current_engine.results.get(lt, []) if r.material_number != material_number
            ]

        for row in inv_result['rows']:
            if row.line_type == LineType.MIN_TARGET_STOCK.value and l05_row:
                row.values = dict(l05_row.values)
                row.manual_edits = dict(l05_row.manual_edits)
            elif row.line_type == LineType.PRODUCTION_PLAN.value and prior_prod_edits:
                row.manual_edits = prior_prod_edits
            elif row.line_type == LineType.PURCHASE_RECEIPT.value and prior_purch_edits:
                row.manual_edits = prior_purch_edits
            if row.line_type in current_engine.results:
                current_engine.results[row.line_type].append(row)

        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)

        # Rebuild L08 dependent requirements from the (now updated) production plan
        bom_eng = BOMEngine(current_engine.data)
        current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
            if r.material_number != material_number
        ]
        children_demand = {}
        prod_row = next((r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
                          if r.material_number == material_number), None)
        if prod_row is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, dict(prod_row.values)
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Propagate to child materials: refresh direct L02 links from edited parent
        for child_mat, child_period_demand in children_demand.items():
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

        # Full downstream cascade: recalculate every affected child (and deeper levels)
        inv_eng = InventoryEngine(current_engine.data)
        queue = list(children_demand.keys())
        visited = {material_number}
        while queue:
            child_mat = queue.pop(0)
            if child_mat in visited:
                continue
            visited.add(child_mat)
            grandchildren = recalc_one_material(
                current_engine,
                child_mat,
                inv_eng,
                bom_eng,
                periods_list,
                override_forecast=False,
            )
            queue.extend(gc for gc in grandchildren if gc not in visited)

        recalculate_capacity_and_values(current_engine, sess)

    else:
        recalculate_value_results(current_engine, sess)

    delta_pct = round((new_value - original_val) / abs(original_val) * 100, 2) if original_val != 0 else 0.0
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({
        'success': True,
        'results': results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
        'edit_meta': {
            'old_value': old_value,
            'new_value': new_value,
            'original_value': original_val,
            'delta_pct': delta_pct,
        },
    })
