"""
S&OP Planning Engine - BOM Engine
BOM level management and dependent demand/requirements calculation.
"""

from typing import Dict, List, Set, Tuple
from collections import defaultdict
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class BOMEngine:
    """
    Manages BOM structure and creates dependent demand/requirements rows.
    Does NOT do the full BOM explosion itself - that's done level-by-level
    in the PlanningEngine orchestrator.
    """

    def __init__(self, data: DataLoader):
        self.data = data
        self.periods = data.periods
        # Parent -> list of (child, qty_per) from BOM
        # Include coproducts — they have NEGATIVE qty_per, creating negative dependent demand
        self.parent_children: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for b in data.bom:
            self.parent_children[b.parent_material].append(
                (b.component_material, b.quantity_per)
            )

    def get_max_level(self) -> int:
        return self.data.get_max_bom_level()

    def get_materials_at_level(self, level: int) -> List[str]:
        return self.data.get_materials_at_level(level)

    def compute_dependent_requirements(
        self, parent_mat: str, production_plan: Dict[str, float]
    ) -> Dict[str, Dict[str, float]]:
        """
        For a parent with a production plan, compute what each child needs.
        Returns: {child_mat -> {period -> qty}}
        """
        result: Dict[str, Dict[str, float]] = {}
        children = self.parent_children.get(parent_mat, [])
        for child, qty_per in children:
            child_demand = {}
            for period in self.periods:
                parent_qty = production_plan.get(period, 0.0)
                child_demand[period] = parent_qty * qty_per
            result[child] = child_demand
        return result

    def create_dependent_demand_rows(
        self, child_mat: str, demand_by_parent: Dict[str, Dict[str, float]]
    ) -> List[PlanningRow]:
        """
        Create Line 02 rows for a child material.
        One row per parent that contributes demand.
        Aux column = parent material number.
        """
        rows = []
        material = self.data.materials.get(child_mat)

        for parent_mat, period_data in demand_by_parent.items():
            # Generate row even if all zeros (Excel does this)
            rows.append(PlanningRow(
                    material_number=child_mat,
                    material_name=material.name if material else '',
                    product_type=material.product_type.value if material else '',
                    product_family=material.product_family if material else '',
                    spc_product=material.spc_product if material else '',
                    product_cluster=material.product_cluster if material else '',
                    product_name=material.product_name if material else '',
                    line_type=LineType.DEPENDENT_DEMAND.value,
                    aux_column=parent_mat,
                    values=dict(period_data)
                ))
        return rows

    def create_dependent_requirements_rows(
        self, parent_mat: str, children_demand: Dict[str, Dict[str, float]]
    ) -> List[PlanningRow]:
        """
        Create Line 08 rows for children of a parent.
        One row per child. Aux column = child material, aux2 = qty_per.
        Material number = parent material.
        
        WAIT - checking Excel: Line 08 rows have:
        - Material number = CHILD material
        - Aux column = component material number (e.g., 150000485)
        - Aux 2 = qty_per (e.g., 1.124)
        
        Actually looking more carefully at Excel reference:
        Material 600003822 has Line 08 with:
          material_number=600003822, aux=150000485, aux2=1.124
        
        So: material_number = PARENT, aux = CHILD (component), aux2 = qty_per
        """
        rows = []
        parent_material = self.data.materials.get(parent_mat)

        for child_mat, period_data in children_demand.items():
            # Generate row even if all zeros (Excel does this)
            # Find qty_per for this child from BOM
            qty_per = None
            for b in self.data.bom:
                if b.parent_material == parent_mat and b.component_material == child_mat:
                    qty_per = b.quantity_per
                    break

            rows.append(PlanningRow(
                    material_number=parent_mat,
                    material_name=parent_material.name if parent_material else '',
                    product_type=parent_material.product_type.value if parent_material else '',
                    product_family=parent_material.product_family if parent_material else '',
                    spc_product=parent_material.spc_product if parent_material else '',
                    product_cluster=parent_material.product_cluster if parent_material else '',
                    product_name=parent_material.product_name if parent_material else '',
                    line_type=LineType.DEPENDENT_REQUIREMENTS.value,
                    aux_column=child_mat,
                    aux_2_column=str(qty_per) if qty_per is not None else None,
                    values=dict(period_data)
                ))
        return rows

    def get_all_dependent_demand(self) -> Dict[str, Dict[str, float]]:
        """Not used in level-by-level approach."""
        return {}
