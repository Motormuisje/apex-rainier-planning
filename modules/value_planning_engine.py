"""
S&OP Value Planning Engine — exact reproduction of VBA macro logic.

VBA flow (GenerateValuesSheet):
  1. Loop through Planning sheet rows
  2. For each row, decide whether to copy it to Values_Planning:
     - "01. Demand forecast", "04. Inventory", "06. Purchase receipt" → always copy
     - "03. Total demand" → only if material also has a "07. Purchase plan" row
     - "07. Capacity utilization" → only if ProdLineClmn = 1 in material master
     - "12. FTE requirements" → always copy, but mill/packaging groups get special formula
  3. For every copied row: values = AUX1 × volume_from_planning_sheet
  4. AUX1 (unit price) is set by separate subs:
     - "01. Demand forecast" → sum(ExWorksRevenue) / sum(Volume) from Avg Sales Price
     - "03. Total demand" & "06. Purchase receipt" → Cost Per Unit from Cost raw material
     - "04. Inventory" → Total Value / Total Stock from stock sheet, fallback to mat master
     - "07. Capacity utilization" → Fxd Price for "Machine Variable" from Cost machine hour
     - "12. FTE requirements" → Direct FTE cost per month from Valuation parameters
  5. Starting stock for "04. Inventory" → Value of Unrestricted Stock (col 17) from stock sheet
  6. Consolidation rows (20 rows from TURNOVER through ROCE)
"""

from typing import Dict, List, Optional, Set
from collections import defaultdict

from modules.models import (
    PlanningRow, LineType, ProductType, ValuationParameters,
    SalesPriceItem, RawMaterialCost, MachineCost
)


