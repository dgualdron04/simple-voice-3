"""Datos básicos de la UDI (dirección, teléfonos, correo, reconocimiento legal) leídos de
data/rag/udi_institucional.xml. Respuestas exactas, sin LLM: lo que dice el XML es lo que se dice.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from core.config import get_settings, resolve_path
from core.registration import validators as v

_facts: dict[str, str] | None = None

# clave -> (fragmento del <texto> de la pregunta en el XML, palabras que la activan)
INTENTS = {
    "direccion": ("dirección", ("direccion", "donde queda", "donde se encuentra", "ubicacion", "ubicada", "como llego", "donde esta la udi", "donde esta la universidad")),
    "telefono": ("teléfono", ("telefono", "pbx", "celular", "numero de contacto", "numero de la")),
    "correo": ("correo", ("correo", "email", "e mail")),
    "legal": ("reconocida", ("personeria", "reconocida", "reconocimiento", "ministerio de educacion", "es legal", "es una universidad")),
    "resumen": ("qué es la udi", ("que es la udi", "que es la universidad", "hablame de la universidad", "hablame de la udi",
                                  "sobre la universidad", "sobre la udi", "cuentame de la universidad", "cuentame de la udi",
                                  "informacion de la universidad", "informacion de la udi", "quienes son")),
}


def _speak_digits(text: str) -> str:
    def groups(match: re.Match) -> str:
        return ", ".join(" ".join(v.DIGIT_NAMES[int(d)] for d in g) for g in match.groups())

    return re.sub(r"\b(\d{3}) (\d{3}) (\d{4})\b", groups, text)


def _load() -> dict[str, str]:
    global _facts
    if _facts is None:
        _facts = {}
        path = resolve_path(get_settings()["paths"]["xml_dir"]) / "udi_institucional.xml"
        try:
            root = ET.parse(path).getroot()
            for item in root.iter("pregunta"):
                texto = (item.findtext("texto") or "").lower()
                respuesta = " ".join((item.findtext("respuesta") or "").split())
                for key, (fragment, _) in INTENTS.items():
                    if fragment in texto and respuesta:
                        _facts[key] = _speak_digits(respuesta)
        except (OSError, ET.ParseError) as error:
            print(f"[institucion] no pude leer {path}: {error}")
    return _facts


def institutional_answer(question: str) -> str | None:
    """Solo para preguntas sobre la universidad en general (sin programa concreto)."""
    t = " ".join(v.words(question))
    about_udi = "udi" in t.split() or "universidad" in t or "contacto" in t
    facts = _load()
    for key, (_, triggers) in INTENTS.items():
        if key in facts and any(p in t for p in triggers):
            if key in ("telefono", "correo") and not about_udi:
                continue
            return facts[key]
    return None
