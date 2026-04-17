"""
S&OP Planning Engine - Inventory Engine
Calculates per-material inventory planning for a SINGLE material at a time.

Lines calculated:
- Line 03: Total Demand  (forecast + dependent demand)
- Line 04: Inventory     (running balance, CAN go negative)
- Line 05: Minimum Target Stock  (safety_stock + strategic_stock)
- Line 06: Production Plan / Purchase Receipt  (heuristic with ceiling)
- Line 07: Purchase Plan (receipt shifted by lead time)

CRITICAL LOGIC (verified against Excel VBA):
- Production Plan: CEILING(need, BOM_header_quantity)  NOT lot_size!
- Purchase Receipt: CEILING(need, MOQ_from_purchase_sheet)
- For produced+purchased:
    1. production_need = raw_need * production_fraction
    2. production_plan = CEILING(production_need, BOM_header_qty) if > 0 else 0
    3. purchase_need = raw_need - production_plan  (residual, NOT proportional)
    4. purchase_receipt = CEILING(purchase_need, MOQ_from_purchase_sheet) if > 0 else 0
- Inventory CAN go negative (backorder)
"""

import math
from typing import Dict, List, Optional
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


def ceiling_multiple(value: float, multiple: float) -> float:
    """Excel-compatible CEILING(value, multiple)."""
    if multiple <= 0 or value <= 0:
        return 0.0
    ratio = value / multiple
    nearest_int = round(ratio)
    if math.isclose(ratio, nearest_int, rel_tol=1e-9, abs_tol=1e-9):
        return nearest_int * multiple
    return math.ceil(ratio) * multiple


