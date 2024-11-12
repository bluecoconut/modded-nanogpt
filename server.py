import os
import re
import json
import time
import difflib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

LOG_DIR = 'logs'

# Regular expression to parse log lines
LOG_LINE_REGEX = re.compile(
    r'step:(\d+)/(\d+) (\w+):([\d\.]+)(?: [\w_]+:[\d\.]+)* train_time:([\d\.]+)ms step_avg:([\d\.n]+)ms'
)

# Cache for diffs to improve performance
diff_cache = {}

def parse_logs():
    """Parses log files and returns data."""
    runs = []
    for filename in os.listdir(LOG_DIR):
        if filename.endswith('.txt'):
            run_id = filename[:-4]
            filepath = os.path.join(LOG_DIR, filename)
            # Get modification time
            mtime = os.path.getmtime(filepath)
            timestamp = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            data = []
            code = ''
            with open(filepath, 'r') as f:
                lines = f.readlines()
                # Extract code between lines with '=' * 100
                in_code = False
                code_lines = []
                for line in lines:
                    if line.strip() == '=' * 100:
                        in_code = not in_code
                        continue
                    if in_code:
                        code_lines.append(line)
                    else:
                        match = LOG_LINE_REGEX.match(line.strip())
                        if match:
                            step = int(match.group(1))
                            total_steps = int(match.group(2))
                            metric_name = match.group(3)
                            metric_value = float(match.group(4))
                            train_time = float(match.group(5))
                            step_avg = match.group(6)
                            if step_avg == 'nan':
                                step_avg = None
                            else:
                                step_avg = float(step_avg)
                            data.append({
                                'step': step,
                                'metric_name': metric_name,
                                'metric_value': metric_value,
                                'train_time': train_time,
                                'step_avg': step_avg,
                            })
                code = ''.join(code_lines)
            runs.append({
                'run_id': run_id,
                'timestamp': timestamp,
                'mtime': mtime,  # Store mtime for sorting
                'code': code,
                'data': data
            })
    # Sort runs by modification time descending (most recent first)
    runs.sort(key=lambda x: x['mtime'], reverse=True)
    return runs

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/':
            # Serve the HTML page
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif parsed_path.path == '/data':
            # Serve the data as JSON
            data = parse_logs()
            # Remove 'mtime' and 'code' before sending data
            for run in data:
                del run['mtime']
                del run['code']
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
        elif parsed_path.path == '/diff':
            # Serve the diff between code of selected run and previous or first run
            query = parse_qs(parsed_path.query)
            run_id = query.get('run_id', [None])[0]
            compare_to = query.get('compare_to', ['previous'])[0]  # 'previous' or 'first'
            if run_id:
                runs = parse_logs()
                # Find the index of the selected run
                index = next((i for i, run in enumerate(runs) if run['run_id'] == run_id), None)
                if index is not None:
                    cache_key = (run_id, compare_to)
                    if cache_key in diff_cache:
                        diff_text = diff_cache[cache_key]
                    else:
                        current_code = runs[index]['code'].splitlines(keepends=True)
                        if compare_to == 'previous' and index + 1 < len(runs):
                            prev_code = runs[index + 1]['code'].splitlines(keepends=True)
                            compare_label = 'Previous Run'
                        elif compare_to == 'first' and len(runs) > 0:
                            prev_code = runs[-1]['code'].splitlines(keepends=True)  # Last in list is oldest due to sorting
                            compare_label = 'First Run'
                        else:
                            prev_code = []
                            compare_label = 'No Run to Compare'
                        if prev_code:
                            diff = difflib.unified_diff(prev_code, current_code, fromfile=compare_label, tofile='Selected Run')
                            diff_text = ''.join(diff)
                            if not diff_text:
                                diff_text = 'No differences found.'
                        else:
                            diff_text = 'No run available to compare.'
                        # Include run IDs in the diff text
                        diff_text = f'Comparing Run {runs[index]["run_id"]} to {compare_label}\n\n' + diff_text
                        # Cache the diff
                        diff_cache[cache_key] = diff_text
                else:
                    diff_text = 'Run ID not found.'
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(diff_text.encode('utf-8'))
            else:
                self.send_error(400, 'Bad Request: run_id parameter is missing.')
        else:
            self.send_error(404, 'File Not Found: %s' % self.path)

