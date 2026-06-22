#Remove-Item data\zuu.db
#python -m src.xml_ingest
# python -m src.xml_ingest

from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import re

from src.config import get_settings, resolve_path

settings = get_settings()

DB_PATH = resolve_path(settings["paths"]["database"])
XML_DIR = resolve_path(settings["paths"]["xml_dir"])


PROGRAM_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "nombre": "TEXT",
    "titulo": "TEXT",
    "duracion": "TEXT",
    "modalidad": "TEXT",
    "jornada": "TEXT",
    "codigo_snies": "TEXT",
    "resolucion": "TEXT",
    "fecha_registro_calificado": "TEXT",
    "fecha_vigencia_registro_calificado": "TEXT",
    "vigencia_registro_calificado": "TEXT",
    "creditos": "TEXT",
    "periodicidad_admision": "TEXT",
    "sede": "TEXT",
    "valor_diurna": "TEXT",
    "valor_nocturna": "TEXT",
    "valor_virtual": "TEXT",
    "valor_general": "TEXT",
    "estado": "TEXT",
    "observacion": "TEXT",
    "telefono": "TEXT",
    "correo": "TEXT",
    "malla": "TEXT",
    "trabajo": "TEXT",
    "raw_text": "TEXT",
    "source_file": "TEXT",
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def fix_mojibake(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    for _ in range(2):
        if "Ã" in text or "Â" in text:
            try:
                fixed = text.encode("latin1").decode("utf-8")
                if fixed != text:
                    text = fixed
                    continue
            except UnicodeError:
                pass

            text = text.replace("Â", "")
            break

    return text


def tag_name(tag: str) -> str:
    return tag.split("}")[-1].lower().replace("-", "_").strip()


def own_text(element) -> str:
    return clean_text(fix_mojibake(element.text or ""))


def full_text(element) -> str:
    text = " ".join(t.strip() for t in element.itertext() if t and t.strip())
    return clean_text(fix_mojibake(text))


def read_xml_file(xml_path: Path) -> str:
    try:
        text = xml_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = xml_path.read_text(encoding="latin1")

    text = text.strip()

    # Quita bloques markdown tipo ```xml ... ```
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # Si hay basura antes del XML, recorta desde el primer <
    first_xml = text.find("<")
    if first_xml > 0:
        text = text[first_xml:]

    # Si hay basura después del cierre, recorta hasta el último >
    last_xml = text.rfind(">")
    if last_xml != -1:
        text = text[:last_xml + 1]

    return text.strip()


def parse_xml(xml_path: Path):
    xml_text = read_xml_file(xml_path)

    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise ET.ParseError(f"{xml_path.name}: {error}") from error


def find_text(root, possible_names):
    possible_names = [name.lower() for name in possible_names]

    for element in root.iter():
        name = tag_name(element.tag)

        if name in possible_names:
            text = own_text(element) or full_text(element)

            if text:
                return text

    return ""


def find_nested_text(root, path):
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


def find_first_element(root, possible_names):
    possible_names = [name.lower() for name in possible_names]

    for element in root.iter():
        if tag_name(element.tag) in possible_names:
            return element

    return None


def xml_to_key_lines(element, prefix=""):
    name = tag_name(element.tag)
    current_path = f"{prefix}.{name}" if prefix else name

    lines = []

    text = own_text(element)
    if text:
        lines.append(f"{current_path}: {text}")

    for key, value in element.attrib.items():
        value = fix_mojibake(str(value))
        lines.append(f"{current_path}@{key}: {value}")

    for child in list(element):
        lines.extend(xml_to_key_lines(child, current_path))

    return lines

def is_program_xml(root) -> bool:
    root_name = tag_name(root.tag)

    return root_name in [
        "programa",
        "carrera",
        "program",
        "programa_academico",
    ]


def extract_general_document_data(xml_path: Path):
    root = parse_xml(xml_path)

    title = (
        find_text(root, ["titulo", "title", "nombre", "name"])
        or xml_path.stem.replace("_", " ").title()
    )

    document_type = root.attrib.get("tipo", "general")
    document_id = root.attrib.get("id", xml_path.stem)

    content_parts = [
        f"Tipo de documento: {document_type}",
        f"ID del documento: {document_id}",
        "",
        "\n".join(xml_to_key_lines(root)),
    ]

    return {
        "title": title,
        "content": "\n".join(part for part in content_parts if part.strip()),
        "source_file": str(xml_path),
    }


def insert_general_document(conn, data):
    cur = conn.cursor()

    source_file = data["source_file"]

    cur.execute("DELETE FROM documents_fts WHERE source_file = ?", (source_file,))
    cur.execute("DELETE FROM program_fields WHERE source_file = ?", (source_file,))
    cur.execute("DELETE FROM programs WHERE source_file = ?", (source_file,))

    cur.execute("""
    INSERT INTO documents_fts (
        title,
        content,
        source_file,
        program_id
    )
    VALUES (?, ?, ?, NULL)
    """, (
        data.get("title", ""),
        data.get("content", ""),
        source_file,
    ))

def xml_to_field_rows(element, prefix=""):
    name = tag_name(element.tag)
    current_path = f"{prefix}.{name}" if prefix else name

    rows = []

    text = own_text(element)
    if text:
        rows.append({
            "field_path": current_path,
            "field_name": name,
            "field_value": text,
        })

    for key, value in element.attrib.items():
        rows.append({
            "field_path": f"{current_path}@{key}",
            "field_name": key.lower(),
            "field_value": fix_mojibake(str(value)),
        })

    for child in list(element):
        rows.extend(xml_to_field_rows(child, current_path))

    return rows


def extract_malla(root) -> str:
    malla = find_first_element(root, ["malla", "plan_estudios", "plan_de_estudios"])

    if malla is None:
        return ""

    partes = []

    for semestre in list(malla):
        if tag_name(semestre.tag) != "semestre":
            continue

        numero = semestre.attrib.get("numero", "").strip()
        asignaturas = []

        for child in list(semestre):
            if tag_name(child.tag) == "asignatura":
                text = full_text(child)
                if text:
                    asignaturas.append(text)

        if asignaturas:
            if numero:
                partes.append(f"Semestre {numero}: {', '.join(asignaturas)}")
            else:
                partes.append(", ".join(asignaturas))

    if partes:
        return " | ".join(partes)

    return full_text(malla)


def ensure_schema(conn):
    cur = conn.cursor()

    columns_sql = ",\n".join(
        f"{name} {definition}"
        for name, definition in PROGRAM_COLUMNS.items()
    )

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS programs (
        {columns_sql}
    )
    """)

    # Migración: si la tabla ya existía, agrega columnas nuevas sin borrar datos.
    cur.execute("PRAGMA table_info(programs)")
    existing_columns = {row[1] for row in cur.fetchall()}

    for column, definition in PROGRAM_COLUMNS.items():
        if column not in existing_columns and column != "id":
            cur.execute(f"ALTER TABLE programs ADD COLUMN {column} {definition}")

    cur.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
    USING fts5(
        title,
        content,
        source_file,
        program_id UNINDEXED
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS program_fields (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        program_id INTEGER,
        field_path TEXT,
        field_name TEXT,
        field_value TEXT,
        source_file TEXT
    )
    """)

    conn.commit()


