import re
import unicodedata
from difflib import get_close_matches


DOMAIN_WORDS = [
    "udi",
    "universidad",
    "investigación",
    "desarrollo",
    "ingeniería",
    "sistemas",
    "diurno",
    "diurna",
    "nocturno",
    "nocturna",
    "modalidad",
    "presencial",
    "malla",
    "materias",
    "asignaturas",
    "duración",
    "semestres",
    "costo",
    "valor",
    "precio",
    "matrícula",
    "trabajo",
    "laboral",
    "administración",
    "empresas",
    "comunicación",
    "social",
    "criminalística",
    "derecho",
    "diseño",
    "gráfico",
    "industrial",
    "civil",
    "electrónica",
    "negocios",
    "internacionales",
    "snies",
    "resolución",
    "créditos",
    "contacto",
    "correo",
    "teléfono",
]


COMMON_FIXES = {
    "ingenieria": "ingeniería",
    "ingeneria": "ingeniería",
    "ingeniera": "ingeniería",
    "ingenierá": "ingeniería",
    "ingeniería sistemas": "ingeniería de sistemas",
    "ingenieria sistemas": "ingeniería de sistemas",

    "sidestemos": "sistemas",
    "sistemos": "sistemas",
    "sistema": "sistemas",
    "sis temas": "sistemas",
    "estemos": "sistemas",
    "estem os": "sistemas",
    "estudiosis temas": "estudio sistemas",
    "estudio sis temas": "estudio sistemas",

    "cuantos cuesta": "cuánto cuesta",
    "cuantos dura": "cuánto dura",
    "cuanto duras": "cuánto dura",
    "cuantos duros": "cuánto dura",
    "cuanto duros": "cuánto dura",

    "de dia": "diurna",
    "de día": "diurna",
    "de noche": "nocturna",

    "matricula": "matrícula",
    "duracion": "duración",
    "modalida": "modalidad",

    "negocio internacionales": "negocios internacionales",
    "negocio internacional": "negocios internacionales",
    "me suele sobre": "háblame sobre",
    "me hable sobre": "háblame sobre",
    "háblame sobre guinea sistemas": "háblame sobre ingeniería de sistemas",
    "guinea sistemas": "ingeniería de sistemas",
    "ingeniería de zeus": "ingeniería de sistemas",
    "ingenieria de zeus": "ingeniería de sistemas",
    "ingeniería de sus": "ingeniería de sistemas",
    "ingenieria de sus": "ingeniería de sistemas",

    "negocio internacionales": "negocios internacionales",
    "negocio internacional": "negocios internacionales",
    "me suele sobre": "háblame sobre",
    "me hable sobre": "háblame sobre",

    "administracion empresas": "administración de empresas",
    "administración empresas": "administración de empresas",
    "comunicacion social": "comunicación social",
    "criminalistica": "criminalística",
    "diseno grafico": "diseño gráfico",
    "diseño grafico": "diseño gráfico",
    "diseno industrial": "diseño industrial",
    "ingenieria civil": "ingeniería civil",
    "ingenieria electronica": "ingeniería electrónica",
    "ingenieria industrial": "ingeniería industrial",

    "guinea sistemas": "ingeniería de sistemas",
    "ingeniería de zeus": "ingeniería de sistemas",
    "ingenieria de zeus": "ingeniería de sistemas",
    "ingeniería de sus": "ingeniería de sistemas",
    "ingenieria de sus": "ingeniería de sistemas",

    "graves sistemas": "ingeniería de sistemas",
    "grave sistemas": "ingeniería de sistemas",
    "graves de sistemas": "ingeniería de sistemas",
    "sede electronica": "ingeniería electrónica",
    "sede electrónica": "ingeniería electrónica",
    "me puedes hablar sede electronica": "me puedes hablar sobre ingeniería electrónica",
    "me puedes hablar sede electrónica": "me puedes hablar sobre ingeniería electrónica",
    "administrando empresas": "administración de empresas",
    "administracion empresas": "administración de empresas",
    "comunicacion social": "comunicación social",
    "enemigos internacionales": "negocios internacionales",

    "en centro de": "entre",
    "en centro": "entre",
    "con el mejor sistemas son": "cual es mejor sistemas o",
    "con el mejor sistema son": "cual es mejor sistemas o",
    "me puede dar una lista": "dame una lista",
    "me puedes dar una lista": "dame una lista",
    "carreras que ofertas": "carreras que oferta",
}


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def apply_common_fixes(text: str) -> str:
    fixed = text.lower()

    for wrong, right in COMMON_FIXES.items():
        fixed = fixed.replace(wrong, right)

    return normalize_spaces(fixed)


def fix_domain_words(text: str) -> str:
    """
    Corrige tokens raros si se parecen a palabras importantes del dominio.
    No corrige todas las palabras, solo las que son parecidas a vocabulario UDI.
    """
    words = text.split()
    corrected = []

    domain_plain = {
        strip_accents(word): word
        for word in DOMAIN_WORDS
    }

    for word in words:
        clean = strip_accents(word.lower())

        if len(clean) < 5:
            corrected.append(word)
            continue

        matches = get_close_matches(
            clean,
            domain_plain.keys(),
            n=1,
            cutoff=0.78
        )

        if matches:
            corrected.append(domain_plain[matches[0]])
        else:
            corrected.append(word)

    return normalize_spaces(" ".join(corrected))


def fix_stt_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower()
    text = normalize_spaces(text)

    text = apply_common_fixes(text)
    text = fix_domain_words(text)

    # Corrección de frases completas después de corregir palabras.
    text = text.replace("ingeniería sistemas", "ingeniería de sistemas")
    text = text.replace("programa ingeniería", "programa de ingeniería")

    return normalize_spaces(text)