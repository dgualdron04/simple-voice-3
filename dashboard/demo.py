"""Datos sintéticos para ver el dashboard sin datos reales (python -m dashboard --demo)."""
from __future__ import annotations

import json
import random
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from core.registration.store import RegistrationStore

PROGRAMAS = [("Ingeniería de Sistemas", 20), ("Derecho", 16), ("Administración de Empresas", 12),
             ("Comunicación Social", 9), ("Negocios Internacionales", 8), ("Diseño Gráfico", 6),
             ("Ingeniería Civil", 5), ("Criminalística", 4), ("Ingeniería Industrial", 3)]
NOMBRES = ["Camila", "Santiago", "Valentina", "Mateo", "Sofía", "Juan", "Laura", "Andrés", "Daniela", "Felipe",
           "Mariana", "Sebastián", "Paula", "Nicolás", "Natalia", "Diego"]
APELLIDOS = ["Gómez", "Rodríguez", "Pérez", "Martínez", "Torres", "Ramírez", "Castro", "Vargas", "Ortiz", "Rojas"]

HEARD_LLM = ["cuéntame cómo va tu día", "qué opinas del café colombiano", "eres un robot de verdad",
             "puedes cantar una canción", "cuál es tu color favorito", "qué haces cuando no hay nadie"]
HEARD_FAQ = [("cuánto cuesta derecho", "knowledge:program_db"), ("quién es el rector", "knowledge:faq"),
             ("dónde queda la universidad", "knowledge:institution"), ("qué programas ofrecen", "knowledge:listing"),
             ("cuánto dura ingeniería de sistemas", "knowledge:program_db"),
             ("háblame de la universidad", "knowledge:institution"),
             ("gracias", "fast:thanks"), ("hola cómo estás", "fast:small_talk"), ("dame la mano", "gesture:dar_mano")]
HEARD_IGN = [("hola cuánto cuesta derecho", "no_wake"), ("mesa", "single_word"), ("gracias por ver el video", "noise"),
             ("ja ja ja ja ja ja", "noise"), ("el valor de derecho en jornada diurna", "echo"), ("hola roga", "no_wake")]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def build(seed: int = 7) -> tuple[RegistrationStore, Path]:
    rnd = random.Random(seed)
    root = Path(tempfile.mkdtemp(prefix="udi_demo_"))
    store = RegistrationStore(root / "registros.db", root / "registros.key")
    now = datetime.now()
    pesos = [p for p, w in PROGRAMAS for _ in range(w)]
    day_weights = [9, 8, 7, 7, 6, 5, 5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1]
    created = []
    for i in range(64):
        tipo = "solo_telefono" if rnd.random() < 0.28 else "preregistro"
        when = now - timedelta(days=rnd.choices(range(21), weights=day_weights)[0],
                               hours=rnd.randint(0, 9), minutes=rnd.randint(0, 59))
        programa = rnd.choice(pesos) if tipo == "preregistro" else None
        rid = store.save(
            tipo=tipo, robot_id=rnd.choices(["g1", "h2", "go2x"], [5, 3, 2])[0],
            consent_at=when.isoformat(timespec="seconds"),
            nombre=None if tipo == "solo_telefono" else f"{rnd.choice(NOMBRES)} {rnd.choice(APELLIDOS)}",
            telefono=f"3{rnd.randint(0, 2)}{rnd.randint(0, 9)}{rnd.randint(1000000, 9999999)}",
            correo=None if rnd.random() < 0.4 or tipo == "solo_telefono" else f"demo{i}@correo.test",
            programa=programa, modalidad=None if not programa else rnd.choices(["presencial", "virtual"], [8, 2])[0],
            jornada=None if not programa else rnd.choices(["diurna", "nocturna"], [7, 3])[0])
        created.append((rid, when))
    with sqlite3.connect(store.db_path) as conn:
        for rid, when in created:
            estado = rnd.choices(["nuevo", "contactado", "descartado"], [5, 4, 1])[0]
            conn.execute("UPDATE registros SET created_at = ?, estado = ? WHERE id = ?",
                         (when.isoformat(timespec="seconds"), estado, rid))

    logs = root / "logs"
    logs.mkdir()
    n = 0
    for back in range(6, -1, -1):
        day = now - timedelta(days=back)
        events = [{"kind": "start", "ts": _iso(day.replace(hour=8, minute=0))}]
        for _ in range(rnd.randint(25, 70)):
            hour = rnd.choices(range(8, 19), [1, 3, 5, 7, 6, 3, 4, 6, 7, 4, 2])[0]
            ts = _iso(day.replace(hour=hour, minute=rnd.randint(0, 59), second=rnd.randint(0, 59)))
            r = rnd.random()
            stt = {"audio_s": round(rnd.uniform(1, 5), 2), "stt_ms": rnd.randint(1100, 3400),
                   "avg_logprob": round(-rnd.random() * (1.4 if rnd.random() < .12 else .6), 2), "no_speech_prob": 0.05}
            if r < 0.14:
                heard, why = rnd.choice(HEARD_IGN)
                events.append({"kind": "turn", "ts": ts, "heard": heard, "route": f"ignored:{why}", "stt": stt,
                               "engine_ms": 0.4})
            elif r < 0.30:
                events.append({"kind": "turn", "ts": ts, "heard": rnd.choice(HEARD_LLM), "route": "llm:free",
                               "stt": stt, "engine_ms": 1.2})
                events.append({"kind": "llm", "ts": ts, "route": "llm:free", "first_token_ms": rnd.randint(350, 1900),
                               "total_ms": rnd.randint(900, 3500), "answer": "Respuesta de ejemplo del modelo."})
            elif r < 0.38:
                events.append({"kind": "turn", "ts": ts, "heard": "<dato:telefono>",
                               "route": rnd.choice(["flow:consent", "flow:telefono", "flow:nombre", "flow:confirm"]),
                               "stt": stt, "engine_ms": 0.8})
            else:
                heard, route = rnd.choice(HEARD_FAQ)
                events.append({"kind": "turn", "ts": ts, "heard": heard, "route": route, "stt": stt,
                               "engine_ms": round(rnd.uniform(0.3, 6), 1)})
        if back == 1:
            events.append({"kind": "error", "ts": _iso(day.replace(hour=11)), "where": "DialogEngine.handle",
                           "error": "KeyError('programa')"})
        for e in events:
            n += 1
            e.update({"run": "demo", "n": n, "robot": "g1" if rnd.random() < .7 else "h2"})
        with open(logs / f"uso_{day:%Y-%m-%d}.jsonl", "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in sorted(events, key=lambda x: x["ts"])))
    return store, logs