def extract_program_data(xml_path: Path):
    root = parse_xml(xml_path)

    nombre = (
        find_text(root, ["nombre", "name"])
        or xml_path.stem.replace("_", " ").title()
    )

    data = {
        "nombre": nombre,
        "titulo": find_text(root, ["titulo", "title"]),
        "duracion": find_text(root, ["duracion", "duración", "semestres"]),
        "modalidad": find_text(root, ["modalidad"]),
        "jornada": find_text(root, ["jornada"]),
        "codigo_snies": find_text(root, ["codigo_snies", "snies", "código_snies"]),
        "resolucion": find_text(root, ["resolucion", "resolución"]),
        "fecha_registro_calificado": find_text(root, ["fecha_registro_calificado"]),
        "fecha_vigencia_registro_calificado": find_text(root, ["fecha_vigencia_registro_calificado"]),
        "vigencia_registro_calificado": find_text(root, ["vigencia_registro_calificado"]),
        "creditos": find_text(root, ["creditos", "créditos"]),
        "periodicidad_admision": find_text(root, ["periodicidad_admision", "periodicidad_admisión"]),
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
        "valor_virtual": (
            find_nested_text(root, ["costo", "virtual"])
            or find_nested_text(root, ["valor", "virtual"])
            or find_text(root, ["valor_virtual", "costo_virtual"])
        ),
        "valor_general": (
            find_text(root, ["valor", "costo", "matricula", "matrícula"])
        ),
        "estado": find_text(root, ["estado"]),
        "observacion": find_text(root, ["observacion", "observación", "observaciones"]),
        "telefono": find_nested_text(root, ["contacto", "telefono"]) or find_text(root, ["telefono", "teléfono"]),
        "correo": find_nested_text(root, ["contacto", "correo"]) or find_text(root, ["correo", "email"]),
        "malla": extract_malla(root),
        "trabajo": find_text(root, ["trabajo", "campo_laboral", "perfil_ocupacional", "perfil_laboral"]),
        "raw_text": "\n".join(xml_to_key_lines(root)),
        "source_file": str(xml_path),
        "field_rows": xml_to_field_rows(root),
    }

    return data


