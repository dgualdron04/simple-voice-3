"""Servidor local del dashboard (solo biblioteca estándar; sin internet).

Por seguridad escucha en 127.0.0.1: los pre-registros contienen datos personales. Para abrirlo a otros equipos de
la red hay que dar --token, que se exige en cada petición a la API.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.analytics import load_events, summarize
from core.registration.store import ESTADOS, EXPORT_COLUMNS, RegistrationStore

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"


class Context:
    def __init__(self, store: RegistrationStore, log_dir: Path, robot_name: str, demo: bool, token: str | None):
        self.store, self.log_dir, self.robot_name, self.demo, self.token = store, log_dir, robot_name, demo, token


def make_handler(ctx: Context):
    class Handler(BaseHTTPRequestHandler):
        server_version = "UDIDashboard"

        def log_message(self, *args):                       # sin ruido en consola
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _authorized(self, query: dict) -> bool:
            if not ctx.token:
                return True
            given = self.headers.get("X-Token") or (query.get("token") or [""])[0]
            return secrets.compare_digest(given, ctx.token)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if not url.path.startswith("/api/"):
                return self._send(404, b"no encontrado", "text/plain; charset=utf-8")
            if not self._authorized(query):
                return self._json({"error": "token requerido"}, 401)

            if url.path == "/api/meta":
                return self._json({"robot": ctx.robot_name, "demo": ctx.demo, "estados": list(ESTADOS)})
            if url.path == "/api/registrations":
                return self._json(ctx.store.fetch_all())
            if url.path == "/api/registrations.csv":
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
                writer.writeheader()
                writer.writerows(ctx.store.fetch_all())
                return self._send(200, ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="preregistros.csv"'})
            if url.path == "/api/usage":
                try:
                    days = max(1, min(60, int((query.get("days") or ["7"])[0])))
                except ValueError:
                    days = 7
                robot = (query.get("robot") or [""])[0] or None
                return self._json(summarize(load_events(ctx.log_dir, days), ctx.log_dir, robot))
            return self._json({"error": "no encontrado"}, 404)

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self._authorized(query):
                return self._json({"error": "token requerido"}, 401)
            parts = url.path.strip("/").split("/")                  # api/registrations/<id>/estado
            if len(parts) == 4 and parts[:2] == ["api", "registrations"] and parts[3] == "estado" and parts[2].isdigit():
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    estado = json.loads(self.rfile.read(length) or b"{}").get("estado")
                    ok = ctx.store.set_estado(int(parts[2]), estado)
                except (ValueError, json.JSONDecodeError):
                    return self._json({"error": "estado inválido"}, 400)
                return self._json({"ok": ok}, 200 if ok else 404)
            return self._json({"error": "no encontrado"}, 404)

    return Handler


def serve(ctx: Context, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in LOOPBACK and not ctx.token:
        raise SystemExit("Por seguridad, para escuchar fuera de este equipo hay que usar --token.")
    return ThreadingHTTPServer((host, port), make_handler(ctx))


def serve_in_thread(ctx: Context, port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    server = serve(ctx, "127.0.0.1", port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]
