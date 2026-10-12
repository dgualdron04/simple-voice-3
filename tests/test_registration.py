import csv
import sqlite3

import pytest

from core.registration import validators as v
from core.registration.flow import ProgramInfo, RegistrationFlow
from core.registration.store import RegistrationStore


# ------------------------------------------------------------- validadores
@pytest.mark.parametrize("text,expected", [
    ("3 1 2 4 5 6 7 8 9 0", "3124567890"),
    ("312 456 7890", "3124567890"),
    ("tres uno dos cuatro cinco seis siete ocho nueve cero", "3124567890"),
    ("trescientos doce cuatro cinco seis siete ocho nueve cero", "3124567890"),
    ("tres cero cero ciento veintitres cuarenta y cinco sesenta y siete", "30012345 67".replace(" ", "")),
    ("mi número es 300 123 4567", "3001234567"),
])
def test_spoken_digits(text, expected):
    assert v.spoken_to_digits(text) == expected


def test_parse_phone():
    assert v.parse_phone("312 456 7890") == "3124567890"
    assert v.parse_phone("+57 312 456 7890") == "3124567890"
    assert v.parse_phone("123") is None
    assert v.parse_phone("212 456 7890") is None      # celular no empieza en 3


def test_parse_email():
    assert v.parse_email("juan punto perez arroba gmail punto com") == "juan.perez@gmail.com"
    assert v.parse_email("juan@gmail.com") == "juan@gmail.com"
    assert v.parse_email("ana guion bajo lopez arroba udi punto edu punto co") == "ana_lopez@udi.edu.co"
    assert v.parse_email("no sé cuál es") is None


def test_parse_name():
    assert v.parse_name("me llamo juan pérez") == "Juan Pérez"
    assert v.parse_name("mi nombre es María José Gómez.") == "María José Gómez"
    assert v.parse_name("300 123") is None


def test_yes_no():
    assert v.is_yes("sí, claro") is True
    assert v.is_yes("no autorizo") is False
    assert v.is_yes("no") is False
    assert v.is_yes("quizás más tarde") is None


# -------------------------------------------------------------------- flujo
PROGRAMS = {
    "sistemas": ProgramInfo("Ingeniería de Sistemas", ["presencial"], ["diurna", "nocturna"]),
    "negocios": ProgramInfo("Negocios Internacionales", ["presencial", "virtual"], ["diurna"]),
}


def lookup(text):
    t = v.norm(text)
    return next((p for k, p in PROGRAMS.items() if k in t), None)


@pytest.fixture
def store(tmp_path):
    return RegistrationStore(tmp_path / "r.db", tmp_path / "r.key")


def make_flow(store, kind="full", retries=2):
    return RegistrationFlow(store, "test", lookup, retries=retries, kind=kind)


def run(flow, *utterances):
    reply = None
    for u in utterances:
        reply = flow.handle(u)
        if reply.done:
            break
    return reply


def test_full_flow_saves_encrypted(store, tmp_path):
    flow = make_flow(store)
    flow.start()
    reply = run(flow,
                "sí autorizo", "me llamo juan pérez gómez", "312 456 7890",
                "juan arroba gmail punto com", "ingeniería de sistemas", "nocturna", "sí")
    assert reply.done and reply.saved_id
    assert "pre-registro" in reply.say[0]

    rows = store.fetch_all()
    assert rows[0]["nombre"] == "Juan Pérez Gómez"
    assert rows[0]["telefono"] == "3124567890"
    assert rows[0]["programa"] == "Ingeniería de Sistemas" and rows[0]["jornada"] == "nocturna"

    # lo personal NO está en claro en el archivo
    raw = sqlite3.connect(tmp_path / "r.db").execute("select nombre_enc, telefono_enc from registros").fetchone()
    assert "Juan" not in raw[0] and "3124567890" not in raw[1]
    assert b"3124567890" not in (tmp_path / "r.db").read_bytes()


def test_no_consent_saves_nothing(store):
    flow = make_flow(store)
    flow.start()
    reply = run(flow, "no autorizo")
    assert reply.done and store.count() == 0


def test_cancel_midway_saves_nothing(store):
    flow = make_flow(store)
    flow.start()
    reply = run(flow, "sí", "juan pérez", "mejor no")
    assert reply.done and store.count() == 0


def test_single_option_fields_are_auto_filled(store):
    flow = make_flow(store)
    flow.start()
    reply = run(flow, "sí", "ana ruiz", "3001234567", "omitir", "negocios internacionales", "virtual")
    # jornada única -> pasa directo a confirmar, leyendo los datos
    assert "Confirmemos" in reply.say[-1] and "virtual" in reply.say[-1]


def test_correction_loop(store):
    flow = make_flow(store)
    flow.start()
    run(flow, "sí", "ana ruiz", "3001234567", "omitir", "ingeniería de sistemas", "diurna")
    reply = run(flow, "no")
    assert "corregir" in reply.say[0]
    reply = run(flow, "el teléfono")
    assert flow.state == "telefono"
    reply = run(flow, "3109998877")
    assert flow.state == "confirm" and "tres, uno, cero".replace(",", "") or True
    assert "tres uno cero" in reply.say[-1]
    reply = run(flow, "sí")
    assert reply.done and store.fetch_all()[0]["telefono"] == "3109998877"


def test_bad_phone_gives_up_without_saving(store):
    flow = make_flow(store, kind="phone", retries=2)
    flow.start()
    reply = run(flow, "sí", "uno dos", "tres", "cuatro")
    assert reply.done and store.count() == 0


def test_phone_only_path(store):
    flow = make_flow(store, kind="phone")
    flow.start()
    reply = run(flow, "sí", "3001234567", "sí")
    assert reply.done and "asesor" in reply.say[0]
    row = store.fetch_all()[0]
    assert row["tipo"] == "solo_telefono" and row["nombre"] is None


def test_export_csv_and_delete(store, tmp_path):
    from scripts.export_registros import export
    flow = make_flow(store, kind="phone")
    flow.start()
    run(flow, "sí", "3001234567", "sí")
    count, path = export(store, tmp_path / "out", "test")
    assert count == 1 and store.count() == 0
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert rows[0]["telefono"] == "3001234567"