def insert_program(conn, data):
    cur = conn.cursor()

    source_file = data["source_file"]

    cur.execute("DELETE FROM programs WHERE source_file = ?", (source_file,))
    cur.execute("DELETE FROM documents_fts WHERE source_file = ?", (source_file,))
    cur.execute("DELETE FROM program_fields WHERE source_file = ?", (source_file,))

    insert_columns = [column for column in PROGRAM_COLUMNS.keys() if column != "id"]

    placeholders = ", ".join(["?"] * len(insert_columns))
    column_sql = ", ".join(insert_columns)

    values = [data.get(column, "") for column in insert_columns]

    cur.execute(f"""
    INSERT INTO programs ({column_sql})
    VALUES ({placeholders})
    """, values)

    program_id = cur.lastrowid

    content_parts = [
        data.get("raw_text", ""),
        "",
        f"Nombre del programa: {data.get('nombre', '')}",
        f"Título otorgado: {data.get('titulo', '')}",
        f"Duración: {data.get('duracion', '')}",
        f"Modalidad: {data.get('modalidad', '')}",
        f"Jornada: {data.get('jornada', '')}",
        f"Código SNIES: {data.get('codigo_snies', '')}",
        f"Resolución: {data.get('resolucion', '')}",
        f"Créditos: {data.get('creditos', '')}",
        f"Estado: {data.get('estado', '')}",
        f"Observación: {data.get('observacion', '')}",
        f"Valor diurno: {data.get('valor_diurna', '')}",
        f"Valor nocturno: {data.get('valor_nocturna', '')}",
        f"Valor virtual: {data.get('valor_virtual', '')}",
        f"Teléfono: {data.get('telefono', '')}",
        f"Correo: {data.get('correo', '')}",
        f"Malla curricular: {data.get('malla', '')}",
    ]

    content = "\n".join(part for part in content_parts if part.strip())

    cur.execute("""
    INSERT INTO documents_fts (
        title,
        content,
        source_file,
        program_id
    )
    VALUES (?, ?, ?, ?)
    """, (
        data.get("nombre", ""),
        content,
        source_file,
        program_id,
    ))

    for row in data.get("field_rows", []):
        if not row.get("field_value"):
            continue

        cur.execute("""
        INSERT INTO program_fields (
            program_id,
            field_path,
            field_name,
            field_value,
            source_file
        )
        VALUES (?, ?, ?, ?, ?)
        """, (
            program_id,
            row["field_path"],
            row["field_name"],
            row["field_value"],
            source_file,
        ))

    return program_id


def ingest_xml_files():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    xml_files = sorted(XML_DIR.glob("*.xml"))

    if not xml_files:
        print(f"No encontré archivos XML en {XML_DIR}")
        conn.close()
        return

    total_ok = 0
    total_error = 0

    for xml_path in xml_files:
        print(f"Ingestando: {xml_path}")

        try:
            root = parse_xml(xml_path)

            if is_program_xml(root):
                data = extract_program_data(xml_path)
                insert_program(conn, data)
            else:
                data = extract_general_document_data(xml_path)
                insert_general_document(conn, data)

            total_ok += 1

        except Exception as error:
            total_error += 1
            print(f"ERROR ingestando {xml_path.name}: {error}")

    conn.commit()
    conn.close()

    print("=" * 60)
    print("Ingesta XML terminada.")
    print(f"Archivos correctos: {total_ok}")
    print(f"Archivos con error: {total_error}")
    print(f"Base de datos: {DB_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    ingest_xml_files()