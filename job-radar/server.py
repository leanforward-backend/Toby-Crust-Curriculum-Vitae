#!/usr/bin/env python3
"""Local server for the Job Radar dashboard.

    python3 server.py              # http://127.0.0.1:8787
    python3 server.py --port 9000

Serves the dashboard, the CV profiles next to it, and a small JSON API:
  GET  /api/jobs    jobs, statuses and recent runs
  POST /api/status  {"id": "...", "state": "saved" | "applied" | "interview" | "dismissed" | null}
  GET  /api/scan    whether a scan is running
  POST /api/scan    start a scan now
Listens on localhost only.
"""
import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
WEB = os.path.join(ROOT, 'web')
DATA = os.path.join(ROOT, 'data')
STATES = {'saved', 'applied', 'interview', 'dismissed'}
# Repo files the dashboard may link to (the tailored CVs). Nothing else outside web/ is served.
REPO_ALLOW = ('profiles/', 'cv.css', 'index.html')

lock = threading.Lock()
scan = {'proc': None, 'startedAt': None, 'lastExit': None, 'finishedAt': None}


def read_json(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(name, value):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    with open(path + '.tmp', 'w') as f:
        json.dump(value, f, indent=1, ensure_ascii=False)
    os.replace(path + '.tmp', path)


def scan_state():
    p = scan['proc']
    if p is not None and p.poll() is not None:
        scan.update(proc=None, lastExit=p.returncode, finishedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return {'running': scan['proc'] is not None, 'startedAt': scan['startedAt'], 'finishedAt': scan['finishedAt'], 'lastExit': scan['lastExit']}


class Handler(BaseHTTPRequestHandler):
    server_version = 'JobRadar/1.0'

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/'):
            return
        sys.stderr.write('%s %s\n' % (self.command, self.path))

    # ---------- helpers
    def send_json(self, value, code=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text/') or ctype.endswith('javascript') else ''))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_host(self):
        # Refuse DNS-rebinding requests that reach us under another hostname.
        port = self.server.server_address[1]
        return self.headers.get('Host', '') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def same_origin(self):
        # Block other websites from posting to this local server.
        origin = self.headers.get('Origin')
        return origin is None or origin == f"http://{self.headers.get('Host', '')}"

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 10_000:
            raise ValueError('body too large')
        return json.loads(self.rfile.read(n) or b'{}')

    # ---------- routes
    def do_GET(self):
        if not self.local_host():
            return self.send_error(403)
        path = unquote(urlparse(self.path).path)
        if path == '/':
            self.send_response(302)
            self.send_header('Location', '/radar/')
            self.end_headers()
            return
        if path == '/api/jobs':
            profile = json.load(open(os.path.join(ROOT, 'profile.json')))
            jobs = [{k: v for k, v in j.items() if k != 'desc'} for j in read_json('jobs.json', [])]
            return self.send_json({
                'jobs': jobs,
                'status': read_json('status.json', {}),
                'runs': read_json('runs.json', [])[-30:],
                'scan': scan_state(),
                'profile': {
                    'location': profile['location']['label'],
                    'years': profile['experience']['years'],
                    'maxYears': profile['experience']['maxYearsRequired'],
                    'tracks': {k: {'label': t['label'], 'cv': '/' + t['cv'], 'cvName': t.get('cvName')} for k, t in profile['tracks'].items()},
                },
            })
        if path == '/api/scan':
            return self.send_json(scan_state())
        if path.startswith('/radar/'):
            rel = path[len('/radar/'):] or 'index.html'
            return self.serve_under(WEB, rel)
        rel = path.lstrip('/')
        if rel.startswith(REPO_ALLOW) or rel in REPO_ALLOW:
            return self.serve_under(REPO, rel)
        self.send_error(404)

    def serve_under(self, base, rel):
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
            return self.send_error(404)
        self.send_file(full)

    def do_POST(self):
        path = urlparse(self.path).path
        if not (self.local_host() and self.same_origin()):
            return self.send_json({'error': 'Cross-site requests are not allowed.'}, 403)
        if path == '/api/status':
            try:
                body = self.body_json()
            except ValueError:
                return self.send_json({'error': 'Send a JSON body.'}, 400)
            jid, state = body.get('id'), body.get('state')
            if not isinstance(jid, str) or (state is not None and state not in STATES):
                return self.send_json({'error': 'Send an id and a state of saved, applied, interview, dismissed or null.'}, 400)
            with lock:
                if jid not in {j['id'] for j in read_json('jobs.json', [])}:
                    return self.send_json({'error': 'That job is no longer in the list.'}, 404)
                status = read_json('status.json', {})
                if state is None:
                    status.pop(jid, None)
                else:
                    status[jid] = {'state': state, 'updatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds')}
                write_json('status.json', status)
            return self.send_json({'status': status})
        if path == '/api/scan':
            with lock:
                if scan_state()['running']:
                    return self.send_json(scan_state(), 409)
                with open(os.path.join(DATA, 'scan.log'), 'a') as log:
                    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'scan.py')], stdout=log, stderr=log, cwd=ROOT)
                scan.update(proc=proc, startedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            return self.send_json(scan_state(), 202)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description='Serve the Job Radar dashboard on localhost.')
    ap.add_argument('--port', type=int, default=int(os.environ.get('JOB_RADAR_PORT', 8787)))
    args = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Job Radar running at http://127.0.0.1:{args.port}/', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
