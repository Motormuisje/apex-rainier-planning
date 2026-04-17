"""License and trial activation routes."""

from flask import Blueprint, jsonify, redirect, render_template, request

from modules.license_manager import LicenseStatus


LICENSE_EXEMPT = {'/accept', '/api/license/status', '/api/license/activate'}


def create_license_blueprint(license_manager) -> Blueprint:
    bp = Blueprint('license', __name__)

    @bp.before_app_request
    def check_license():
        if request.path in LICENSE_EXEMPT or request.path.startswith('/static'):
            return None
        status, info = license_manager.check()
        if status == LicenseStatus.NOT_ACTIVATED:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'license_required', 'message': 'Trial not yet activated.'}), 403
            return render_template('accept.html', mode='accept')
        if status == LicenseStatus.EXPIRED:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'license_expired', 'message': 'Trial period has ended.'}), 403
            return render_template('accept.html', mode='expired', info=info)
        if status == LicenseStatus.TAMPERED:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'license_invalid', 'message': 'License record is invalid.'}), 403
            return render_template('accept.html', mode='tampered')
        return None

    @bp.route('/accept')
    def accept_page():
        status, info = license_manager.check()
        if status == LicenseStatus.OK:
            return redirect('/')
        mode = 'accept' if status == LicenseStatus.NOT_ACTIVATED else status
        return render_template('accept.html', mode=mode, info=info)

    @bp.route('/api/license/status', methods=['GET'])
    def license_status():
        status, info = license_manager.check()
        return jsonify({'status': status, 'info': info})

    @bp.route('/api/license/activate', methods=['POST'])
    def license_activate():
        status, _ = license_manager.check()
        if status == LicenseStatus.EXPIRED:
            return jsonify({'success': False, 'error': 'Trial period has already expired.'}), 403
        if status == LicenseStatus.TAMPERED:
            return jsonify({'success': False, 'error': 'License record is corrupt.'}), 403
        ok = license_manager.activate()
        if ok:
            _, info = license_manager.check()
            return jsonify({'success': True, 'info': info})
        return jsonify({'success': False, 'error': 'Activation failed.'}), 500

    return bp
