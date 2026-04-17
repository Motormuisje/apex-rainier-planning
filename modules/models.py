"""
S&OP Planning Engine - Data Models
All data structures used in the planning calculations.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime
from enum import Enum


class ProductType(Enum):
    RAW_MATERIAL = "Raw Material"
    BULK_PRODUCT = "Bulk Product"
    PACKAGED_PRODUCT = "Packaged Product"
    PACKAGING_GOODS = "Packaging Goods"
    OTHER = "Other"
    
    @classmethod
    def from_string(cls, value: str) -> 'ProductType':
        if not value:
            return cls.OTHER
        value_lower = value.lower()
        if "raw" in value_lower:
            return cls.RAW_MATERIAL
        elif "bulk" in value_lower:
            return cls.BULK_PRODUCT
        elif "packaged" in value_lower or "packed" in value_lower:
            return cls.PACKAGED_PRODUCT
        elif "packaging" in value_lower:
            return cls.PACKAGING_GOODS
        return cls.OTHER


class LineType(Enum):
    DEMAND_FORECAST = "01. Demand forecast"
    DEPENDENT_DEMAND = "02. Dependent demand"
    TOTAL_DEMAND = "03. Total demand"
    INVENTORY = "04. Inventory"
    MIN_TARGET_STOCK = "05. Minimum target stock"
    PRODUCTION_PLAN = "06. Production plan"
    PURCHASE_RECEIPT = "06. Purchase receipt"
    PURCHASE_PLAN = "07. Purchase plan"
    CAPACITY_UTILIZATION = "07. Capacity utilization"
    DEPENDENT_REQUIREMENTS = "08. Dependent requirements"
    AVAILABLE_CAPACITY = "09. Available capacity"
    UTILIZATION_RATE = "10. Utilization rate"
    SHIFT_AVAILABILITY = "11. Shift availability"
    FTE_REQUIREMENTS = "12. FTE requirements"
    CONSOLIDATION = "13. Consolidation"


class ShiftSystem(Enum):
    TWO_SHIFT = "2-shift system"
    THREE_SHIFT = "3-shift system"
    CONTINUOUS = "24/7 production"
    UNLIMITED = "Unlimited"


# Shift hours per month (based on FTE sheet)
SHIFT_HOURS = {
    ShiftSystem.TWO_SHIFT: 4160 / 12,      # ~347 hours/month
    ShiftSystem.THREE_SHIFT: 6240 / 12,    # 520 hours/month
    ShiftSystem.CONTINUOUS: 8760 / 12,     # 730 hours/month
    ShiftSystem.UNLIMITED: 999999,
}

# FTE hours per year
FTE_HOURS_PER_YEAR = 1492


@dataclass
class Material:
    material_number: str
    name: str
    product_type: ProductType
    product_family: str
    spc_product: Optional[str] = None
    product_cluster: Optional[str] = None
    product_name: Optional[str] = None
    production_line: Optional[str] = None
    grouped_production_line: Optional[str] = None
    mill_machine_group: Optional[str] = None
    packaging_machine_group: Optional[str] = None
    truck_operation: int = 0
    fte_requirements: float = 0.0
    ton_per_truck: Optional[float] = None
    time_per_truck: Optional[float] = None
    control_room: int = 0
    default_inventory_value: float = 0.0
    is_active: bool = True
    # Raw product-type string from Material Master (before enum conversion).
    # For truck/control-room materials VBA puts a line-type name here (e.g.
    # "01. Demand forecast") to tell TruckOperationsFormulas which line to SUMIFS over.
    product_type_raw: str = ''
    
    @property
    def is_purchased(self) -> bool:
        return self.product_type in [ProductType.RAW_MATERIAL, ProductType.PACKAGING_GOODS]
    
    @property
    def is_produced(self) -> bool:
        return self.product_type in [ProductType.BULK_PRODUCT, ProductType.PACKAGED_PRODUCT]


@dataclass
class BOMItem:
    plant: str
    parent_material: str
    parent_name: str
    component_material: str
    component_name: str
    quantity_per: float
    bom_header_quantity: float = 1.0
    is_coproduct: bool = False
    production_version: Optional[str] = None


@dataclass
class RoutingItem:
    plant: str
    material: str
    material_description: str
    work_center: str
    base_quantity: float
    standard_time: float
    production_version: Optional[str] = None
    
    @property
    def time_per_unit(self) -> float:
        if self.base_quantity > 0:
            return self.standard_time / self.base_quantity
        return 0.0


@dataclass
class Machine:
    machine_id: str
    machine_code: str
    name: str
    oee: float
    machine_group: Optional[str] = None
    availability_by_period: Dict[str, float] = field(default_factory=dict)
    shift_system: ShiftSystem = ShiftSystem.THREE_SHIFT
    shift_hours_override: Optional[float] = None

    def get_availability(self, period: str) -> float:
        return self.availability_by_period.get(period, 1.0)

    def get_available_hours(self, period: str) -> float:
        base_hours = (self.shift_hours_override
                      if self.shift_hours_override is not None
                      else SHIFT_HOURS.get(self.shift_system, 520))
        return base_hours * self.oee * self.get_availability(period)


@dataclass
class MachineGroup:
    group_id: str
    machine_codes: List[str]
    shift_system: ShiftSystem = ShiftSystem.THREE_SHIFT
    
    def get_shift_hours(self) -> float:
        return SHIFT_HOURS.get(self.shift_system, 520)


@dataclass
class SafetyStockConfig:
    material_number: str
    safety_stock: float
    lot_size: float
    strategic_stock: float = 0.0
    target_stock: float = 0.0
    use_moving_average: bool = False


@dataclass
class PlanningConfig:
    initial_date: datetime
    forecast_months: int = 12
    site: str = "NLX1"
    unlimited_capacity_machine: List[str] = field(default_factory=lambda: ['PBA99'])
    
    def get_periods(self) -> List[str]:
        periods = []
        for i in range(self.forecast_months):
            year = self.initial_date.year + (self.initial_date.month + i - 1) // 12
            month = ((self.initial_date.month + i - 1) % 12) + 1
            periods.append(f"{year}-{str(month).zfill(2)}")
        return periods


@dataclass
class PlanningRow:
    """Single row in the planning output."""
    material_number: str
    material_name: str
    product_type: str
    product_family: str
    spc_product: str
    product_cluster: str
    product_name: str
    line_type: str
    aux_column: Optional[str] = None
    aux_2_column: Optional[str] = None
    starting_stock: float = 0.0
    values: Dict[str, float] = field(default_factory=dict)
    manual_edits: Dict = field(default_factory=dict)  # { period: { original: float, new: float } }

    def get_value(self, period: str) -> float:
        return self.values.get(period, 0.0)
    
    def set_value(self, period: str, value: float):
        self.values[period] = value
    
    def to_dict(self) -> Dict:
        return {
            'material_number': self.material_number,
            'material_name': self.material_name,
            'product_type': self.product_type,
            'product_family': self.product_family,
            'spc_product': self.spc_product,
            'product_cluster': self.product_cluster,
            'product_name': self.product_name,
            'line_type': self.line_type,
            'aux_column': self.aux_column,
            'aux_2_column': self.aux_2_column,
            'starting_stock': self.starting_stock,
            'values': self.values,
            'manual_edits': self.manual_edits,
        }


@dataclass
class ValuationParameters:
    """Financial parameters for value calculations."""
    direct_fte_cost_per_month: float  # Cost number 1
    indirect_fte_cost_per_month: float  # Cost number 2
    overhead_cost_per_month: float  # Cost number 3
    sga_cost_per_month: float  # Cost number 4
    depreciation_per_year: float  # Cost number 5
    net_book_value: float  # Cost number 6
    days_sales_outstanding: int  # Cost number 7
    days_payable_outstanding: int  # Cost number 8


@dataclass
class SalesPriceItem:
    """Average sales price for a product."""
    plant_code: str
    product_id: str
    volume_2025: float
    ex_works_revenue: float
    
    @property
    def price_per_unit(self) -> float:
        if self.volume_2025 > 0:
            return self.ex_works_revenue / self.volume_2025
        return 0.0


@dataclass
class RawMaterialCost:
    """Cost per unit for raw materials."""
    plant_code: str
    product_code: str
    product_name: str
    cost_per_unit: float


@dataclass
class MachineCost:
    """Machine hour cost."""
    plant_code: str
    cost_center: str
    variable_cost_per_hour: float  # Activity type 50
