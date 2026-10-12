"""Almacén local de pre-registros. SQLite con cifrado AES-256-GCM por campo personal.

Los datos personales (nombre, teléfono, correo) se cifran antes de llegar al disco.
La clave vive en un archivo local (paths.registrations_key). En producción debe
protegerse con permisos del SO y, si el hardware lo permite, un elemento seguro
(por verificar en Jetson). Los campos académicos no son personales y van en claro.
"""
from __future__ import annotations

import base64
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONSENT_VERSION = "v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS registros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    consent_version TEXT NOT NULL,
    tipo TEXT NOT NULL,
    robot_id TEXT NOT NULL,
    nombre_enc TEXT,
    telefono_enc TEXT,
    correo_enc TEXT,
    programa TEXT,
    modalidad TEXT,
    jornada TEXT
)
"""

EXPORT_COLUMNS = ["id", "created_at", "consent_at", "consent_version", "tipo", "robot_id",
                  "nombre", "telefono", "correo", "programa", "modalidad", "jornada"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RegistrationStore:
    def __init__(self, db_path: Path, key_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._aes = AESGCM(self._load_key(Path(key_path)))
        with self._connect() as conn:
            conn.execute(SCHEMA)

    @staticmethod
    def _load_key(path: Path) -> bytes:
        if path.exists():
            return base64.b64decode(path.read_text().strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        path.write_text(base64.b64encode(key).decode())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _enc(self, value: str | None) -> str | None:
        if value is None:
            return None
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self._aes.encrypt(nonce, value.encode("utf-8"), None)).decode()

    def _dec(self, blob: str | None) -> str | None:
        if blob is None:
            return None
        raw = base64.b64decode(blob)
        return self._aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")

    def save(self, *, tipo: str, robot_id: str, consent_at: str, nombre: str | None,
             telefono: str | None, correo: str | None, programa: str | None = None,
             modalidad: str | None = None, jornada: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO registros (created_at, consent_at, consent_version, tipo, robot_id,"
                " nombre_enc, telefono_enc, correo_enc, programa, modalidad, jornada)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (utc_now(), consent_at, CONSENT_VERSION, tipo, robot_id, self._enc(nombre),
                 self._enc(telefono), self._enc(correo), programa, modalidad, jornada),
            )
            return int(cur.lastrowid)

    def fetch_all(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM registros ORDER BY id").fetchall()
        return [{
            "id": r["id"], "created_at": r["created_at"], "consent_at": r["consent_at"],
            "consent_version": r["consent_version"], "tipo": r["tipo"], "robot_id": r["robot_id"],
            "nombre": self._dec(r["nombre_enc"]), "telefono": self._dec(r["telefono_enc"]),
            "correo": self._dec(r["correo_enc"]), "programa": r["programa"],
            "modalidad": r["modalidad"], "jornada": r["jornada"],
        } for r in rows]

    def delete_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._connect() as conn:
            cur = conn.executemany("DELETE FROM registros WHERE id = ?", [(i,) for i in ids])
            return cur.rowcount

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._connect() as conn:
            return conn.execute("DELETE FROM registros WHERE created_at < ?", (cutoff,)).rowcount

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM registros").fetchone()[0]