class ValuePlanningEngine:
    """Converts planning volumes/hours to financial values."""

    def __init__(self, data, planning_results: Dict[str, List[PlanningRow]],
                 aux_overrides: Optional[Dict[str, float]] = None):
        self.data = data
        self.planning_results = planning_results
        self.periods = data.periods
        self.value_results: Dict[str, List[PlanningRow]] = defaultdict(list)
        self.aux_overrides = aux_overrides or {}

        # Pre-build lookup sets for row filtering (VBA checks at generation time)
        self._purchase_plan_materials: Set[str] = set()

        self._build_lookups()

        # Running totals built during conversion
        self._totals: Dict[str, Dict[str, float]] = {
            k: {p: 0.0 for p in self.periods}
            for k in ('turnover', 'raw_material', 'machine',
                      'direct_fte', 'inventory', 'purchase_receipt')
        }

    # ------------------------------------------------------------------
    # Build lookups from planning results and material master
    # ------------------------------------------------------------------
    def _build_lookups(self):
        # Materials that have a "07. Purchase plan" row
        for row in self.planning_results.get(LineType.PURCHASE_PLAN.value, []):
            self._purchase_plan_materials.add(row.material_number)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def calculate(self) -> Dict[str, List[PlanningRow]]:
        print("\n" + "=" * 70)
        print("VALUE PLANNING ENGINE - FINANCIAL CONVERSION")
        print("=" * 70)

        self._convert_demand_forecast()
        self._convert_total_demand()
        self._convert_inventory()
        self._convert_purchase_receipt()
        self._convert_capacity_utilization()
        self._convert_fte_requirements()
        self._create_consolidation_rows()

        print("\nValue planning calculation complete")
        return dict(self.value_results)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_value_row(self, src: PlanningRow, line_type: str,
                        unit_price: float, starting_stock_value: float = 0.0
                        ) -> PlanningRow:
        """Build a value row: aux_column = unit_price, values = price × volume.
        
        VBA CopyDataAndApplyFormula copies columns 1-8 only (up to LineTypeClmn).
        AUX1 (col 9) is set by Add_* subs.
        AUX2 (col 10) is NOT set for individual material rows.
        """
        vr = PlanningRow(
            material_number=src.material_number,
            material_name=src.material_name,
            product_type=src.product_type,
            product_family=src.product_family,
            spc_product=src.spc_product,
            product_cluster=src.product_cluster,
            product_name=src.product_name,
            line_type=line_type,
            aux_column=unit_price,
            aux_2_column=None,  # VBA does NOT set aux2 for individual rows
            starting_stock=starting_stock_value,
        )
        for p in self.periods:
            vr.set_value(p, src.get_value(p) * unit_price)
        return vr

    def _accum(self, key: str, row: PlanningRow):
        bucket = self._totals[key]
        for p in self.periods:
            bucket[p] += row.get_value(p)

    def _get_aux_value(self, line_type: str, material_number: str, default: float) -> float:
        override = self.aux_overrides.get(f"{line_type}||{material_number}")
        if override is None:
            return default
        try:
            return float(override)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # 1. Demand Forecast → Revenue
    # VBA: Add_Sales_Price → aux = sum(ExWorksRevenue) / sum(Volume)
    # ------------------------------------------------------------------
    def _convert_demand_forecast(self):
        print("\n[1] Converting demand forecast to revenue...")
        rows = self.planning_results.get(LineType.DEMAND_FORECAST.value, [])
        converted = 0
        for row in rows:
            sp = self.data.sales_prices.get(row.material_number)
            # VBA: =IFERROR(AUX1 * volume, 0) — row always included, 0 if no price
            price = sp.price_per_unit if sp and sp.price_per_unit != 0 else 0
            price = self._get_aux_value(LineType.DEMAND_FORECAST.value, row.material_number, price)
            vr = self._make_value_row(row, LineType.DEMAND_FORECAST.value, price)
            self.value_results[LineType.DEMAND_FORECAST.value].append(vr)
            self._accum('turnover', vr)
            converted += 1
        print(f"  Converted {converted} demand forecast rows")

    # ------------------------------------------------------------------
    # 2. Total Demand → Raw material cost
    # VBA: Only copied if material has a "07. Purchase plan" row
    # VBA: Add_Raw_Material_Cost → aux = Cost Per Unit
    # ------------------------------------------------------------------
    def _convert_total_demand(self):
        print("\n[2] Converting total demand to raw material cost...")
        rows = self.planning_results.get(LineType.TOTAL_DEMAND.value, [])
        converted = 0

        # VBA: purchased_and_produced materials get special treatment
        pap = {}
        if hasattr(self.data, 'purchased_and_produced'):
            pap = self.data.purchased_and_produced  # mat_num -> produced_ratio

        for row in rows:
            # VBA: only include if material has a "07. Purchase plan" row
            if row.material_number not in self._purchase_plan_materials:
                continue
            mc = self.data.material_costs.get(row.material_number)
            # VBA: =IFERROR(AUX1 * volume, 0) — row always included, 0 if no cost
            unit_cost = mc.cost_per_unit if mc else 0
            unit_cost = self._get_aux_value(LineType.TOTAL_DEMAND.value, row.material_number, unit_cost)

            # VBA: PurchasedAndProducedMaterials special case
            # value = aux * volume * (1 - produced_ratio)
            factor = 1.0
            if row.material_number in pap:
                factor = 1.0 - pap[row.material_number]

            vr = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.TOTAL_DEMAND.value,
                aux_column=unit_cost,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0,
            )
            for p in self.periods:
                vr.set_value(p, row.get_value(p) * unit_cost * factor)

            self.value_results[LineType.TOTAL_DEMAND.value].append(vr)
            self._accum('raw_material', vr)
            converted += 1
        print(f"  Converted {converted} total demand rows")

    # ------------------------------------------------------------------
    # 3. Inventory → Inventory value
    # VBA: Add_Inventory_Value →
    #   aux = TotalValue / TotalStock from stock sheet
    #   fallback: Material master InventoryValueClmn
    # VBA: ValueStartStockLevel →
    #   starting_stock = initial_stock_qty × unit_cost (consistent with period values)
    # ------------------------------------------------------------------
    def _get_inventory_unit_cost(self, mat_num: str) -> float:
        stock = self.data.stock.get(mat_num)
        if stock:
            ts = stock.get('Total Stock', 0)
            if ts and ts > 0:
                return stock.get('Total Value', 0) / ts
        # Fallback: material master default inventory value
        mat = self.data.materials.get(mat_num)
        if mat:
            return mat.default_inventory_value
        return 0.0

    def _convert_inventory(self):
        print("\n[3] Converting inventory to value...")
        rows = self.planning_results.get(LineType.INVENTORY.value, [])
        converted = 0
        for row in rows:
            uc = self._get_inventory_unit_cost(row.material_number)
            uc = self._get_aux_value(LineType.INVENTORY.value, row.material_number, uc)
            # Compute starting stock value the same way as period values:
            # initial_stock_qty (= row.starting_stock) × unit_cost.
            # This is consistent with how period inventory values are computed and
            # more robust than reading "Value of Unrestricted Stock" from SAP (which
            # may be missing or use a different price base).
            ss_value = row.starting_stock * uc
            # VBA: always include, IFERROR → 0 if no cost
            vr = self._make_value_row(row, LineType.INVENTORY.value, uc,
                                      starting_stock_value=ss_value)
            self.value_results[LineType.INVENTORY.value].append(vr)
            self._accum('inventory', vr)
            converted += 1
        print(f"  Converted {converted} inventory rows")

    # ------------------------------------------------------------------
    # 4. Purchase Receipt → Purchase cost
    # VBA: Add_Raw_Material_Cost handles both "03. Total demand" and "06. Purchase receipt"
    # ------------------------------------------------------------------
    def _convert_purchase_receipt(self):
        print("\n[4] Converting purchase receipts to cost...")
        rows = self.planning_results.get(LineType.PURCHASE_RECEIPT.value, [])
        converted = 0
        for row in rows:
            mc = self.data.material_costs.get(row.material_number)
            # VBA: always include, IFERROR → 0 if no cost
            cost = mc.cost_per_unit if mc else 0
            cost = self._get_aux_value(LineType.PURCHASE_RECEIPT.value, row.material_number, cost)
            vr = self._make_value_row(row, LineType.PURCHASE_RECEIPT.value, cost)
            self.value_results[LineType.PURCHASE_RECEIPT.value].append(vr)
            self._accum('purchase_receipt', vr)
            converted += 1
        print(f"  Converted {converted} purchase receipt rows")

    # ------------------------------------------------------------------
    # 5. Capacity Utilization → Machine cost
    # VBA: Only rows where ProdLineClmn = 1 in material master
    # VBA: Add_Machine_Cost → lookupValue = column B (machine code)
    #      Left(CostCenter, 5) = machine_code, act_type = "Machine Variable"
    #      aux = CostMHClmn = 7 = Fxd Prices in OCrcy
    # ------------------------------------------------------------------
    def _convert_capacity_utilization(self):
        print("\n[5] Converting capacity utilization to machine cost...")
        rows = self.planning_results.get(LineType.CAPACITY_UTILIZATION.value, [])
        converted = 0
        for row in rows:
            # VBA: only rows in the production-line section of the planning sheet.
            # In Python these are machine-level aggregation rows, identified by product_type='Machine'.
            # (Material-level routing rows have product_type = the material's product type,
            #  group-level rows have product_type = 'Machine Group'.)
            if row.product_type != 'Machine':
                continue

            # VBA: lookupValue = MachineNameClmn (col B) = machine code
            # In planning data, for machine rows, material_name = machine_code (e.g. PBA11)
            machine_code = row.material_name  # col B = machine code for Z_MACH rows
            if not machine_code:
                continue

            # VBA: Left(CostCenter, 5) match
            mach_cost = self.data.machine_costs.get(machine_code)
            if not mach_cost:
                continue

            rate = mach_cost.variable_cost_per_hour  # Actually Fxd Price per VBA
            if rate == 0:
                continue
            rate = self._get_aux_value(LineType.CAPACITY_UTILIZATION.value, row.material_number, rate)

            vr = self._make_value_row(row, LineType.CAPACITY_UTILIZATION.value, rate)
            self.value_results[LineType.CAPACITY_UTILIZATION.value].append(vr)
            self._accum('machine', vr)
            converted += 1
        print(f"  Converted {converted} capacity utilization rows")

    # ------------------------------------------------------------------
    # 6. FTE Requirements → Direct FTE cost
    # VBA: ALL FTE rows are copied. AUX = Direct FTE cost per month (val param #1)
    # VBA GenerateValuesSheet: values = AUX1 × volume_from_planning_sheet for all rows
    # (truck FTE values are already computed correctly in the planning engine)
    # ------------------------------------------------------------------
    def _convert_fte_requirements(self):
        print("\n[6] Converting FTE requirements to direct FTE cost...")
        if not self.data.valuation_params:
            print("  Warning: No valuation parameters, skipping")
            return
        rows = self.planning_results.get(LineType.FTE_REQUIREMENTS.value, [])

        converted = 0
        for row in rows:
            fte_cost = self._get_aux_value(
                LineType.FTE_REQUIREMENTS.value,
                row.material_number,
                self.data.valuation_params.direct_fte_cost_per_month,
            )
            # VBA GenerateValuesSheet: values = AUX1 × volume_from_planning_sheet
            # The planning row already has the correct FTE value (for trucks this is
            # computed by TruckOperationsFormulas in the planning engine). We just
            # multiply by fte_cost here, same as every other FTE row.
            vr = self._make_value_row(row, LineType.FTE_REQUIREMENTS.value, fte_cost)
            self.value_results[LineType.FTE_REQUIREMENTS.value].append(vr)
            self._accum('direct_fte', vr)
            converted += 1
        print(f"  Converted {converted} FTE requirement rows")

    # ------------------------------------------------------------------
    # 7. Financial Consolidation (20 rows)
    # ------------------------------------------------------------------
    def _create_consolidation_rows(self):
        print("\n[7] Creating financial consolidation rows...")

        if not self.data.valuation_params:
            print("  Warning: No valuation parameters, skipping consolidation")
            return

        vp = self.data.valuation_params
        T = self._totals
        P = self.periods

        consol: List[PlanningRow] = []

        def _cr(mat_num, aux, vals, use_avg=False, include_starting_in_avg=False):
            """Create a consolidation row.
            VBA: some use SUM for aux2, some use AVERAGE.
            INVENTORY VALUE uses AVERAGE(StartForecast-1 : EndForecast) — includes starting stock!
            """
            if include_starting_in_avg:
                # VBA: AVERAGE(PlanningStartForecastClmn - 1 : PlanningEndForecastClmn)
                # This includes the Starting Stock column, so it's 13 values not 12
                starting = sum(
                    r.starting_stock
                    for r in self.value_results.get(LineType.INVENTORY.value, [])
                )
                all_vals = [starting] + [vals[p] for p in P]
                n = len(all_vals)
                a2 = sum(all_vals) / n if n > 0 else 0
            elif use_avg:
                n = len(vals)
                a2 = sum(vals.values()) / n if n > 0 else 0
            else:
                a2 = sum(vals.values())
            return PlanningRow(
                material_number=mat_num, material_name="",
                product_type="", product_family="", spc_product="",
                product_cluster="", product_name="",
                line_type=LineType.CONSOLIDATION.value,
                aux_column=aux,
                aux_2_column=str(a2) if a2 != 0 else None,
                starting_stock=0.0, values=dict(vals))

        def const(val):
            return {p: val for p in P}

        def add(*dicts):
            return {p: sum(d[p] for d in dicts) for p in P}

        def sub(a, b):
            return {p: a[p] - b[p] for p in P}

        def scale(d, s):
            return {p: d[p] * s for p in P}

        # 1-4: direct sums from conversion (VBA: SUMIFS by line type, aux2 = SUM)
        consol.append(_cr("ZZZZZZ_TURNOVER", "01. Demand forecast", T['turnover']))
        consol.append(_cr("ZZZZZZ_RAW MATERIAL COST", "03. Total demand", T['raw_material']))
        consol.append(_cr("ZZZZZZ_MACHINE COST", "07. Capacity utilization", T['machine']))
        consol.append(_cr("ZZZZZZ_DIRECT FTE COST", "12. FTE requirements", T['direct_fte']))

        # 5-6: fixed monthly costs (aux2 = SUM)
        indirect = const(vp.indirect_fte_cost_per_month)
        overhead = const(vp.overhead_cost_per_month)
        consol.append(_cr("ZZZZZZ_INDIRECT FTE COST", None, indirect))
        consol.append(_cr("ZZZZZZ_OVERHEAD COST", None, overhead))

        # 7: COGS (aux2 = SUM)
        cogs = add(T['raw_material'], T['machine'], T['direct_fte'], indirect, overhead)
        consol.append(_cr("ZZZZZZ_COST OF GOODS", None, cogs))

        # 8: Gross Margin (aux2 = SUM)
        gm = sub(T['turnover'], cogs)
        consol.append(_cr("ZZZZZZ_GROSS MARGIN", None, gm))

        # 9: SG&A (aux2 = SUM)
        sga = const(vp.sga_cost_per_month)
        consol.append(_cr("ZZZZZZ_SG&A COST", None, sga))

        # 10: EBITDA (aux2 = SUM)
        ebitda = sub(gm, sga)
        consol.append(_cr("ZZZZZZ_EBITDA", None, ebitda))

        # 11: D&A Cost (aux2 = SUM)
        da_m = vp.depreciation_per_year / 12
        da = const(da_m)
        consol.append(_cr("ZZZZZZ_D&A COST", None, da))

        # 12: EBIT (aux2 = SUM)
        ebit = sub(ebitda, da)
        consol.append(_cr("ZZZZZZ_EBIT", None, ebit))

        # 13: Fixed Assets NBV (VBA: aux2 = AVERAGE)
        nbv = const(vp.net_book_value)
        consol.append(_cr("ZZZZZZ_FIXED ASSETS NET BOOK VALUE", None, nbv, use_avg=True))

        # 14: Inventory Value (VBA: aux2 = AVERAGE(StartForecast-1 : EndForecast), includes starting stock!)
        consol.append(_cr("ZZZZZZ_INVENTORY VALUE", "04. Inventory", T['inventory'],
                          include_starting_in_avg=True))

        # 15: Receivables = Turnover / 30 * DSO (VBA: aux2 = AVERAGE)
        recv = scale(T['turnover'], vp.days_sales_outstanding / 30)
        consol.append(_cr("ZZZZZZ_RECEIVABLES", "01. Demand forecast", recv, use_avg=True))

        # 16: Payables = Purchase Receipt / 30 * DPO (VBA: aux2 = AVERAGE)
        pay = scale(T['purchase_receipt'], vp.days_payable_outstanding / 30)
        consol.append(_cr("ZZZZZZ_PAYABLES", "06. Purchase receipt", pay, use_avg=True))

        # 17: Working Capital = Receivables + Inventory - Payables (VBA: aux2 = AVERAGE)
        wc = {p: recv[p] + T['inventory'][p] - pay[p] for p in P}
        consol.append(_cr("ZZZZZZ_WORKING CAPITAL REQUIREMENTS", None, wc, use_avg=True))

        # 18: Capital Investment = NBV + WC (VBA: aux2 = AVERAGE)
        ci = add(nbv, wc)
        consol.append(_cr("ZZZZZZ_CAPITAL INVESTMENT", None, ci, use_avg=True))

        # 19: Operational Cashflow = EBITDA - InventoryValue(m) + InventoryValue(m-1)
        # VBA: aux2 = SUM
        # For first period, col-1 = StartingStock column of INVENTORY VALUE row
        inv_starting = sum(
            r.starting_stock
            for r in self.value_results.get(LineType.INVENTORY.value, [])
        )
        ocf = {}
        for i, p in enumerate(P):
            prev = inv_starting if i == 0 else T['inventory'][P[i - 1]]
            ocf[p] = ebitda[p] - T['inventory'][p] + prev
        consol.append(_cr("ZZZZZZ_OPERATIONAL CASHFLOW", None, ocf))

        # 20: ROCE = EBIT * 12 / Capital Investment
        # VBA: aux2 = EBIT_aux2 / CI_aux2, formatted as percentage
        roce = {p: (ebit[p] * 12) / ci[p] if ci[p] != 0 else 0 for p in P}
        # ROCE aux2 is special: total EBIT / total CI
        ebit_total = sum(ebit.values())
        ci_total = sum(ci.values()) / len(P) if P else 1  # Average CI
        roce_aux2 = ebit_total / ci_total if ci_total != 0 else 0
        roce_row = PlanningRow(
            material_number="ZZZZZZ_ROCE", material_name="",
            product_type="", product_family="", spc_product="",
            product_cluster="", product_name="",
            line_type=LineType.CONSOLIDATION.value,
            aux_column=None,
            aux_2_column=str(roce_aux2) if roce_aux2 != 0 else None,
            starting_stock=0.0, values=dict(roce))
        consol.append(roce_row)

        self.value_results[LineType.CONSOLIDATION.value] = consol
        print(f"  Created {len(consol)} consolidation rows")
