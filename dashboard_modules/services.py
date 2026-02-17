"""
Dashboard Module: Service Management

Provides API endpoints for monitoring and controlling external services
like the Web Terminal server on port 5002.
"""

import os
import signal
import subprocess
import time
from pathlib import Path
from flask import Blueprint, jsonify

services_bp = Blueprint('services', __name__, url_prefix='/api/services')

# Service configurations
# Services configuration - add service definitions here as needed
# Example format:
# SERVICES = {
#     'service_name': {
#         'name': 'Display Name',
#         'port': 5002,
#         'process_pattern': 'uvicorn main:app.*--port 5002',
#         'start_script': '/path/to/start_script.sh',
#         'working_dir': '/path/to/working/dir'
#     }
# }
SERVICES = {}


def find_service_process(service_id: str) -> dict | None:
    """Find a running service process by its configuration."""
    if service_id not in SERVICES:
        return None

    service = SERVICES[service_id]
    port = service['port']

    try:
        # Use lsof to find process on the port
        result = subprocess.run(
            ['lsof', '-i', f':{port}', '-t'],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            pid = int(result.stdout.strip().split('\n')[0])
            # Get process command
            cmd_result = subprocess.run(
                ['ps', '-p', str(pid), '-o', 'args='],
                capture_output=True, text=True, timeout=5
            )
            return {
                'pid': pid,
                'cmd': cmd_result.stdout.strip(),
                'port': port
            }
    except Exception:
        pass

    return None


def check_port_listening(port: int) -> bool:
    """Check if a port is listening."""
    try:
        result = subprocess.run(
            ['lsof', '-i', f':{port}', '-t'],
            capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


@services_bp.route('/status')
def get_all_services_status():
    """Get status of all managed services."""
    statuses = {}
    for service_id, config in SERVICES.items():
        proc = find_service_process(service_id)
        statuses[service_id] = {
            'name': config['name'],
            'port': config['port'],
            'online': proc is not None,
            'pid': proc['pid'] if proc else None
        }
    return jsonify(statuses)


@services_bp.route('/status/<service_id>')
def get_service_status(service_id: str):
    """Get status of a specific service."""
    if service_id not in SERVICES:
        return jsonify({'error': f'Unknown service: {service_id}'}), 404

    config = SERVICES[service_id]
    proc = find_service_process(service_id)

    return jsonify({
        'service_id': service_id,
        'name': config['name'],
        'port': config['port'],
        'online': proc is not None,
        'pid': proc['pid'] if proc else None,
        'cmd': proc['cmd'] if proc else None
    })


@services_bp.route('/restart/<service_id>', methods=['POST'])
def restart_service(service_id: str):
    """Restart a specific service."""
    if service_id not in SERVICES:
        return jsonify({'error': f'Unknown service: {service_id}'}), 404

    config = SERVICES[service_id]
    proc = find_service_process(service_id)

    # Stop if running
    if proc:
        try:
            os.kill(proc['pid'], signal.SIGTERM)
            # Wait for graceful shutdown
            for _ in range(10):
                time.sleep(0.5)
                if not check_port_listening(config['port']):
                    break

            # Force kill if still running
            if check_port_listening(config['port']):
                os.kill(proc['pid'], signal.SIGKILL)
                time.sleep(1)
        except ProcessLookupError:
            pass  # Already stopped
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Failed to stop service: {str(e)}'
            }), 500

    # Start the service
    try:
        working_dir = config['working_dir']
        script_path = config['start_script']

        if not Path(script_path).exists():
            return jsonify({
                'success': False,
                'error': f'Start script not found: {script_path}'
            }), 500

        # Start in background with nohup
        log_file = Path(working_dir) / 'server.log'
        subprocess.Popen(
            ['bash', script_path],
            cwd=working_dir,
            stdout=open(log_file, 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}
        )

        # Wait for service to come up
        for _ in range(20):
            time.sleep(0.5)
            if check_port_listening(config['port']):
                new_proc = find_service_process(service_id)
                return jsonify({
                    'success': True,
                    'message': f'{config["name"]} restarted successfully',
                    'pid': new_proc['pid'] if new_proc else None,
                    'port': config['port']
                })

        return jsonify({
            'success': False,
            'error': 'Service did not start within timeout'
        }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to start service: {str(e)}'
        }), 500
