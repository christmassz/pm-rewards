#!/usr/bin/env python3
"""
Simple real-time dashboard for PM-rewards application
Run with: python3 dashboard.py
"""

import os
import json
import time
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, Response, jsonify
import queue

app = Flask(__name__)

# Global state
dashboard_data = {
    'status': 'stopped',
    'markets': {},
    'active_workers': 0,
    'uptime': 0,
    'last_update': None,
    'logs': []
}

log_queue = queue.Queue()
pm_process = None

class PMRunner:
    """Manages the PM-rewards process and captures its output"""

    def __init__(self):
        self.process = None
        self.running = False

    def start(self, duration=300):  # 5 minutes default
        """Start the PM application in paper mode"""
        if self.running:
            return False

        try:
            self.process = subprocess.Popen(
                ['python3', '-m', 'src.main', '--seconds', str(duration)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                cwd='/Users/jo/Desktop/Work/PM-rewards'
            )
            self.running = True

            # Start thread to read output
            threading.Thread(target=self._read_output, daemon=True).start()
            return True
        except Exception as e:
            print(f"Failed to start PM process: {e}")
            return False

    def stop(self):
        """Stop the PM application"""
        if self.process and self.running:
            self.process.terminate()
            self.process.wait()
            self.running = False
            dashboard_data['status'] = 'stopped'

    def _read_output(self):
        """Read output from PM process and parse it"""
        start_time = time.time()

        dashboard_data['status'] = 'running'
        dashboard_data['uptime'] = 0

        while self.running and self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break

                line = line.strip()
                if line:
                    self._parse_line(line)
                    dashboard_data['uptime'] = time.time() - start_time
                    dashboard_data['last_update'] = datetime.now().strftime('%H:%M:%S')

                    # Add to event stream
                    log_queue.put({
                        'type': 'log',
                        'data': line,
                        'timestamp': datetime.now().isoformat()
                    })

            except Exception as e:
                print(f"Error reading process output: {e}")
                break

        self.running = False
        dashboard_data['status'] = 'stopped'
        print("PM process monitoring stopped")

    def _parse_line(self, line):
        """Parse output lines and extract relevant data"""
        try:
            # Parse active workers info
            if "Active workers:" in line and "markets:" in line:
                parts = line.split("markets:")
                if len(parts) > 1:
                    markets_str = parts[1].strip()
                    if markets_str.startswith('[') and markets_str.endswith(']'):
                        markets_list = eval(markets_str)  # Safe since we control the input
                        dashboard_data['active_workers'] = len(markets_list)
                        # Update market list
                        for market in markets_list:
                            if market not in dashboard_data['markets']:
                                dashboard_data['markets'][market] = {
                                    'name': market.replace('-', ' ').title(),
                                    'prices': {'Yes': None, 'No': None},
                                    'last_heartbeat': None
                                }

            # Parse worker heartbeat data
            elif "[WORKER " in line and "Heartbeat:" in line:
                parts = line.split("[WORKER ")
                if len(parts) > 1:
                    worker_part = parts[1].split("]")[0]
                    heartbeat_part = line.split("Heartbeat: ")[1]
                    prices = eval(heartbeat_part)  # Safe since we control the input

                    if worker_part in dashboard_data['markets']:
                        dashboard_data['markets'][worker_part]['prices'] = prices
                        dashboard_data['markets'][worker_part]['last_heartbeat'] = datetime.now().strftime('%H:%M:%S')

            # Keep recent logs
            dashboard_data['logs'].append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'message': line
            })

            # Keep only last 50 logs
            if len(dashboard_data['logs']) > 50:
                dashboard_data['logs'] = dashboard_data['logs'][-50:]

        except Exception as e:
            print(f"Error parsing line '{line}': {e}")

# Global PM runner instance
pm_runner = PMRunner()

@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/status')
def api_status():
    """API endpoint for current status"""
    return jsonify(dashboard_data)

@app.route('/api/start/<int:duration>')
def api_start(duration):
    """Start the PM application"""
    if duration < 30:
        duration = 30  # Minimum 30 seconds
    if duration > 3600:
        duration = 3600  # Maximum 1 hour

    success = pm_runner.start(duration)
    return jsonify({
        'success': success,
        'message': f'Started PM application for {duration} seconds' if success else 'Failed to start'
    })

@app.route('/api/stop')
def api_stop():
    """Stop the PM application"""
    pm_runner.stop()
    return jsonify({
        'success': True,
        'message': 'Stopped PM application'
    })

@app.route('/events')
def events():
    """Server-Sent Events endpoint for real-time updates"""
    def generate():
        while True:
            try:
                # Send current status every 2 seconds
                yield f"data: {json.dumps({'type': 'status', 'data': dashboard_data})}\n\n"
                time.sleep(2)

                # Also send any queued log events
                try:
                    while True:
                        event = log_queue.get_nowait()
                        yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    pass

            except GeneratorExit:
                break
            except Exception as e:
                print(f"Error in event stream: {e}")
                break

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    # Cleanup on exit
    import atexit
    atexit.register(pm_runner.stop)

    print("🚀 PM-Rewards Dashboard starting...")
    print("📊 Open http://localhost:5001 in your browser")
    print("⚡ Real-time market data will appear when you start the application")

    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True)