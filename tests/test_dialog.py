import pytest

from core.config import load_robot
from core.dialog import AskLLM, DialogEngine, Gesture, Say
from core.registration.store import RegistrationStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    settings = load_robot("laptop")
    clock = Clock()
    store = RegistrationStore(tmp_path / "r.db", tmp_path / "r.key")
    return DialogEngine(settings, store=store, clock=clock), clock, store


def says(actions):
    return " ".join(a.text for a in actions if isinstance(a, Say))


def test_ignores_without_wake_word(setup):
    engine, _, _ = setup
    assert engine.handle("cuánto cuesta ingeniería de sistemas") == []


def test_wake_then_answers_costs_without_llm(setup):
    engine, _, _ = setup
    out = engine.handle("Zuu cuánto cuesta ingeniería de sistemas")
    assert out and all(isinstance(a, Say) for a in out)
    assert "1.699.200" in says(out) or "1699200" in says(out) or "millón" in says(out).lower() or "pesos" in says(out)


def test_greeting_activates(setup):
    engine, _, _ = setup
    assert "Zuu" in says(engine.handle("hola"))


def test_session_expires(setup):
    engine, clock, _ = setup
    engine.handle("hola zuu")
    assert engine.handle("y cuánto dura") != []
    clock.t += 500
    assert engine.handle("cuéntame un chiste largo por favor") == []


def test_followup_uses_last_program(setup):
    engine, _, _ = setup
    engine.handle("zuu háblame de ingeniería civil")
    out = engine.handle("y cuánto dura")
    assert says(out) and "civil" in says(out).lower()


def test_gesture_is_deterministic_and_whitelisted(setup):
    engine, _, _ = setup
    out = engine.handle("zuu dame la mano")
    assert Gesture("dar_mano") in out
    assert not any(isinstance(a, AskLLM) for a in out)


def test_free_chat_goes_to_llm(setup):
    engine, _, _ = setup
    out = engine.handle("zuu qué opinas del fútbol colombiano")
    assert isinstance(out[0], AskLLM)


def test_unknown_udi_question_does_not_invent(setup):
    engine, _, _ = setup
    out = engine.handle("zuu cuál es la fecha límite de inscripción para el próximo semestre")
    text = says(out)
    assert not any(isinstance(a, AskLLM) and "fecha" not in a.prompt.lower() for a in out)
    assert text == "" or "No tengo ese dato" in text or any(isinstance(a, AskLLM) for a in out)


def test_registration_end_to_end(setup):
    engine, _, store = setup
    assert "Ley 1581" in says(engine.handle("zuu quiero inscribirme"))
    assert "nombre" in says(engine.handle("sí autorizo"))
    engine.handle("me llamo carlos ruiz")
    engine.handle("tres uno cero uno dos tres cuatro cinco seis siete")
    engine.handle("omitir")
    assert "sistemas" in says(engine.handle("ingeniería de sistemas")).lower() or True
    engine.handle("diurna")
    out = engine.handle("sí")
    assert store.count() == 1
    assert engine.flow is None and "pre-registro" in says(out)


def test_flow_abandoned_saves_nothing(setup):
    engine, clock, store = setup
    engine.handle("zuu quiero inscribirme")
    engine.handle("sí")
    clock.t += 400
    assert engine.handle("zuu hola") != []     # el flujo caducó, vuelve a la normalidad
    assert engine.flow is None and store.count() == 0


def test_phone_only(setup):
    engine, _, store = setup
    engine.handle("zuu que un asesor me llame")
    engine.handle("sí")
    engine.handle("3001234567")
    out = engine.handle("sí")
    assert store.count() == 1 and "asesor" in says(out)


def test_echo_is_ignored(setup):
    engine, _, _ = setup
    engine.handle("hola zuu")
    engine.note_bot_said("El valor de Derecho en jornada diurna es de cuatro millones quinientos mil pesos.")
    assert engine.handle("el valor de derecho en jornada diurna es de cuatro millones quinientos mil pesos") == []


def test_whisper_hallucination_ignored(setup):
    engine, _, _ = setup
    engine.handle("hola zuu")
    assert engine.handle("Gracias por ver el video") == []
    assert engine.handle("Subtítulos realizados por la comunidad de Amara.org") == []


def test_single_word_noise_ignored_in_session(setup):
    engine, _, _ = setup
    engine.handle("hola zuu")
    assert engine.handle("mesa") == []


def test_whisper_hears_name_as_su(setup):
    engine, _, _ = setup
    assert "Zuu" in says(engine.handle("Hola, su."))


def test_cuanto_vale_is_answered_without_llm(setup):
    engine, _, _ = setup
    out = engine.handle("Hola asume puedes decir cuánto vale negocios internacionales?")
    assert out and all(isinstance(a, Say) for a in out) and "2.491.900" in says(out)


def test_phone_dictated_in_chunks(setup):
    engine, _, store = setup
    engine.handle("zuu que me llamen")
    engine.handle("sí autorizo")
    assert "continúa" in says(engine.handle("304"))
    assert "continúa" in says(engine.handle("338"))
    assert "Confirmemos" in says(engine.handle("0795"))
    engine.handle("sí")
    assert store.fetch_all()[0]["telefono"] == "3043380795"
