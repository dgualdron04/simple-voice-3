import json

import pytest

from core.config import load_robot
from core.dialog import DialogEngine
from core.registration.store import RegistrationStore
from core.usage_log import UsageLog, redact_output, redact_text


@pytest.mark.parametrize("raw,must_not_contain", [
    ("mi número es 304 338 0795", "0795"),
    ("304-338-0795", "338"),
    ("tres cero cuatro tres tres ocho cero siete nueve cinco", "cuatro"),
    ("mi correo es juan.perez@gmail.com", "juan"),
    ("juan punto perez arroba gmail punto com", "arroba"),
    ("me llamo Carlos Andrés Ruiz", "Carlos"),
    ("mi nombre es María", "María"),
])
def test_redaction_removes_personal_data(raw, must_not_contain):
    assert must_not_contain not in redact_text(raw)


def test_redaction_keeps_useful_text():
    assert redact_text("cuánto cuesta derecho en la noche") == "cuánto cuesta derecho en la noche"
    assert "11 programas" in redact_text("tenemos 11 programas")          # números cortos no son datos personales


def test_confirmation_summary_is_never_logged():
    assert redact_output("Confirmemos tus datos: nombre Juan; teléfono tres cero cuatro...") == "Confirmemos tus datos: <redactado>"


def test_events_are_jsonl_and_survive_failures(tmp_path):
    log = UsageLog(tmp_path / "logs", "laptop")
    log.event("start", x=1)
    log.user_turn("hola robot zuu", {"route": "wake:greeting", "session": False}, {"stt_ms": 900},
                  [{"say": True, "text": "Hola"}], 3.2)
    lines = (next((tmp_path / "logs").glob("uso_*.jsonl"))).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in lines]
    assert rows[0]["kind"] == "start" and rows[1]["route"] == "wake:greeting" and rows[1]["heard"] == "hola robot zuu"


def test_sensitive_flow_states_hide_everything(tmp_path):
    log = UsageLog(tmp_path / "logs", "laptop")
    log.user_turn("me llamo Ana Ruiz y mi cédula es 1098765432", {"route": "flow:nombre", "sensitive": True,
                                                                  "flow_state": "nombre"}, None, [], 1)
    text = next((tmp_path / "logs").glob("uso_*.jsonl")).read_text(encoding="utf-8")
    assert "Ana" not in text and "1098765432" not in text and "<dato:nombre>" in text


def test_log_text_off_keeps_only_metadata(tmp_path):
    log = UsageLog(tmp_path / "logs", "laptop", log_text=False)
    log.user_turn("algo privado", {"route": "llm:free"}, None, [{"say": True, "text": "respuesta"}], 1)
    text = next((tmp_path / "logs").glob("uso_*.jsonl")).read_text(encoding="utf-8")
    assert "privado" not in text and "respuesta" not in text and "llm:free" in text


def test_old_logs_are_purged(tmp_path):
    import os
    import time
    d = tmp_path / "logs"
    d.mkdir()
    old = d / "uso_2020-01-01.jsonl"
    old.write_text("{}")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    UsageLog(d, "laptop", retention_days=14)
    assert not old.exists()


# ---------------------------------------------------------------- rutas del motor
@pytest.fixture
def engine(tmp_path):
    return DialogEngine(load_robot("laptop"), store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"))


def route(engine, text):
    engine.handle(text)
    return engine.trace["route"]


def test_engine_traces_why(engine):
    assert route(engine, "cuánto cuesta derecho") == "ignored:no_wake"
    assert route(engine, "robot zuu") == "wake:greeting"
    assert route(engine, "cuánto cuesta derecho").startswith("knowledge:")
    assert route(engine, "quién es el rector") == "knowledge:faq"
    assert route(engine, "qué opinas del café") == "llm:free"
    assert route(engine, "quién es Elon Musk") == "declined:factual"
    assert route(engine, "mesa") == "ignored:single_word"
    assert route(engine, "dame la mano") == "gesture:dar_mano"


# ---------------------------------------------------------------- memoria de sesión
def test_session_memory_prefills_registration(engine):
    engine.handle("robot zuu hola")
    engine.handle("me llamo Carlos Ruiz")
    assert engine.memory["nombre"] == "Carlos Ruiz"
    engine.handle("háblame de ingeniería civil")
    out = " ".join(a.text for a in engine.handle("quiero inscribirme") if hasattr(a, "text"))
    assert "Ley 1581" in out
    first = " ".join(a.text for a in engine.handle("sí autorizo") if hasattr(a, "text"))
    assert "Carlos Ruiz" in first                    # confirma, no asume
    second = " ".join(a.text for a in engine.handle("sí") if hasattr(a, "text"))
    assert "teléfono" in second                      # nombre confirmado -> siguiente dato


def test_prefill_can_be_declined(engine):
    engine.handle("robot zuu hola")
    engine.handle("me llamo Carlos Ruiz")
    engine.handle("quiero inscribirme")
    engine.handle("sí autorizo")
    asked = " ".join(a.text for a in engine.handle("no") if hasattr(a, "text"))
    assert "nombre completo" in asked


def test_memory_is_forgotten_when_session_ends(tmp_path):
    clock = [1000.0]
    e = DialogEngine(load_robot("laptop"), store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"),
                     clock=lambda: clock[0])
    e.handle("robot zuu hola")
    e.handle("me llamo Carlos Ruiz")
    assert e.memory
    clock[0] += 500                                   # la persona se fue
    e.handle("charla de fondo sin activar al robot")
    assert not e.memory and e.last_program is None


def test_remembered_names_never_reach_the_log(tmp_path):
    log = UsageLog(tmp_path / "logs", "laptop")
    log.names = {"Carlos", "Ruiz"}
    log.user_turn("hola soy carlos", {"route": "x"}, None,
                  [{"say": True, "text": "Mucho gusto, Carlos."}, {"say": True, "text": "¿Tu nombre es Carlos Ruiz?"}], 1)
    text = next((tmp_path / "logs").glob("uso_*.jsonl")).read_text(encoding="utf-8")
    assert "Carlos" not in text and "Ruiz" not in text
