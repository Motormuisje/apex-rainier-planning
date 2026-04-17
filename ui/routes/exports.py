"""Planning export and month-over-month routes."""

from datetime import datetime
from typing import Callable

from flask import Blueprint, jsonify, request, send_file

from modules.database_exporter import DatabaseExporter
from modules.mom_comparison_engine import MoMComparisonEngine


def create_exports_blueprint(
    get_active: Callable[[], tuple],
    export_dir: Callable[[], object],
    cycle_manager: Callable[[], object],
    apply_edit_highlights: Callable[[str, object], None],
) -> Blueprint:
    bp = Blueprint('exports', __name__)

    @bp.route('/api/export')
    def export():
        _, current_engine = get_active()

        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        out_dir = export_dir()
        out_dir.mkdir(exist_ok=True)
        export_path = out_dir / f'SOP_Python_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'

        inventory_quality_engine = None
        try:
            from modules.inventory_quality_engine import InventoryQualityEngine
            inventory_quality_engine = InventoryQualityEngine(
                current_engine.data,
                current_engine.results,
                current_engine.value_results,
            )
        except Exception:
            pass

        previous_df = None
        try:
            cm = cycle_manager()
            if cm.has_previous_cycle():
                previous_df = cm.load_previous_cycle()
                if previous_df.empty:
                    previous_df = None
                    print('[export] Previous cycle loaded but empty - MoM sheet skipped')
                else:
                    print(f'[export] Previous cycle loaded ({len(previous_df)} rows) - MoM sheet will be included')
            else:
                print('[export] No previous cycle on disk - MoM sheet skipped (will be available after next calculation)')
        except Exception as exc:
            print(f'[export] Could not load previous cycle: {exc}')

        current_engine.to_excel_with_values(
            str(export_path),
            inventory_quality_engine=inventory_quality_engine,
            previous_cycle_df=previous_df,
        )
        apply_edit_highlights(str(export_path), current_engine)

        return send_file(str(export_path), as_attachment=True)

    @bp.route('/api/export_db', methods=['POST'])
    def export_db():
        """Export planning results to a flat DB-ready Excel file via DatabaseExporter."""
        _, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        try:
            planning_df = current_engine.to_dataframe()
            site = getattr(current_engine.data.config, 'site', 'NLX1')
            initial_date = current_engine.data.config.initial_date

            exporter = DatabaseExporter(planning_df, site, initial_date)
            db_df = exporter.export_to_dataframe()

            if db_df.empty:
                return jsonify({'error': 'No data to export (no matching line types)'}), 400

            out_dir = export_dir()
            out_dir.mkdir(exist_ok=True)

            req_data = request.get_json(silent=True) or {}
            filename = req_data.get('filename', '').strip()
            if not filename:
                filename = f'SOP_DB_Export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            safe_name = ''.join(c for c in filename if c.isalnum() or c in '._- ')
            if not safe_name.endswith('.xlsx'):
                safe_name += '.xlsx'

            export_path = out_dir / safe_name
            db_df.to_excel(str(export_path), index=False)
            print(f'[export_db] {len(db_df)} rows written -> {export_path}')

            return send_file(str(export_path), as_attachment=True, download_name=safe_name)
        except Exception as exc:
            import traceback
            return jsonify({'error': str(exc), 'trace': traceback.format_exc()}), 500

    @bp.route('/api/mom')
    def get_mom_comparison():
        """Return sequential period-over-period MoM comparison from the current run."""
        _, current_engine = get_active()
        if current_engine is None:
            return jsonify({'available': False, 'message': 'No calculations run yet. Run calculations first.'})

        try:
            num_months = int(request.args.get('num_months', 6))
            num_months = max(1, min(num_months, 24))

            current_df = current_engine.to_dataframe()
            result = MoMComparisonEngine.calculate_sequential(current_df, num_months)
            return jsonify(result)
        except Exception as exc:
            import traceback
            return jsonify({'error': str(exc), 'trace': traceback.format_exc()}), 500

    return bp