# The HTML content of the page
HTML_CONTENT = '''
<!DOCTYPE html>
<html>
<head>
    <title>Run Monitor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 0; }
        #container { display: flex; height: 100vh; }
        #runs { width: 300px; overflow-y: auto; border-right: 1px solid #ccc; padding: 10px; box-sizing: border-box; }
        #plot { flex-grow: 1; padding: 10px; box-sizing: border-box; display: flex; flex-direction: column; }
        .run-item { cursor: pointer; margin: 5px 0; padding: 5px; border: 1px solid #ccc; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .selected { background-color: #e0e0e0; }
        #controls { margin-bottom: 20px; }
        #diff { margin-top: 20px; overflow-y: auto; flex-grow: 1; background-color: #f9f9f9; padding: 10px; border: 1px solid #ccc; font-family: monospace; white-space: pre-wrap; }
    </style>
    <!-- Include Chart.js from CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div id="container">
        <div id="runs">
            <h2>Runs (<span id="total-runs"></span>)</h2>
            <ul id="run-list"></ul>
        </div>
        <div id="plot">
            <div id="controls">
                <label>
                    <input type="checkbox" name="lossType" value="val_loss" checked> Val Loss
                </label>
                <label>
                    <input type="checkbox" name="lossType" value="train_loss" checked> Train Loss
                </label>
                &nbsp;&nbsp;&nbsp;
                <label>
                    <input type="radio" name="xAxisType" value="iteration" checked> Iteration
                </label>
                <label>
                    <input type="radio" name="xAxisType" value="train_time"> Total Duration
                </label>
                &nbsp;&nbsp;&nbsp;
                Compare to:
                <label>
                    <input type="radio" name="compareTo" value="previous" checked> Previous
                </label>
                <label>
                    <input type="radio" name="compareTo" value="first"> First
                </label>
            </div>
            <canvas id="chart-canvas"></canvas>
            <div id="diff"></div>
        </div>
    </div>
    <script>
        var selectedRuns = [];
        var lossTypes = ['val_loss', 'train_loss']; // Initially both selected
        var xAxisType = 'iteration'; // or 'train_time'
        var runData = [];
        var chart = null;
        var lastClickedRunId = null;
        var compareTo = 'previous'; // 'previous' or 'first'

        function fetchData() {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', '/data', true);
            xhr.onload = function() {
                if (xhr.status === 200) {
                    runData = JSON.parse(xhr.responseText);
                    updateRunList(runData);
                    updateChart();
                }
            };
            xhr.send();
        }

        function updateRunList(data) {
            var runList = document.getElementById('run-list');
            var totalRunsSpan = document.getElementById('total-runs');
            runList.innerHTML = '';
            totalRunsSpan.textContent = data.length;
            data.forEach(function(run) {
                var li = document.createElement('li');
                var runShortId = run.run_id.substring(0, 8);
                li.innerHTML = '<b>' + runShortId + '</b> - ' + run.timestamp;
                li.classList.add('run-item');
                if (selectedRuns.includes(run.run_id)) {
                    li.classList.add('selected');
                }
                li.onclick = function() {
                    if (selectedRuns.includes(run.run_id)) {
                        selectedRuns = selectedRuns.filter(function(r) { return r !== run.run_id; });
                        li.classList.remove('selected');
                    } else {
                        selectedRuns.push(run.run_id);
                        li.classList.add('selected');
                    }
                    lastClickedRunId = run.run_id;
                    updateChart();
                    fetchDiff(run.run_id);
                };
                runList.appendChild(li);
            });
        }

        function getColorForRun(runId) {
            // Generate a consistent color for each run based on its UUID
            var hash = 0;
            for (var i = 0; i < runId.length; i++) {
                hash = runId.charCodeAt(i) + ((hash << 5) - hash);
            }
            var h = hash % 360;
            return 'hsl(' + h + ', 70%, 50%)';
        }

        function updateChart() {
            var plotData = runData.filter(function(run) { return selectedRuns.includes(run.run_id); });

            if (chart) {
                chart.destroy();
            }

            var datasets = [];
            plotData.forEach(function(run) {
                var color = getColorForRun(run.run_id);
                lossTypes.forEach(function(lossType) {
                    var dataPoints = [];
                    run.data.forEach(function(point) {
                        if (point.metric_name === lossType) {
                            var xValue = (xAxisType === 'iteration') ? point.step : point.train_time;
                            dataPoints.push({ x: xValue, y: point.metric_value });
                        }
                    });
                    if (dataPoints.length > 0) {
                        datasets.push({
                            label: run.run_id.substring(0, 8) + ' - ' + lossType.replace('_', ' '),
                            data: dataPoints,
                            borderColor: color,
                            fill: false,
                            tension: 0.1,
                            borderWidth: 1,
                            pointRadius: 0,
                            borderDash: []
                        });
                    }
                });
            });

            var ctx = document.getElementById('chart-canvas').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: datasets
                },
                options: {
                    animation: false,
                    scales: {
                        x: {
                            type: 'linear',
                            title: {
                                display: true,
                                text: xAxisType === 'iteration' ? 'Iteration' : 'Total Duration (ms)'
                            }
                        },
                        y: {
                            title: {
                                display: true,
                                text: 'Loss'
                            }
                        }
                    },
                    plugins: {
                        legend: {
                            display: true,
                            labels: {
                                usePointStyle: true
                            }
                        },
                        tooltip: {
                            mode: 'nearest',
                            intersect: false
                        }
                    }
                }
            });
        }

        function setupControls() {
            document.querySelectorAll('input[name="lossType"]').forEach(function(elem) {
                elem.addEventListener('change', function() {
                    lossTypes = Array.from(document.querySelectorAll('input[name="lossType"]:checked')).map(function(el) { return el.value; });
                    updateChart();
                });
            });

            document.querySelectorAll('input[name="xAxisType"]').forEach(function(elem) {
                elem.addEventListener('change', function() {
                    xAxisType = this.value;
                    updateChart();
                });
            });

            document.querySelectorAll('input[name="compareTo"]').forEach(function(elem) {
                elem.addEventListener('change', function() {
                    compareTo = this.value;
                    if (lastClickedRunId) {
                        fetchDiff(lastClickedRunId);
                    }
                });
            });
        }

        function fetchDiff(runId) {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', '/diff?run_id=' + encodeURIComponent(runId) + '&compare_to=' + compareTo, true);
            xhr.onload = function() {
                if (xhr.status === 200) {
                    var diffDiv = document.getElementById('diff');
                    var diffText = xhr.responseText;
                    diffDiv.textContent = diffText;
                }
            };
            xhr.send();
        }

        // Fetch data every 500ms
        setInterval(fetchData, 500);
        setupControls();
        // Initial fetch
        fetchData();
    </script>
</body>
</html>
'''

def run(server_class=HTTPServer, handler_class=SimpleHTTPRequestHandler):
    server_address = ('', 8000)
    httpd = server_class(server_address, handler_class)
    print('Starting server at http://localhost:8000')
    httpd.serve_forever()

if __name__ == '__main__':
    run()
