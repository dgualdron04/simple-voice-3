#Remove-Item data\zuu.db
#python -m src.xml_ingest

from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import re
from src.config import get_settings

settings = get_settings()

DB_PATH = Path(settings["paths"]["database"])
XML_DIR = Path(settings["paths"]["xml_dir"])


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def tag_name(tag: str) -> str:
    """
    Limpia nombres de tags XML.
    Ejemplo:
    {namespace}programa -> programa
    """
    return tag.split("}")[-1].lower().replace("-", "_")


def own_text(element) -> str:
    """
    Obtiene solo el texto directo del nodo, no el texto de todos sus hijos.
    """
    return clean_text(element.text or "")


def full_text(element) -> str:
    """
    Obtiene todo el texto interno del nodo, incluyendo hijos.
    """
    return clean_text(" ".join(t.strip() for t in element.itertext() if t.strip()))


def find_text(root, possible_names):
    """
    Busca el primer tag que coincida con alguno de los nombres posibles.
    """
    possible_names = [name.lower() for name in possible_names]

    for element in root.iter():
        name = tag_name(element.tag)

        if name in possible_names:
            text = own_text(element)

            if not text:
                text = full_text(element)

            if text:
                return text

    return ""


def find_nested_text(root, path):
    """
    Busca datos anidados.
    Ejemplo:
    costo.diurno
    costo.nocturno
    """
    current_elements = [root]

    for part in path:
        next_elements = []

        for element in current_elements:
            for child in list(element):
                if tag_name(child.tag) == part:
                    next_elements.append(child)

        current_elements = next_elements

        if not current_elements:
            return ""

    return full_text(current_elements[0])


def xml_to_key_lines(element, prefix=""):
    """
    Convierte el XML en texto plano con rutas tipo:
    programa.nombre: Ingeniería de Sistemas
    programa.costo.diurno: 1699200
    """
    name = tag_name(element.tag)
    current_path = f"{prefix}.{name}" if prefix else name

    lines = []

    text = own_text(element)
    if text:
        lines.append(f"{current_path}: {text}")

    for key, value in element.attrib.items():
        lines.append(f"{current_path}@{key}: {value}")

    for child in list(element):
        lines.extend(xml_to_key_lines(child, current_path))

    return lines


def ensure_schema(conn):
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        titulo TEXT,
        duracion TEXT,
        modalidad TEXT,
        sede TEXT,
        valor_diurna TEXT,
        valor_nocturna TEXT,
        malla TEXT,
        trabajo TEXT,
        raw_text TEXT,
        source_file TEXT
    )
    """)

    cur.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
    USING fts5(
        title,
        content,
        source_file,
        program_id UNINDEXED
    )
    """)

    conn.commit()


def extract_program_data(xml_path: Path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    nombre = (
        find_text(root, ["nombre", "titulo", "name"])
        or xml_path.stem.replace("_", " ").title()
    )

    data = {
        "nombre": nombre,
        "titulo": find_text(root, ["titulo", "title"]),
        "duracion": find_text(root, ["duracion", "duración", "semestres"]),
        "modalidad": find_text(root, ["modalidad"]),
        "sede": find_text(root, ["sede", "campus"]),
        "valor_diurna": (
            find_nested_text(root, ["costo", "diurno"])
            or find_nested_text(root, ["valor", "diurno"])
            or find_text(root, ["valor_diurna", "costo_diurno"])
        ),
        "valor_nocturna": (
            find_nested_text(root, ["costo", "nocturno"])
            or find_nested_text(root, ["valor", "nocturno"])
            or find_text(root, ["valor_nocturna", "costo_nocturno"])
        ),
        "malla": find_text(root, ["malla", "plan_estudios", "plan_de_estudios"]),
        "trabajo": find_text(root, ["trabajo", "campo_laboral", "perfil_ocupacional"]),
        "raw_text": "\n".join(xml_to_key_lines(root)),
        "source_file": str(xml_path),
    }

    return data


def ingest_xml_files():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    cur = conn.cursor()

    xml_files = list(XML_DIR.glob("*.xml"))

    if not xml_files:
        print(f"No encontré archivos XML en {XML_DIR}")
        return

    for xml_path in xml_files:
        print(f"Ingestando: {xml_path}")

        data = extract_program_data(xml_path)

        cur.execute("DELETE FROM programs WHERE source_file = ?", (str(xml_path),))
        cur.execute("DELETE FROM documents_fts WHERE source_file = ?", (str(xml_path),))

        cur.execute("""
        INSERT INTO programs (
            nombre,
            titulo,
            duracion,
            modalidad,
            sede,
            valor_diurna,
            valor_nocturna,
            malla,
            trabajo,
            raw_text,
            source_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["nombre"],
            data["titulo"],
            data["duracion"],
            data["modalidad"],
            data["sede"],
            data["valor_diurna"],
            data["valor_nocturna"],
            data["malla"],
            data["trabajo"],
            data["raw_text"],
            data["source_file"],
        ))

        program_id = cur.lastrowid

        cur.execute("""
        INSERT INTO documents_fts (
            title,
            content,
            source_file,
            program_id
        )
        VALUES (?, ?, ?, ?)
        """, (
            data["nombre"],
            data["raw_text"],
            data["source_file"],
            program_id,
        ))

    conn.commit()
    conn.close()

    print("Ingesta XML terminada correctamente.")


if __name__ == "__main__":
    ingest_xml_files()