"""Helpers for rebuilding clean PlanningEngine instances for UI sessions."""

from typing import Callable

from modules.planning_engine import PlanningEngine
from ui.parsers import format_purchased_and_produced


def get_config_overrides(global_config: dict) -> dict:
    """Build config_overrides dict from global config for use in PlanningEngine."""
    ov = {}
    if global_config.get('site'):
        ov['site'] = global_config['site']
    if global_config.get('forecast_months'):
        ov['forecast_months'] = int(global_config['forecast_months'])
    if global_config.get('unlimited_machines'):
        ov['unlimited_machines'] = global_config['unlimited_machines']
    if global_config.get('purchased_and_produced'):
        ov['purchased_and_produced'] = global_config['purchased_and_produced']
    vp = global_config.get('valuation_params')
    if vp and any(float(v or 0) != 0 for v in vp.values()):
        ov['valuation_params'] = vp
    return ov


def get_session_config_overrides(sess: dict | None, global_config: dict) -> dict:
    """Build config_overrides for a session-specific engine rebuild."""
    ov = get_config_overrides(global_config)
    if sess is None:
        return ov
    engine_data = getattr(sess.get('engine'), 'data', None)
    vp_obj = getattr(engine_data, 'valuation_params', None)
    if vp_obj is not None:
        ov['valuation_params'] = {
            '1': vp_obj.direct_fte_cost_per_month,
            '2': vp_obj.indirect_fte_cost_per_month,
            '3': vp_obj.overhead_cost_per_month,
            '4': vp_obj.sga_cost_per_month,
            '5': vp_obj.depreciation_per_year,
            '6': vp_obj.net_book_value,
            '7': vp_obj.days_sales_outstanding,
            '8': vp_obj.days_payable_outstanding,
        }
    elif sess.get('valuation_params'):
        ov['valuation_params'] = sess['valuation_params']

    pap = getattr(engine_data, 'purchased_and_produced', None)
    if pap is not None:
        ov['purchased_and_produced'] = format_purchased_and_produced(pap)
    return ov


def build_clean_engine_for_session(
    sess: dict,
    global_config: dict,
    params: dict | None = None,
) -> PlanningEngine | None:
    params = params or sess.get('parameters') or {}
    if not params:
        return None
    months_forecast = int(params.get('months_forecast', 12) or 12)
    if global_config.get('forecast_months'):
        months_forecast = int(global_config.get('forecast_months') or months_forecast)
    engine = PlanningEngine(
        sess['file_path'],
        planning_month=params.get('planning_month'),
        months_actuals=int(params.get('months_actuals', 0) or 0),
        months_forecast=months_forecast,
        extract_files=sess.get('extract_files'),
        config_overrides=get_session_config_overrides(sess, global_config),
    )
    engine.run()
    return engine


def install_clean_engine_baseline(
    sess: dict,
    engine,
    snapshot_engine_state: Callable[[object], dict],
    clear_machine_overrides: bool = True,
) -> None:
    sess['reset_baseline'] = snapshot_engine_state(engine)
    # A fresh calculate invalidates stale machine undo history.
    sess['machine_undo'] = []
    if clear_machine_overrides:
        sess['machine_overrides'] = {}
