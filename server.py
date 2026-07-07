#!/usr/bin/env python3
"""Lightweight static file server with MDB query proxy for the occupancy dashboard."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from device_names import enrich_records, lookup_device_names
from pickle_inspector import get_efs_root, inspect_pickles

MDB_ENDPOINT = "http://mdb-sit.dozee.int"
RECORDSDB_ENDPOINT = "http://recordsdb-sit.dozee.int"
DEVICESDB_ENDPOINT = "http://devicesdb-sit.dozee.int"
PORT = 8765
EFS_PICKLEFILES_DIR = get_efs_root()
EFS_ROOT_EXISTS = os.path.isdir(EFS_PICKLEFILES_DIR)
MDB_PATH = "/api/dozee/fsrcalibration_sit/query"
DEFAULT_LIMIT = "2000"


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/records":
            self._proxy_mdb(parsed.query)
            return
        if parsed.path == "/api/config":
            self._send_json(
                200,
                {
                    "mdb_endpoint": MDB_ENDPOINT,
                    "recordsdb_endpoint": RECORDSDB_ENDPOINT,
                    "devicesdb_endpoint": DEVICESDB_ENDPOINT,
                    "efs_picklefiles_dir": EFS_PICKLEFILES_DIR,
                    "efs_root_exists": EFS_ROOT_EXISTS,
                },
            )
            return
        if parsed.path == "/api/pickles":
            self._inspect_pickles(parsed.query)
            return
        if parsed.path == "/api/device-names":
            self._device_names(parsed.query)
            return
        super().do_GET()

    def _device_names(self, query_string: str):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        device_ids = params.get("device_id", [])
        names = lookup_device_names(device_ids, devicesdb_endpoint=DEVICESDB_ENDPOINT)
        self._send_json(200, names)

    def _inspect_pickles(self, query_string: str):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        device_id = params.get("device_id", [""])[0]
        user_id = params.get("user_id", [""])[0]
        paired_at = params.get("paired_at", [""])[0]
        efs_root = params.get("efs_root", [""])[0] or EFS_PICKLEFILES_DIR
        result = inspect_pickles(
            device_id,
            user_id,
            paired_at,
            efs_root=efs_root,
            recordsdb_endpoint=RECORDSDB_ENDPOINT,
        )
        status = 400 if result.get("error") else 200
        self._send_json(status, result)

    def _proxy_mdb(self, query_string: str):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        mdb_params = []

        for key in ("filter", "span", "limit", "sort"):
            for value in params.get(key, []):
                mdb_params.append((key, value))

        if not any(k == "limit" for k, _ in mdb_params):
            mdb_params.append(("limit", DEFAULT_LIMIT))

        url = f"{MDB_ENDPOINT}{MDB_PATH}?{urllib.parse.urlencode(mdb_params)}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                records = json.loads(body.decode("utf-8"))
                if isinstance(records, list):
                    try:
                        records = enrich_records(records, devicesdb_endpoint=DEVICESDB_ENDPOINT)
                    except Exception:
                        pass
                    body = json.dumps(records).encode("utf-8")
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            self._send_json(exc.code, {"error": err_body or exc.reason})
        except Exception as exc:
            self._send_json(502, {"error": str(exc)})

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and isinstance(args[0], str) and args[0].startswith("GET /api/"):
            super().log_message(fmt, *args)


def bind_server(start_port: int, attempts: int = 10) -> tuple[ThreadingHTTPServer, int]:
    last_error = None
    for offset in range(attempts):
        port = start_port + offset
        try:
            server = ThreadingHTTPServer(("", port), DashboardHandler)
            server.allow_reuse_address = True
            return server, port
        except OSError as exc:
            if exc.errno != 48:  # Address already in use
                raise
            last_error = exc
    raise SystemExit(
        f"Ports {start_port}-{start_port + attempts - 1} are in use. "
        f"Stop the existing server or change PORT in server.py.\n"
        f"Last error: {last_error}"
    )


def main():
    server, port = bind_server(PORT)
    if port != PORT:
        print(f"Port {PORT} is in use; using {port} instead.")
    print(f"Occupancy dashboard: http://localhost:{port}")
    print(f"MDB endpoint: {MDB_ENDPOINT}")
    print(f"RecordsDB endpoint: {RECORDSDB_ENDPOINT}")
    print(f"DevicesDB endpoint: {DEVICESDB_ENDPOINT}")
    print(f"EFS pickle dir: {EFS_PICKLEFILES_DIR} ({'found' if EFS_ROOT_EXISTS else 'missing'})")
    server.serve_forever()


if __name__ == "__main__":
    main()
