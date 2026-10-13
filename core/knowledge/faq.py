"""Preguntas frecuentes y personas desde los XML (<pregunta><texto/><respuesta/></pregunta>, <persona>).

Respuesta exacta, sin LLM: lo curado en el XML es lo que se dice. Cubre «quién es el rector»,
«quién fundó la UDI», «quién te creó», «de dónde es Jairo Castro», etc.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from core.config import get_settings, resolve_path
from core.registration import validators as v

STOP = {"de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "en", "es", "son", "que",
        "a", "al", "se", "me", "te", "tu", "mi", "por", "para", "con", "su", "sus", "lo", "ya", "pero", "si",
        "no", "eso", "esto", "hay", "ser", "esta", "puede", "puedes", "quiero", "saber", "dime", "favor",
        "ahora", "bueno", "oye", "hola", "entonces", "pues", "era", "fue", "ha", "han", "como", "tiene", "tienen"}
SYNONYMS = {
    "crear": {"crearon", "creo", "creaste", "creador", "creado", "creada", "crea", "hizo", "hicieron", "hiciste",
              "desarrollo", "desarrollaron", "desarrollador", "programo", "programaron", "construyo", "inventor"},
    "fundar": {"fundo", "fundador", "fundada", "fundaron", "fundacion", "origen", "inicio", "empezo", "comenzo"},
    "dueno": {"dueno", "propietario", "propietaria", "duena"},
}
SYN_LOOKUP = {w: k for k, ws in SYNONYMS.items() for w in ws}

THRESHOLD = 0.6


def _stem(word: str) -> str:
    word = SYN_LOOKUP.get(word, word)
    return word[:5]


def content_tokens(text: str, drop: set[str] = frozenset()) -> list[str]:
    return [_stem(w) for w in v.words(text) if w not in STOP and w not in drop and len(w) > 1]


@dataclass
class Entry:
    tokens: set[str]
    answer: str


@dataclass
class Person:
    name_tokens: set[str]
    summary: str


_entries: list[Entry] | None = None
_persons: list[Person] | None = None


def _load() -> None:
    global _entries, _persons
    if _entries is not None:
        return
    _entries, _persons = [], []
    xml_dir = resolve_path(get_settings()["paths"]["xml_dir"])
    for path in sorted(xml_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for item in root.iter("pregunta"):
            question = " ".join((item.findtext("texto") or "").split())
            answer = " ".join((item.findtext("respuesta") or "").split())
            if question and answer:
                _entries.append(Entry(set(content_tokens(question)), answer))
        persona = root.find(".//persona")
        if persona is not None:
            name = persona.findtext("nombre") or ""
            summary = " ".join((root.findtext("resumen") or "").split())
            if name and summary:
                _persons.append(Person(set(content_tokens(name)), summary))


def _personalize(answer: str, robot_name: str) -> str:
    return re.sub(r"\bZUU\b|\bZuu\b|\bZú\b", robot_name, answer)


def faq_answer(question: str, robot_name: str, aliases: set[str]) -> str | None:
    _load()
    drop = {v.norm(robot_name)} | aliases
    tokens = set(content_tokens(question, drop))
    if not tokens:
        return None

    # «¿quién es Jairo Castro?» -> la persona cuyo nombre completo está en la pregunta (y solo una)
    if {"quien"} & tokens or len(tokens) <= 3:
        named = [p for p in _persons if len(p.name_tokens) >= 2 and p.name_tokens <= tokens]
        if len(named) == 1 and tokens - named[0].name_tokens <= {"quien"}:
            return _personalize(named[0].summary, robot_name)

    best, best_score = None, 0.0
    for entry in _entries:
        overlap = len(tokens & entry.tokens)
        if overlap < min(2, len(tokens)):
            continue
        score = 2 * overlap / (len(tokens) + len(entry.tokens))
        if score > best_score:
            best, best_score = entry, score
    if best is not None and best_score >= THRESHOLD:
        return _personalize(best.answer, robot_name)
    return None


def reset() -> None:
    global _entries, _persons
    _entries = _persons = None
