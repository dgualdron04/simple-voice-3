"""Dashboard local:  python -m dashboard [--robot g1] [--demo] [--port 8765] [--open]"""
from __future__ import annotations

import argparse
import webbrowser

from core.config import load_robot, resolve_path
from core.registration.store import RegistrationStore
from dashboard.server import Context, serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard de pre-registros y uso del robot")
    parser.add_argument("--robot", default=None, help="perfil (laptop | g1 | h2 | go2x); define rutas de datos")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--token", default=None, help="obligatorio si --host no es local")
    parser.add_argument("--demo", action="store_true", help="datos sintéticos (no toca los reales)")
    parser.add_argument("--open", action="store_true", help="abrir el navegador")
    args = parser.parse_args()

    settings = load_robot(args.robot)
    if args.demo:
        from dashboard.demo import build
        store, log_dir = build()
    else:
        store = RegistrationStore(resolve_path(settings["paths"]["registrations_db"]),
                                  resolve_path(settings["paths"]["registrations_key"]))
        log_dir = resolve_path(settings["logging"]["dir"])

    ctx = Context(store, log_dir, settings["robot"]["name"], args.demo, args.token)
    server = serve(ctx, args.host, args.port)
    suffix = f"/?token={args.token}" if args.token else ""
    url = f"http://{args.host}:{args.port}{suffix}"
    print(f"Dashboard {'(DEMO) ' if args.demo else ''}en {url}   (Ctrl+C para cerrar)")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