class InventoryEngine:
    """Calculates inventory-related lines for a single material."""

    def __init__(self, data: DataLoader):
        self.data = data
        self.periods = data.periods

    def calculate_for_material(
        self,
        mat_num: str,
        forecast: Dict[str, float],
        dependent_demand_agg: Dict[str, float],
        dependent_demand_by_parent: Dict[str, Dict[str, float]],
        override_target_stock: Optional[float] = None,
        override_target_stock_values: Optional[Dict[str, float]] = None,
        override_forecast: Optional[Dict[str, float]] = None,
        fixed_production_plan: Optional[Dict[str, float]] = None,
        fixed_purchase_receipt: Optional[Dict[str, float]] = None,
    ) -> Dict:
        material = self.data.materials.get(mat_num)
        if not material:
            return {'total_demand': {}, 'target_stock_value': 0, 'production_plan': None,
                    'purchase_receipt': None, 'purchase_plan': None, 'inventory': {}, 'rows': []}

        if override_forecast is not None:
            forecast = override_forecast

        rows: List[PlanningRow] = []

        # === LINE 03: Total Demand = Forecast + Dependent Demand ===
        total_demand = {}
        for period in self.periods:
            total_demand[period] = forecast.get(period, 0.0) + dependent_demand_agg.get(period, 0.0)

        rows.append(self._make_row(mat_num, material, LineType.TOTAL_DEMAND.value,
                                   values=total_demand))

        # === LINE 05: Minimum Target Stock ===
        # VBA Method A (default): constant = safety_stock + strategic_stock
        # VBA Method B (moving average): CreateTargetStock_MovingAverage with window=3
        MOVING_AVG_WINDOW = 3
        ss_config = self.data.safety_stock.get(mat_num)
        base_target = (ss_config.safety_stock + ss_config.strategic_stock) if ss_config else 0.0

        forecast_demand_values = [total_demand.get(p, 0.0) for p in self.periods]
        avg_forecast_demand = sum(forecast_demand_values) / len(forecast_demand_values) if forecast_demand_values else 0.0

        if override_target_stock_values is not None:
            target_values = {
                p: float(override_target_stock_values.get(p, base_target) or 0.0)
                for p in self.periods
            }
        elif override_target_stock is not None:
            target_values = {p: override_target_stock for p in self.periods}
        elif ss_config and ss_config.use_moving_average and avg_forecast_demand > 0:
            # VBA Method B: per-period target = moving_avg(demand) * coverage_months
            coverage_months = base_target / avg_forecast_demand
            target_values = {}
            for idx, p in enumerate(self.periods):
                window_end = min(idx + MOVING_AVG_WINDOW, len(self.periods))
                window = [total_demand.get(self.periods[j], 0.0) for j in range(idx, window_end)]
                window_avg = sum(window) / len(window) if window else 0.0
                target_values[p] = window_avg * coverage_months
        else:
            target_values = {p: base_target for p in self.periods}

        # For display: use base_target for aux_column, average of target_values for coverage
        if override_target_stock_values is not None:
            display_target = sum(target_values.values()) / len(target_values) if target_values else 0.0
        else:
            display_target = base_target if override_target_stock is None else override_target_stock
        coverage = round(display_target / avg_forecast_demand, 1) if avg_forecast_demand > 0 else 0.0
        # Append '!' as a warning flag when coverage exceeds 6 months (VBA: light red RGB 255,199,206)
        if coverage > 0:
            coverage_str = f"{coverage:.1f}!" if coverage > 6 else f"{coverage:.1f}"
        else:
            coverage_str = None

        target_stock_data = dict(target_values)
        rows.append(self._make_row(
            mat_num, material, LineType.MIN_TARGET_STOCK.value,
            aux_column=(str(int(display_target)) if display_target == int(display_target) else str(round(display_target, 2))) if display_target else None,
            aux_2_column=coverage_str,
            values=target_stock_data,
        ))

        # === DETERMINE: Produced, Purchased, or Both ===
        is_purchased_and_produced = self.data.is_purchased_and_produced(mat_num)
        production_fraction_config = self.data.get_purchase_fraction(mat_num)

        # BOM header quantity = ceiling multiple for production plan
        bom_header_qty = self.data.get_production_ceiling(mat_num)
        lot_size = ss_config.lot_size if ss_config else 1.0
        if lot_size <= 0:
            lot_size = 1.0

        # Purchase receipt ceiling multiple (VBA: XLOOKUP to Purchase sheet col 7 = MOQ/lot size)
        in_purchase_sheet = mat_num in self.data.purchase_sheet_materials
        purch_ceil_multiple = self.data.get_purchase_moq(mat_num) if in_purchase_sheet else 1.0

        has_routing = len(self.data.get_all_routings(mat_num)) > 0
        is_bom_parent = any(b.parent_material == mat_num for b in self.data.bom if not b.is_coproduct)

        if is_purchased_and_produced:
            needs_production = True
            needs_purchase = True
        elif is_bom_parent and has_routing:
            needs_production = True
            needs_purchase = False
        else:
            needs_production = False
            needs_purchase = True

        # === Parameters ===
        initial_stock = self.data.stock_levels.get(mat_num, 0.0)
        lead_time = self.data.get_lead_time(mat_num) if needs_purchase else 0

        # === LINE 06: Production Plan and/or Purchase Receipt ===
        fixed_production_plan = {k: float(v or 0.0) for k, v in (fixed_production_plan or {}).items()}
        fixed_purchase_receipt = {k: float(v or 0.0) for k, v in (fixed_purchase_receipt or {}).items()}

        production_plan = {p: 0.0 for p in self.periods} if needs_production else None
        purchase_receipt = {p: 0.0 for p in self.periods} if needs_purchase else None
        purch_raw_need: Dict[str, float] = {}  # raw need before MOQ ceiling, per period

        running_stock = initial_stock
        min_prod_qty = bom_header_qty  # VBA Add_MinProdQty: minimum batch = BOM header quantity

        for i, period in enumerate(self.periods):
            demand = total_demand.get(period, 0.0)
            raw_need = target_values[period] - running_stock + demand

            prod_qty = 0.0
            purch_qty = 0.0
            fixed_prod = production_plan is not None and period in fixed_production_plan
            fixed_purch = purchase_receipt is not None and period in fixed_purchase_receipt

            if is_purchased_and_produced:
                if fixed_prod:
                    prod_qty = fixed_production_plan[period]
                elif raw_need > 0:
                    prod_need = raw_need * production_fraction_config
                    if prod_need > 0:
                        prod_qty = ceiling_multiple(prod_need, bom_header_qty)
                        prod_qty = max(prod_qty, min_prod_qty)

                if fixed_purch:
                    purch_qty = fixed_purchase_receipt[period]
                elif raw_need > 0:
                    purch_need = raw_need - prod_qty
                    if purch_need > 0 and i >= lead_time:
                        purch_qty = ceiling_multiple(purch_need, self.data.get_purchase_moq(mat_num))
                        purch_raw_need[period] = purch_need

            elif needs_production:
                if fixed_prod:
                    prod_qty = fixed_production_plan[period]
                elif raw_need > 0:
                    prod_qty = ceiling_multiple(raw_need, bom_header_qty)
                    prod_qty = max(prod_qty, min_prod_qty)

            elif needs_purchase:
                if fixed_purch:
                    purch_qty = fixed_purchase_receipt[period]
                elif raw_need > 0 and i >= lead_time:
                    purch_qty = ceiling_multiple(raw_need, purch_ceil_multiple)
                    purch_raw_need[period] = raw_need

            if production_plan is not None:
                production_plan[period] = prod_qty
            if purchase_receipt is not None:
                purchase_receipt[period] = purch_qty

            running_stock = running_stock - demand + prod_qty + purch_qty

        # === ProcessPurchaseReceipts: overwrite frozen/first-flexible months with actuals ===
        if purchase_receipt is not None and mat_num in self.data.purchase_actuals:
            actuals_map = self.data.purchase_actuals[mat_num]
            for i, period in enumerate(self.periods):
                if i < lead_time:
                    # Frozen period: always use actual PO qty (0 if no PO)
                    purchase_receipt[period] = actuals_map.get(period, 0.0)
                elif i == lead_time:
                    # First flexible month: only use actual if > 0 (VBA line 1864)
                    if actuals_map.get(period, 0.0) > 0:
                        purchase_receipt[period] = actuals_map[period]
                else:
                    break  # heuristic values kept for remaining months

            # Re-run: rebuild running_stock from scratch and recalculate heuristic
            # for all periods not locked by actuals.
            # - Frozen (i < lead_time): keep actuals, advance running_stock
            # - First-flexible (i == lead_time) with actual > 0: keep actual, advance
            # - First-flexible with actual == 0 OR beyond: recompute heuristic
            # This mirrors Excel's live-formula recalculation after ProcessPurchaseReceipts.
            running_stock = initial_stock
            for i, period in enumerate(self.periods):
                demand = total_demand.get(period, 0.0)
                raw_need = target_values[period] - running_stock + demand
                prod_qty = 0.0
                purch_qty = 0.0

                if i < lead_time or (i == lead_time and actuals_map.get(period, 0.0) > 0):
                    # Frozen or first-flexible locked by actual: keep actuals-set value
                    prod_qty = production_plan.get(period, 0.0) if production_plan else 0.0
                    purch_qty = purchase_receipt.get(period, 0.0)
                else:
                    # First-flexible with no actual, or beyond: honour manual overrides
                    # first (same as the first pass), then fall back to heuristic.
                    if is_purchased_and_produced and production_plan is not None:
                        if period in fixed_production_plan:
                            prod_qty = fixed_production_plan[period]
                        elif raw_need > 0:
                            prod_need = raw_need * production_fraction_config
                            if prod_need > 0:
                                prod_qty = ceiling_multiple(prod_need, bom_header_qty)
                                prod_qty = max(prod_qty, min_prod_qty)
                        if period in fixed_purchase_receipt:
                            purch_qty = fixed_purchase_receipt[period]
                        elif raw_need > 0:
                            purch_need = raw_need - prod_qty
                            if purch_need > 0:
                                purch_qty = ceiling_multiple(
                                    purch_need, self.data.get_purchase_moq(mat_num))
                                purch_raw_need[period] = purch_need
                        production_plan[period] = prod_qty
                        purchase_receipt[period] = purch_qty
                    elif needs_purchase:
                        if period in fixed_purchase_receipt:
                            purch_qty = fixed_purchase_receipt[period]
                        elif raw_need > 0:
                            purch_qty = ceiling_multiple(raw_need, purch_ceil_multiple)
                            purch_raw_need[period] = raw_need
                        purchase_receipt[period] = purch_qty

                running_stock = running_stock - demand + prod_qty + purch_qty

        # === Create Line 06 rows ===
        if production_plan is not None:
            prod_aux1 = str(int(bom_header_qty)) if bom_header_qty == int(bom_header_qty) else str(bom_header_qty)
            prod_aux2 = str(production_fraction_config) if is_purchased_and_produced else None
            rows.append(self._make_row(
                mat_num, material, LineType.PRODUCTION_PLAN.value,
                aux_column=prod_aux1, aux_2_column=prod_aux2,
                values=production_plan,
            ))

        if purchase_receipt is not None:
            purch_ceil_val = self.data.get_purchase_moq(mat_num) if is_purchased_and_produced else purch_ceil_multiple
            purch_aux1 = str(int(purch_ceil_val)) if purch_ceil_val == int(purch_ceil_val) else str(purch_ceil_val)
            rows.append(self._make_row(
                mat_num, material, LineType.PURCHASE_RECEIPT.value,
                aux_column=purch_aux1, values=purchase_receipt,
            ))

        # === LINE 04: Inventory ===
        inventory = {}
        running_stock = initial_stock
        for period in self.periods:
            demand = total_demand.get(period, 0.0)
            prod = production_plan.get(period, 0.0) if production_plan else 0.0
            purch = purchase_receipt.get(period, 0.0) if purchase_receipt else 0.0
            running_stock = running_stock - demand + prod + purch
            inventory[period] = running_stock

        rows.append(self._make_row(
            mat_num, material, LineType.INVENTORY.value,
            starting_stock=initial_stock, values=inventory,
        ))

        # === LINE 07: Purchase Plan = Purchase Receipt shifted by lead_time ===
        purchase_plan = None
        if purchase_receipt is not None:
            purchase_plan = {}
            for i, period in enumerate(self.periods):
                future_idx = i + lead_time
                if future_idx < len(self.periods):
                    purchase_plan[period] = purchase_receipt.get(self.periods[future_idx], 0.0)
                else:
                    purchase_plan[period] = 0.0

            pp_aux = str(lead_time)
            rows.append(self._make_row(
                mat_num, material, LineType.PURCHASE_PLAN.value,
                aux_column=pp_aux, values=purchase_plan,
            ))

        return {
            'total_demand': total_demand,
            'target_stock_value': display_target,
            'production_plan': production_plan,
            'purchase_receipt': purchase_receipt,
            'purchase_plan': purchase_plan,
            'inventory': inventory,
            'purch_raw_need': purch_raw_need,
            'rows': rows,
        }

    def _make_row(self, mat_num, material, line_type,
                  aux_column=None, aux_2_column=None, starting_stock=0.0, values=None):
        return PlanningRow(
            material_number=mat_num,
            material_name=material.name,
            product_type=material.product_type.value,
            product_family=material.product_family,
            spc_product=material.spc_product or '',
            product_cluster=material.product_cluster or '',
            product_name=material.product_name or '',
            line_type=line_type,
            aux_column=aux_column,
            aux_2_column=aux_2_column,
            starting_stock=starting_stock,
            values=dict(values) if values else {},
        )
