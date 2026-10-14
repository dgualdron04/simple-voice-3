"""Validación y normalización de datos hablados (en código, no en el prompt)."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
import unicodedata

UNITS = {
    "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiun": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
        "ochenta": 80, "noventa": 90}
HUNDREDS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
            "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
            "setecientos": 700, "ochocientos": 800, "novecientos": 900}

YES_WORDS = {"si", "sii", "claro", "correcto", "afirmativo", "exacto", "dale", "listo", "ok",
             "okay", "vale", "perfecto", "efectivamente", "autorizo", "acepto", "adelante",
             "siga", "sigue", "sigamos", "continua", "continue"}     # «siga» = «adelante» en Colombia
NO_WORDS = {"no", "nop", "negativo", "incorrecto", "nada"}
SKIP_PHRASES = ("no tengo", "omitir", "omite", "omiti", "saltar", "salta", "prefiero no", "sin correo",
                "no quiero darlo", "no uso", "ninguno", "ninguna", "paso", "siguiente")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().strip())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9ñ\s]", " ", norm(text)).split()


# Muletillas que no deciden nada al inicio de una respuesta («pues sí», «bueno, claro», «eh… sí»).
YES_NO_FILLERS = {"pues", "bueno", "eh", "em", "emm", "este", "mm", "mmm", "ah", "ay", "oye", "mira", "a", "ver",
                  "senor", "senora", "robot", "hola", "y"}
# Respuestas afirmativas de varias palabras (comparadas al inicio, sin tildes).
YES_PHRASES = ("asi es", "de acuerdo", "esta bien", "por supuesto", "claro que si", "yo autorizo", "yo acepto",
               "lo autorizo", "los autorizo", "si senor", "eso es", "como no", "de una", "va pues", "con gusto",
               "hagale", "hagamosle", "obvio", "seguro que si")
# Afirmativas de una palabra que solo cuentan si la respuesta es corta («eso», «así»: Whisper oye «sí» como «así»).
SHORT_YES = {"eso", "asi", "aja", "sip", "sep", "obvio", "seguro", "hagale"}
NO_PHRASES = ("mejor no", "todavia no", "aun no", "ahorita no", "ahora no", "ni loco", "de ninguna manera",
              "no gracias", "jamas", "nunca", "nel")
NEGATED = {"autorizo", "acepto", "quiero", "gracias", "estoy", "deseo", "interesa", "doy", "puedo"}


def is_yes(text: str) -> bool | None:
    """True/False si es claramente sí/no; None si es ambiguo (se vuelve a preguntar).

    Conservador a propósito: lo usa el consentimiento de datos (Ley 1581), así que ante la duda devuelve None.
    """
    w = words(text)
    while w and w[0] in YES_NO_FILLERS:
        w = w[1:]
    if not w:
        return None
    head = " ".join(w[:5])
    if w[0] in NO_WORDS or w[:2] in (["de", "ninguna"],) or any(head.startswith(p) for p in NO_PHRASES):
        return False
    if "no" in w[:3] and NEGATED & set(w[:5]):
        return False
    if w[0] in YES_WORDS or (w[0] == "por" and "supuesto" in w):
        return True
    if any(head.startswith(p) for p in YES_PHRASES):
        return True
    if len(w) <= 2 and w[0] in SHORT_YES:
        return True
    # Un «sí» suelto de medio segundo a veces sale con una letra de más («Síl.», «SIV»).
    # Solo si la respuesta es esa única palabra; «sin» es una palabra real y no cuenta.
    if len(w) == 1 and re.fullmatch(r"si[a-z]", w[0]) and w[0] not in ("sin", "sir"):
        return True
    return None


def wants_skip(text: str) -> bool:
    t = " ".join(words(text))
    if any(p in t for p in SKIP_PHRASES) or t in ("no", "nada"):
        return True
    # «Omitir» suelto se transcribe mal («Oni tira.», «O mitir»): parecido, en una respuesta corta.
    compact = t.replace(" ", "")
    return len(t.split()) <= 3 and 4 <= len(compact) <= 9 and SequenceMatcher(None, compact, "omitir").ratio() >= 0.7


def spoken_to_digits(text: str) -> str:
    """'tres cero cero 123 4567' / 'trescientos doce' -> cadena de dígitos."""
    out: list[str] = []
    cur: int | None = None

    def flush():
        nonlocal cur
        if cur is not None:
            out.append(str(cur))
            cur = None

    for word in words(text):
        if word.isdigit():
            flush()
            out.append(word)
        elif word == "y":
            continue
        elif word in HUNDREDS:
            flush()
            cur = HUNDREDS[word]
        elif word in TENS:
            if cur is not None and cur % 100 != 0:
                flush()
            cur = (cur or 0) + TENS[word]
        elif word in UNITS:
            value = UNITS[word]
            can_extend = cur is not None and (
                (cur % 100 == 0 and cur != 0 and value <= 29)
                or (cur % 100 >= 30 and cur % 10 == 0 and value < 10)
            )
            if can_extend:
                cur += value
            else:
                flush()
                cur = value
        else:
            flush()
    flush()
    return "".join(out)


def is_partial_phone(digits: str) -> bool:
    """Dígitos que aún pueden completar un número válido (celular 3xx... / fijo 60...)."""
    return 0 < len(digits) < 10 and (digits.startswith("3") or digits.startswith("60") or digits.startswith("5"))


def parse_phone(text: str) -> str | None:
    """Celular colombiano (10 dígitos, empieza en 3) o fijo (7 dígitos, o 10 con 60)."""
    return phone_from_digits(spoken_to_digits(text))


def phone_from_digits(digits: str) -> str | None:
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and (digits.startswith("3") or digits.startswith("60")):
        return digits
    if len(digits) == 7 and not digits.startswith("3"):
        return digits
    return None


DIGIT_NAMES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]


def format_phone_for_speech(phone: str) -> str:
    groups = [phone[:3], phone[3:6], phone[6:]] if len(phone) == 10 else [phone[:3], phone[3:]]
    return ", ".join(" ".join(DIGIT_NAMES[int(d)] for d in g) for g in groups)


EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}")


def parse_email(text: str) -> str | None:
    t = f" {norm(text)} "
    t = t.replace(" arroba ", "@").replace(" at ", "@")
    t = re.sub(r"\s*\bguion bajo\b\s*", "_", t)
    t = re.sub(r"\s*\bguion\b\s*", "-", t)
    t = re.sub(r"\s*\bpunto\b\s*", ".", t)
    t = re.sub(r"\s+", "", t)
    match = EMAIL_RE.search(t)
    return match.group(0) if match else None


def format_email_for_speech(email: str) -> str:
    def spoken(s: str) -> str:
        return s.replace(".", " punto ").replace("_", " guion bajo ").replace("-", " guion ")

    local, domain = email.split("@", 1)
    return f"{spoken(local)} arroba {spoken(domain)}"


NAME_PREFIXES = re.compile(
    r"^(?:hola[, ]+)?(?:pues[, ]+)?(?:mi nombre completo es|mi nombre es|me llamo|mi nombre|soy|yo soy|es)\s+",
    re.IGNORECASE,
)


def parse_name(text: str) -> str | None:
    t = NAME_PREFIXES.sub("", text.strip().strip(".,;:!?¿¡"))
    t = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s'-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = t.split()
    if not parts or len(parts) > 5 or len(t) > 60 or any(len(p) < 2 for p in parts):
        return None
    return " ".join(p.capitalize() for p in parts)


def pick_choice(text: str, options: dict[str, list[str]]) -> str | None:
    """options: valor -> sinónimos (sin tildes). Devuelve el valor si hay uno solo."""
    t = " ".join(words(text))
    found = [v for v, syns in options.items() if any(re.search(rf"\b{re.escape(s)}\b", t) for s in syns)]
    return found[0] if len(found) == 1 else None
