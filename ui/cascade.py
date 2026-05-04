"""Cascade helpers for PAP and planning edits."""


def recalc_pap_material(current_engine, material_number, recalc_one_material_fn):
    """Re-run inventory + full BOM cascade for a PAP material change.
    Uses BFS so every child (and grandchild, etc.) gets its inventory recalculated
    after its dependent demand is updated -- not just L02/L03."""
    from modules.inventory_engine import InventoryEngine
    from modules.bom_engine import BOMEngine

    inv_eng = InventoryEngine(current_engine.data)
    bom_eng = BOMEngine(current_engine.data)
    periods_list = current_engine.data.periods

    # Recalculate the PAP material itself (override_forecast keeps the PAP split intact)
    children_demand = recalc_one_material_fn(
        current_engine, material_number, inv_eng, bom_eng, periods_list,
        override_forecast=True,
    )

    # BFS: recalculate every affected child's inventory so the cascade is complete
    queue = list(children_demand.keys())
    visited = {material_number}
    while queue:
        child_mat = queue.pop(0)
        if child_mat in visited:
            continue
        visited.add(child_mat)
        grandchildren_demand = recalc_one_material_fn(
            current_engine, child_mat, inv_eng, bom_eng, periods_list,
            override_forecast=False,
        )
        queue.extend(gc for gc in grandchildren_demand if gc not in visited)


def finish_pap_recalc(current_engine, sess, recalculate_capacity_and_values_fn):
    """Run capacity + value engines after a PAP fraction change."""
    recalculate_capacity_and_values_fn(current_engine, sess)
