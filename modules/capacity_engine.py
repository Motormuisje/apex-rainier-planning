"""
S&OP Planning Engine - Capacity Engine

VBA Logic (from PDF):
- Line 07 Cap Util: AUX1=work_center, AUX2=BaseQty/StdValue. Values=ProductionPlan/AUX2
  Rows generated for ALL routing entries (not just OEE machines).
  Machine-level rows (Z_MACHxx): AUX1=group, AUX2=OEE. Values=SUM of material-level rows.
  Group-level rows: SUM of machine-level rows in group.
- Line 09 Avail Cap: AUX1=shift_hours, AUX2=avg_availability. Values=availability factor per period.
- Line 10 Util Rate: = Cap Util / (Shift Hours × Availability). NO OEE in denominator!
- Line 11 Shift Avail: 520 hours per group.
- Line 12 FTE: hours / (FTE_hours_per_year / 12)
"""

from typing import Dict, List
from collections import defaultdict
from modules.models import PlanningRow, LineType, ShiftSystem, ProductType, SHIFT_HOURS, FTE_HOURS_PER_YEAR
from modules.data_loader import DataLoader


class CapacityEngine:
    def __init__(self, data: DataLoader, production_plan: Dict[str, Dict[str, float]],
                 all_line_data: Dict[str, Dict[str, Dict[str, float]]] = None):
        self.data = data
        self.periods = data.periods
        self.production_plan = production_plan
        # all_line_data: {line_type: {mat_num: {period: value}}}
        # Replaces the old demand_forecasts parameter; contains every line type so
        # _calculate_truck_cap_util can pick the right source via product_type_raw.
        self.all_line_data: Dict[str, Dict[str, Dict[str, float]]] = all_line_data or {}
        # Convenience alias kept for any legacy callers
        self.demand_forecasts = self.all_line_data.get(LineType.DEMAND_FORECAST.value, {})

        self.shift_hours_lookup: Dict[str, float] = data.shift_hours
        self.machine_hours_used: Dict[str, Dict[str, float]] = {}
        self.rows_07_cap: List[PlanningRow] = []
        self.rows_09: List[PlanningRow] = []
        self.rows_10: List[PlanningRow] = []
        self.rows_11: List[PlanningRow] = []
        self.rows_12: List[PlanningRow] = []
        # Per-group monthly shift hours, populated by _calculate_shift_availability
        self.group_monthly_shift_hours: Dict[str, float] = {}

        # All groups from material master (including empty ones)
        self.all_groups: List[str] = []
        self.mill_groups: set = set()
        self.packaging_groups: set = set()
        for mn in sorted(self.data.materials.keys()):
            if mn.startswith('ZZ') and not mn.startswith('ZZZZ'):
                self.all_groups.append(mn)
                mat = self.data.materials[mn]
                if str(mat.mill_machine_group or '').strip() == '1':
                    self.mill_groups.add(mn)
                elif str(mat.packaging_machine_group or '').strip() == '1':
                    self.packaging_groups.add(mn)

    def calculate(self) -> Dict[str, List[PlanningRow]]:
        self._calculate_capacity_utilization()
        # Apply site-specific exceptions BEFORE utilization rate so that
        # Line 10 (utilization rate) uses the corrected cap-util hours.
        self._apply_site_exceptions()
        self._calculate_shift_availability()
        self._calculate_available_capacity()
        self._calculate_utilization_rate()
        self._calculate_fte_requirements()

        return {
            LineType.CAPACITY_UTILIZATION.value: self.rows_07_cap,
            LineType.AVAILABLE_CAPACITY.value: self.rows_09,
            LineType.UTILIZATION_RATE.value: self.rows_10,
            LineType.SHIFT_AVAILABILITY.value: self.rows_11,
            LineType.FTE_REQUIREMENTS.value: self.rows_12,
        }

    def _apply_site_exceptions(self):
        """VBA Exceptions_GTB(): site-specific overrides for NLI1 only."""
        if not self.data.config or self.data.config.site != 'NLI1':
            return

        # ── Exception 1: PML18 capacity utilization ──────────────────────────
        # Normal: PML18 hours = SUM of all material hours on PML18 / OEE
        # Exception: PML18 hours = SUM(all except B30,B32,B150) + MAX(B30+B32, B150)
        # where B30=500000956, B32=500000955, B150=500000969
        B30, B32, B150 = '500000956', '500000955', '500000969'
        special_mats = {B30, B32, B150}

        pml18_machine = self.data.machines.get('PML18')
        if pml18_machine:
            oee = pml18_machine.oee if pml18_machine.oee > 0 else 1.0
            # Recompute raw hours: separate special from normal materials
            new_values = {}
            for p in self.periods:
                normal_h = 0.0
                b30_h = 0.0
                b32_h = 0.0
                b150_h = 0.0
                for mat_num, plan_data in self.production_plan.items():
                    # Find routing hours for this material on PML18
                    routings = self.data.get_all_routings(mat_num)
                    for routing in routings:
                        if routing.work_center != 'PML18':
                            continue
                        base_qty = routing.base_quantity if routing.base_quantity > 0 else 1.0
                        std_time = routing.standard_time if routing.standard_time > 0 else 1.0
                        aux2_val = base_qty / std_time
                        prod_qty = plan_data.get(p, 0.0)
                        h = prod_qty / aux2_val if aux2_val > 0 and prod_qty > 0 else 0.0
                        if mat_num == B30:
                            b30_h += h
                        elif mat_num == B32:
                            b32_h += h
                        elif mat_num == B150:
                            b150_h += h
                        else:
                            normal_h += h
                raw_h = normal_h + max(b30_h + b32_h, b150_h)
                new_values[p] = raw_h / oee if oee > 0 and raw_h > 0 else raw_h

            # Update the already-emitted PML18 machine-level row in rows_07_cap
            mid = pml18_machine.machine_id
            for row in self.rows_07_cap:
                if row.material_number == mid and row.material_name == 'PML18':
                    row.values = new_values
                    # Also update oee_adjusted for group aggregation consistency
                    self.machine_hours_oee_adjusted['PML18'] = new_values
                    break

        # ── Exception 2: B15 production plan ─────────────────────────────────
        # B15 (600004811) need is reduced by B4010 (600004831) production.
        # This affects the inventory engine output (Lines 03/04/06), not capacity rows.
        # NOTE: This exception is applied at the InventoryEngine level when planning_engine
        # processes NLI1. CapacityEngine reflects the corrected production_plan it receives
        # automatically — no additional action needed here.
        pass

    def _get_shift_hours_for_machine(self, machine_code: str) -> float:
        """Return monthly shift hours for a machine, driven by FTE sheet values."""
        _KEY = {
            ShiftSystem.TWO_SHIFT: '2-shift system',
            ShiftSystem.THREE_SHIFT: '3-shift system',
            ShiftSystem.CONTINUOUS: '24/7 production',
        }
        machine = self.data.machines.get(machine_code)
        if not machine or machine.shift_system == ShiftSystem.UNLIMITED:
            return self.shift_hours_lookup.get('2-shift system', 347.0)
        key = _KEY.get(machine.shift_system, '2-shift system')
        return self.shift_hours_lookup.get(key, 520.0)

    def _get_shift_system_name(self, machine_code: str) -> str:
        """Return the shift-system label for a machine (for aux_column display)."""
        _KEY = {
            ShiftSystem.TWO_SHIFT: '2-shift system',
            ShiftSystem.THREE_SHIFT: '3-shift system',
            ShiftSystem.CONTINUOUS: '24/7 production',
            ShiftSystem.UNLIMITED: 'Unlimited',
        }
        machine = self.data.machines.get(machine_code)
        if not machine:
            return '2-shift system'
        return _KEY.get(machine.shift_system, '2-shift system')

    def _calculate_capacity_utilization(self):
        """Line 07: Capacity Utilization.
        VBA: CapUtil = ProductionPlan / AUX2 where AUX2 = BaseQty/StdValue
        Generate rows for ALL routing entries, not just OEE machines.
        """
        print("  [07] Calculating Capacity Utilization...")

        # Initialize machine hours for OEE machines
        for mc in self.data.machines:
            self.machine_hours_used[mc] = {p: 0.0 for p in self.periods}

        # 1. Material-level rows: one per (material, routing entry)
        for mat_num, plan_data in self.production_plan.items():
            routings = sorted(self.data.get_all_routings(mat_num), key=lambda r: r.work_center)
            if not routings:
                continue
            material = self.data.materials.get(mat_num)
            if not material:
                continue

            for routing in routings:
                wc = routing.work_center
                base_qty = routing.base_quantity if routing.base_quantity > 0 else 1.0
                std_time = routing.standard_time

                # AUX2 = BaseQty / StdValue (throughput rate)
                if std_time == 0:
                    aux2_val = 1.0  # match VBA behavior directly
                else:
                    aux2_val = base_qty / std_time

                hours_data = {}
                for period in self.periods:
                    prod_qty = plan_data.get(period, 0.0)
                    if prod_qty > 0:
                        # CapUtil = ProductionPlan / AUX2
                        hours = prod_qty / aux2_val
                        hours_data[period] = hours
                        # Track hours for OEE machines
                        if wc in self.machine_hours_used:
                            self.machine_hours_used[wc][period] += hours
                    else:
                        hours_data[period] = 0.0

                # VBA generates cap util rows for ALL routing entries, even if production is 0
                self.rows_07_cap.append(PlanningRow(
                        material_number=mat_num,
                        material_name=material.name,
                        product_type=material.product_type.value,
                        product_family=material.product_family,
                        spc_product=material.spc_product or '',
                        product_cluster=material.product_cluster or '',
                        product_name=material.product_name or '',
                        line_type=LineType.CAPACITY_UTILIZATION.value,
                        aux_column=wc,
                        aux_2_column=str(aux2_val),
                        values=hours_data.copy()
                    ))

        # === VBA DeleteDoubleProcessRowsPackagedMaterials ===
        # Remove duplicate cap util rows for packaged materials sharing work centers
        # with BOM-related bulk materials, unless the work center is in the allowed list.
        _ALLOWED_WCS = frozenset([
            "TBMA A + C", "BB PE24", "BB H&B", "BB PE20", "BB PE25",
            "ZVM07", "PE20", "PE25",
        ])
        _bulk_for_packaged: Dict[str, set] = defaultdict(set)
        for b in self.data.bom:
            p_mat = self.data.materials.get(b.parent_material)
            c_mat = self.data.materials.get(b.component_material)
            if not p_mat or not c_mat:
                continue
            if p_mat.product_type == ProductType.PACKAGED_PRODUCT and \
               c_mat.product_type == ProductType.BULK_PRODUCT:
                _bulk_for_packaged[b.parent_material].add(b.component_material)
            elif p_mat.product_type == ProductType.BULK_PRODUCT and \
                 c_mat.product_type == ProductType.PACKAGED_PRODUCT:
                _bulk_for_packaged[b.component_material].add(b.parent_material)
        _bulk_wcs: Dict[str, set] = defaultdict(set)
        for row in self.rows_07_cap:
            m = self.data.materials.get(row.material_number)
            if m and m.product_type == ProductType.BULK_PRODUCT and row.aux_column:
                _bulk_wcs[row.material_number].add(row.aux_column)
        _remove_indices: set = set()
        for idx, row in enumerate(self.rows_07_cap):
            if row.material_number not in _bulk_for_packaged:
                continue
            wc = row.aux_column
            if not wc or wc in _ALLOWED_WCS:
                continue
            for bulk_mat in _bulk_for_packaged[row.material_number]:
                if wc in _bulk_wcs.get(bulk_mat, set()):
                    _remove_indices.add(idx)
                    break
        if _remove_indices:
            for idx in _remove_indices:
                row = self.rows_07_cap[idx]
                wc = row.aux_column
                if wc in self.machine_hours_used:
                    for p in self.periods:
                        self.machine_hours_used[wc][p] -= row.values.get(p, 0.0)
            self.rows_07_cap = [
                r for i, r in enumerate(self.rows_07_cap) if i not in _remove_indices
            ]
            print(f"       -> Removed {len(_remove_indices)} duplicate packaged material rows")

        # 2. Machine-level aggregation rows (Z_MACHxx)
        # VBA: machine hours = sum(material hours) / OEE
        # OEE < 1 means the machine needs MORE hours than pure production time
        for mc_code, machine in self.data.machines.items():
            mid = machine.machine_id
            group = machine.machine_group
            oee = machine.oee
            raw_hours = self.machine_hours_used.get(mc_code, {p: 0.0 for p in self.periods})
            # Divide by OEE: actual machine occupation = production hours / OEE
            machine_values = {}
            for p in self.periods:
                h = raw_hours.get(p, 0.0)
                machine_values[p] = h / oee if oee > 0 and h > 0 else h
            self.rows_07_cap.append(PlanningRow(
                material_number=mid,
                material_name=mc_code,
                product_type='Machine', product_family=group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.CAPACITY_UTILIZATION.value,
                aux_column=group if group else None,
                aux_2_column=str(oee),
                values=machine_values
            ))

        # 3. Group-level aggregation rows (sum of OEE-adjusted machine hours)
        # Build OEE-adjusted hours per machine for reuse
        self.machine_hours_oee_adjusted: Dict[str, Dict[str, float]] = {}
        for mc_code, machine in self.data.machines.items():
            oee = machine.oee
            raw = self.machine_hours_used.get(mc_code, {p: 0.0 for p in self.periods})
            self.machine_hours_oee_adjusted[mc_code] = {
                p: (raw.get(p, 0.0) / oee if oee > 0 and raw.get(p, 0.0) > 0 else raw.get(p, 0.0))
                for p in self.periods
            }

        # 2.5. Grouped production line rows (VBA: ProdLineCapacityUtilization grouped logic)
        # Materials with grouped_production_line == '1' have a compound production_line like
        # 'PML01-PML02-PML03'. AVERAGE the OEE-adjusted hours of component machines and emit
        # a single grouped row. Component machines are excluded from group-level SUM.
        grouped_machines: set = set()
        compound_names_seen: set = set()
        for mat_num, material in self.data.materials.items():
            if str(material.grouped_production_line or '').strip() == '1' and material.production_line:
                compound_name = material.production_line.strip()
                if '-' not in compound_name or compound_name in compound_names_seen:
                    continue
                compound_names_seen.add(compound_name)
                components = [c.strip() for c in compound_name.split('-') if c.strip()]
                for c in components:
                    grouped_machines.add(c)
                # Inherit machine group from the first known component machine
                group = next(
                    (self.data.machines[c].machine_group for c in components if c in self.data.machines),
                    None
                )
                # Average OEE-adjusted hours across components per period
                grouped_values = {}
                for p in self.periods:
                    comp_hours = [
                        self.machine_hours_oee_adjusted[c].get(p, 0.0)
                        if c in self.machine_hours_oee_adjusted else 0.0
                        for c in components
                    ]
                    grouped_values[p] = sum(comp_hours) / len(comp_hours) if comp_hours else 0.0
                self.rows_07_cap.append(PlanningRow(
                    material_number=mat_num,
                    material_name=compound_name,
                    product_type='Machine', product_family=group or '',
                    spc_product='', product_cluster='', product_name=compound_name,
                    line_type=LineType.CAPACITY_UTILIZATION.value,
                    aux_column=group if group else None,
                    aux_2_column=None,
                    values=grouped_values
                ))

        # Track per-machine OEE-adjusted hours per group for MAX/SUM aggregation
        # group_machine_hours[grp][mc_code][period] = oee_adjusted_hours
        group_machine_hours: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)
        for mc_code, machine in self.data.machines.items():
            grp = machine.machine_group
            # Skip component machines that belong to a grouped production line —
            # their contribution is captured by the compound averaged row below.
            if grp and mc_code not in grouped_machines:
                group_machine_hours[grp][mc_code] = self.machine_hours_oee_adjusted[mc_code]

        # VBA: after ProdLineCapacityUtilization clears AUX1 of component machines,
        # the compound averaged row still has AUX1 = machine_group and is therefore
        # included in the group-level SUMIFS/MAXIFS.  Mirror that here by adding the
        # averaged grouped values to group_machine_hours under a synthetic key.
        _compound_names_added: set = set()
        for material in self.data.materials.values():
            if str(material.grouped_production_line or '').strip() != '1' or not material.production_line:
                continue
            compound_name = material.production_line.strip()
            if '-' not in compound_name or compound_name in _compound_names_added:
                continue
            _compound_names_added.add(compound_name)
            components = [c.strip() for c in compound_name.split('-') if c.strip()]
            grp = next(
                (self.data.machines[c].machine_group for c in components if c in self.data.machines),
                None
            )
            if not grp:
                continue
            averaged = {}
            for p in self.periods:
                comp_hours = [
                    self.machine_hours_oee_adjusted[c].get(p, 0.0)
                    if c in self.machine_hours_oee_adjusted else 0.0
                    for c in components
                ]
                averaged[p] = sum(comp_hours) / len(comp_hours) if comp_hours else 0.0
            group_machine_hours[grp][f'__compound_{compound_name}'] = averaged

        # Store aggregated group hours for reuse in _calculate_fte_requirements
        self.group_hours_aggregated: Dict[str, Dict[str, float]] = {}

        for grp_id in self.all_groups:
            machines_in_grp = group_machine_hours.get(grp_id, {})
            if grp_id in self.mill_groups:
                # VBA MillGroupFormulas: MAXIFS — use the highest machine value per period
                hours = {
                    p: max((m.get(p, 0.0) for m in machines_in_grp.values()), default=0.0)
                    for p in self.periods
                }
            else:
                # VBA PackGroupFormulas / default: SUMIFS — sum all machines per period
                hours = {p: 0.0 for p in self.periods}
                for mc_vals in machines_in_grp.values():
                    for p in self.periods:
                        hours[p] += mc_vals.get(p, 0.0)
            self.group_hours_aggregated[grp_id] = hours
            self.rows_07_cap.append(PlanningRow(
                material_number=grp_id,
                material_name='', product_type='Machine Group',
                product_family='', spc_product='', product_cluster='', product_name='',
                line_type=LineType.CAPACITY_UTILIZATION.value,
                values=dict(hours)
            ))

        # 4. Truck capacity utilization
        self._calculate_truck_cap_util()

        # 5. Control room capacity utilization
        self._calculate_control_room_cap_util()

        print(f"       -> {len(self.rows_07_cap)} capacity utilization rows")

    def _compute_truck_hours(self) -> Dict[str, Dict[str, float]]:
        """VBA TruckOperationsFormulas — data-driven truck-hour calculation.

        The VBA formula is:
          SUMIFS(values[rows 2..ProdLineFirstRow-1],
                 col_C_range,       truck_col_B,   <- product_type of material rows == truck material_name
                 lineType_range,    truck_col_C)   <- line_type of material rows  == truck product_type_raw
          * Aux1 / Aux2

        Python equivalent:
          - truck col B = tm.name          (e.g. "Bulk Product")
          - truck col C = tm.product_type_raw (e.g. "01. Demand forecast")
          - source rows  = all_line_data[product_type_raw]
          - filtered by  material.product_type.value == tm.name
        """
        truck_hours: Dict[str, Dict[str, float]] = {}
        for truck_mat_id, tm in self.data.materials.items():
            if not tm.ton_per_truck or tm.ton_per_truck <= 0:
                continue
            if not tm.time_per_truck:
                continue

            # product_type filter: truck's Material Name (col B on planning sheet)
            product_type_filter = tm.name
            # line_type  filter: truck's raw product-type string (col C on planning sheet)
            line_type_filter = tm.product_type_raw

            source_data = self.all_line_data.get(line_type_filter)
            if not source_data:
                # Fallback: if the truck's product_type_raw doesn't match any line
                # type (e.g. still blank / "Other"), use Line 01 demand forecast.
                source_data = self.all_line_data.get(LineType.DEMAND_FORECAST.value, {})

            truck_hours[truck_mat_id] = {p: 0.0 for p in self.periods}
            for mat_num, plan_data in source_data.items():
                material = self.data.materials.get(mat_num)
                if not material:
                    continue
                if material.product_type.value != product_type_filter:
                    continue
                for period in self.periods:
                    qty = plan_data.get(period, 0.0)
                    if qty > 0:
                        truck_hours[truck_mat_id][period] += (qty / tm.ton_per_truck) * tm.time_per_truck

        return truck_hours

    def _calculate_truck_cap_util(self):
        self._truck_hours_cache = self._compute_truck_hours()
        for truck_mat_id, hours in self._truck_hours_cache.items():
            tm = self.data.materials.get(truck_mat_id)
            if tm:
                self.rows_07_cap.append(PlanningRow(
                    material_number=truck_mat_id, material_name=tm.name,
                    product_type='Machine Group', product_family='',
                    spc_product='', product_cluster='', product_name='',
                    line_type=LineType.CAPACITY_UTILIZATION.value,
                    aux_column=str(tm.time_per_truck) if tm.time_per_truck else None,
                    aux_2_column=str(tm.ton_per_truck) if tm.ton_per_truck else None,
                    values=dict(hours)
                ))

    def _calculate_control_room_cap_util(self):
        cr_mat = self.data.materials.get('ZZZZZ_CONTROLROOM')
        if cr_mat:
            shift_hours = self.shift_hours_lookup.get('3-shift system', 520.0)
            self.rows_07_cap.append(PlanningRow(
                material_number='ZZZZZ_CONTROLROOM',
                material_name='Control room operators',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.CAPACITY_UTILIZATION.value,
                aux_column='3-shift system',
                aux_2_column=str(shift_hours * 12),
                values={p: shift_hours for p in self.periods}
            ))

    def _calculate_shift_availability(self):
        """Line 11: Shift Availability per machine group.

        NOTE: VBA MachineGroupShiftAvailability() assigns the same shift system to ALL groups,
        defaulting to FTE sheet row 4 (second dropdown option). Python overrides per-machine
        derivation here to match that behaviour so test comparisons against the Excel reference
        are accurate. This is a known deviation from the semantically correct approach (deriving
        shift system per group from machine configuration) and should be revisited when the
        per-group shift system selection is properly modelled.
        """
        print("  [11] Calculating Shift Availability...")
        # VBA default: all groups use the same shift system (FTE sheet row 4 = second option)
        grp_shift_name = getattr(self.data, 'default_shift_name', '3-shift system')
        grp_shift_hours = self.shift_hours_lookup.get(grp_shift_name, 520.0)

        for group_id in self.all_groups:
            group = self.data.machine_groups.get(group_id)
            machine_names = []
            if group:
                for mc in group.machine_codes:
                    m = self.data.machines.get(mc)
                    if m:
                        machine_names.append(m.machine_code)
            annual_hours = grp_shift_hours * 12
            self.group_monthly_shift_hours[group_id] = grp_shift_hours
            aux2_str = (str(int(annual_hours))
                        if annual_hours == int(annual_hours)
                        else str(round(annual_hours, 2)))
            self.rows_11.append(PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names) if machine_names else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.SHIFT_AVAILABILITY.value,
                aux_column=grp_shift_name,
                aux_2_column=aux2_str,
                values={p: grp_shift_hours for p in self.periods}
            ))
        print(f"       -> {len(self.rows_11)} shift availability rows")

    def _calculate_available_capacity(self):
        """Line 09: Available Capacity.
        AUX1 = shift hours or 'Unlimited'. AUX2 = average OEE.
        Values = availability factor from OEE sheet.
        """
        print("  [09] Calculating Available Capacity...")
        for machine_code, machine in self.data.machines.items():
            avail_data = {}
            if machine.shift_system == ShiftSystem.UNLIMITED:
                avail_data = {p: 1.0 for p in self.periods}
                aux_col = 'Unlimited'
            else:
                for period in self.periods:
                    avail_data[period] = machine.get_availability(period)
                shift_h = self._get_shift_hours_for_machine(machine_code)
                aux_col = str(int(shift_h)) if shift_h == int(shift_h) else str(round(shift_h, 1))
            vals = list(avail_data.values())
            avg_avail = sum(vals) / len(vals) if vals else 1.0
            self.rows_09.append(PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine', product_family=machine.machine_group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.AVAILABLE_CAPACITY.value,
                aux_column=aux_col,
                aux_2_column=str(avg_avail),
                values=avail_data.copy()
            ))
        print(f"       -> {len(self.rows_09)} available capacity rows")

    def _calculate_utilization_rate(self):
        """Line 10: Utilization Rate.
        VBA: = Capacity Utilization / (Shift Hours × Machine Availability)
        NO OEE in denominator!
        """
        print("  [10] Calculating Utilization Rate...")
        for machine_code, machine in self.data.machines.items():
            rate_data = {}
            used = self.machine_hours_oee_adjusted.get(machine_code, {})
            machine_shift_hours = self._get_shift_hours_for_machine(machine_code)
            for period in self.periods:
                if machine.shift_system == ShiftSystem.UNLIMITED:
                    rate_data[period] = 1.0
                else:
                    avail_factor = machine.get_availability(period)
                    # VBA: cap_util / (shift_hours * availability) - NO OEE!
                    available = machine_shift_hours * avail_factor
                    used_hours = used.get(period, 0.0)
                    rate_data[period] = used_hours / available if available > 0 else 0.0
            vals = list(rate_data.values())
            avg_rate = sum(vals) / len(vals) if vals else 0.0
            self.rows_10.append(PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine', product_family=machine.machine_group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.UTILIZATION_RATE.value,
                aux_column=str(avg_rate),
                values=rate_data.copy()
            ))
        print(f"       -> {len(self.rows_10)} utilization rate rows")

    def _calculate_fte_requirements(self):
        """Line 12: FTE Requirements per group + trucks + control room.
        Mill groups: FTE based on MAX-aggregated cap util (VBA MillGroupFormulas L12).
        Packaging/default groups: FTE based on SUM-aggregated cap util (VBA PackGroupFormulas L12).
        FTE = cap_util_hours / (fte_hours_per_year / 12)
        """
        print("  [12] Calculating FTE Requirements...")
        fte_hours_per_year = self.data.fte_hours_per_year  # e.g. 1492 hours/year per FTE
        fte_monthly_hours = fte_hours_per_year / 12        # hours one FTE works per month
        for group_id in self.all_groups:
            hours = self.group_hours_aggregated.get(group_id, {p: 0.0 for p in self.periods})
            group = self.data.machine_groups.get(group_id)
            machine_names = []
            if group:
                for mc in group.machine_codes:
                    m = self.data.machines.get(mc)
                    if m:
                        machine_names.append(m.machine_code)
            mat = self.data.materials.get(group_id)
            fte_coeff = mat.fte_requirements if (mat and mat.fte_requirements > 0) else 1.0
            aux2_str = str(round(fte_hours_per_year, 2)) if fte_hours_per_year != int(fte_hours_per_year) else str(int(fte_hours_per_year))
            fte_data = {p: hours[p] * fte_coeff / fte_monthly_hours if fte_monthly_hours > 0 else 0.0 for p in self.periods}
            self.rows_12.append(PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names) if machine_names else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column=str(fte_coeff),
                aux_2_column=aux2_str,
                values=fte_data.copy()
            ))
        self._calculate_truck_fte()
        self._calculate_control_room_fte()
        print(f"       -> {len(self.rows_12)} FTE requirement rows")

    def _calculate_truck_fte(self):
        # Reuse hours already computed by _calculate_truck_cap_util
        truck_hours_cache = getattr(self, '_truck_hours_cache', {})
        if not truck_hours_cache:
            truck_hours_cache = self._compute_truck_hours()
        fte_hours_per_year = self.data.fte_hours_per_year
        fte_monthly_hours = fte_hours_per_year / 12
        aux2_str = str(round(fte_hours_per_year, 2)) if fte_hours_per_year != int(fte_hours_per_year) else str(int(fte_hours_per_year))
        for truck_mat_id, hours in truck_hours_cache.items():
            tm = self.data.materials.get(truck_mat_id)
            fte_coeff = tm.fte_requirements if (tm and tm.fte_requirements > 0) else 1.0
            fte_data = {p: hours[p] * fte_coeff / fte_monthly_hours if fte_monthly_hours > 0 else 0.0 for p in self.periods}
            self.rows_12.append(PlanningRow(
                material_number=truck_mat_id, material_name=tm.name if tm else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column=str(fte_coeff), aux_2_column=aux2_str, values=fte_data.copy()
            ))

    def _calculate_control_room_fte(self):
        # VBA ControlRoomFormulas FTE: cap_util_monthly * fte_coeff / (fte_hours_per_year / 12)
        mat = self.data.materials.get('ZZZZZ_CONTROLROOM')
        if not mat:
            return
        shift_hours = self.shift_hours_lookup.get('3-shift system', 520.0)
        fte_hours_per_year = self.data.fte_hours_per_year
        fte_monthly_hours = fte_hours_per_year / 12
        aux2_str = str(round(fte_hours_per_year, 2)) if fte_hours_per_year != int(fte_hours_per_year) else str(int(fte_hours_per_year))
        fte_coeff = mat.fte_requirements if mat.fte_requirements > 0 else 1.0
        fte_val = shift_hours * fte_coeff / fte_monthly_hours if fte_monthly_hours > 0 else 0.0
        self.rows_12.append(PlanningRow(
            material_number='ZZZZZ_CONTROLROOM',
            material_name='Control room operators',
            product_type='Machine Group', product_family='',
            spc_product='', product_cluster='', product_name='',
            line_type=LineType.FTE_REQUIREMENTS.value,
            aux_column=str(fte_coeff), aux_2_column=aux2_str, values={p: fte_val for p in self.periods}
        ))
