"""Exporta los pre-registros a CSV y borra lo exportado (solo si el CSV quedó bien escrito).

Uso:
  python scripts/export_registros.py --robot g1                  # a data/export/ y borra
  python scripts/export_registros.py --robot g1 --out /media/usb # destino (USB)
  python scripts/export_registros.py --robot g1 --keep           # exporta sin borrar
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_robot, resolve_path  # noqa: E402
from core.registration.store import EXPORT_COLUMNS, RegistrationStore  # noqa: E402


def export(store: RegistrationStore, out_dir: Path, robot_id: str, keep: bool = False) -> tuple[int, Path | None]:
    rows = store.fetch_all()
    if not rows:
        return 0, None

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"registros_{robot_id}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with open(out_file, newline="", encoding="utf-8-sig") as f:
        written = sum(1 for _ in csv.DictReader(f))
    if written != len(rows):
        raise RuntimeError(f"CSV tiene {written} filas y la base {len(rows)}: no se borra nada")

    if not keep:
        store.delete_ids([r["id"] for r in rows])
    return written, out_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default=None)
    parser.add_argument("--out", default=None, help="directorio de salida (p. ej. punto de montaje USB)")
    parser.add_argument("--keep", action="store_true", help="no borrar tras exportar")
    args = parser.parse_args()

    settings = load_robot(args.robot)
    store = RegistrationStore(resolve_path(settings["paths"]["registrations_db"]),
                              resolve_path(settings["paths"]["registrations_key"]))
    out_dir = Path(args.out) if args.out else resolve_path(settings["paths"]["export_dir"])

    count, out_file = export(store, out_dir, settings["robot"]["id"], args.keep)
    if count == 0:
        print("No hay registros para exportar.")
        return 0
    print(f"Exportados {count} registros -> {out_file}")
    print("--keep: no se borró nada." if args.keep else "Registros borrados de la base del robot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
