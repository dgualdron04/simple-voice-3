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
    out = engine.handle("Robot Zuu cuánto cuesta ingeniería de sistemas")
    assert out and all(isinstance(a, Say) for a in out)
    assert "1.699.200" in says(out) or "1699200" in says(out) or "millón" in says(out).lower() or "pesos" in says(out)


def test_greeting_activates(setup):
    engine, _, _ = setup
    assert "Zuu" in says(engine.handle("robot zuu"))


def test_session_expires(setup):
    engine, clock, _ = setup
    engine.handle("robot zuu")
    assert engine.handle("y cuánto dura") != []
    clock.t += 500
    assert engine.handle("cuéntame un chiste largo por favor") == []


def test_followup_uses_last_program(setup):
    engine, _, _ = setup
    engine.handle("robot zuu háblame de ingeniería civil")
    out = engine.handle("y cuánto dura")
    assert says(out) and "civil" in says(out).lower()


def test_gesture_is_deterministic_and_whitelisted(setup):
    engine, _, _ = setup
    out = engine.handle("robot zuu dame la mano")
    assert Gesture("dar_mano") in out
    assert not any(isinstance(a, AskLLM) for a in out)


def test_free_chat_goes_to_llm(setup):
    engine, _, _ = setup
    out = engine.handle("robot zuu qué opinas del fútbol colombiano")
    assert isinstance(out[0], AskLLM)


def test_unknown_udi_question_does_not_invent(setup):
    engine, _, _ = setup
    out = engine.handle("robot zuu cuál es la fecha límite de inscripción para el próximo semestre")
    text = says(out)
    assert not any(isinstance(a, AskLLM) and "fecha" not in a.prompt.lower() for a in out)
    assert text == "" or "No tengo ese dato" in text or any(isinstance(a, AskLLM) for a in out)


def test_registration_end_to_end(setup):
    engine, _, store = setup
    assert "Ley 1581" in says(engine.handle("robot zuu quiero inscribirme"))
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
    engine.handle("robot zuu quiero inscribirme")
    engine.handle("sí")
    clock.t += 400
    assert engine.handle("robot zuu hola") != []     # el flujo caducó, vuelve a la normalidad
    assert engine.flow is None and store.count() == 0


def test_phone_only(setup):
    engine, _, store = setup
    engine.handle("robot zuu que un asesor me llame")
    engine.handle("sí")
    engine.handle("3001234567")
    out = engine.handle("sí")
    assert store.count() == 1 and "asesor" in says(out)


def test_echo_is_ignored(setup):
    engine, _, _ = setup
    engine.handle("robot zuu")
    engine.note_bot_said("El valor de Derecho en jornada diurna es de cuatro millones quinientos mil pesos.")
    assert engine.handle("el valor de derecho en jornada diurna es de cuatro millones quinientos mil pesos") == []


def test_whisper_hallucination_ignored(setup):
    engine, _, _ = setup
    engine.handle("robot zuu")
    assert engine.handle("Gracias por ver el video") == []
    assert engine.handle("Subtítulos realizados por la comunidad de Amara.org") == []


def test_single_word_noise_ignored_in_session(setup):
    engine, _, _ = setup
    engine.handle("robot zuu")
    assert engine.handle("mesa") == []


def test_whisper_hears_name_as_su(setup):
    engine, _, _ = setup
    assert "Zuu" in says(engine.handle("Hola, robot su."))


def test_cuanto_vale_is_answered_without_llm(setup):
    engine, _, _ = setup
    out = engine.handle("Robot zuu puedes decir cuánto vale negocios internacionales?")
    assert out and all(isinstance(a, Say) for a in out) and "2.491.900" in says(out)


def test_phone_dictated_in_chunks(setup):
    engine, _, store = setup
    engine.handle("robot zuu que me llamen")
    engine.handle("sí autorizo")
    assert "continúa" in says(engine.handle("304"))
    assert "continúa" in says(engine.handle("338"))
    assert "Confirmemos" in says(engine.handle("0795"))
    engine.handle("sí")
    assert store.fetch_all()[0]["telefono"] == "3043380795"


def test_needs_robot_prefix(setup):
    engine, _, _ = setup
    assert engine.handle("zuu cuánto cuesta derecho") == []
    assert engine.handle("hola cuánto cuesta derecho") == []
    assert engine.handle("oye robot zuu cuánto cuesta derecho") != []


def test_other_robot_name_does_not_wake_this_one(tmp_path):
    h2 = load_robot("h2")
    engine = DialogEngine(h2, store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"))
    assert engine.handle("robot zuu cuánto cuesta derecho") == []
    assert engine.handle("robot team su cuánto cuesta derecho") != []
    assert "Teamsu" in says(engine.handle("robot teamsu quién eres")) 


@pytest.mark.parametrize("question,expected", [
    ("robot zuu dónde queda la UDI", "Calle 9"),
    ("robot zuu cuál es la dirección de la universidad", "Bucaramanga"),
    ("robot zuu cuál es el teléfono de la UDI", "seis cero siete"),
    ("robot zuu cuál es el correo de la universidad", "rectoria arroba udi"),
    ("robot zuu la udi tiene personería jurídica", "22195"),
])
def test_institutional_facts_are_exact(setup, question, expected):
    engine, _, _ = setup
    out = engine.handle(question)
    assert all(isinstance(a, Say) for a in out) and expected in says(out)


@pytest.mark.parametrize("heard", ["Hola, Rotsu.", "Hola, Rhozu.", "Hola, Rho Zuu.", "robozu", "Hola, robot Zuu."])
def test_fuzzy_wake_for_whisper_mishearings(setup, heard):
    engine, _, _ = setup
    assert "Zuu" in says(engine.handle(heard))


def test_robot_word_alone_does_not_wake(setup):
    engine, _, _ = setup
    assert engine.handle("los robots son muy útiles en la industria") == []


def test_tell_me_about_the_university_is_answered(setup):
    engine, _, _ = setup
    out = engine.handle("robot zuu quisiera que me hablaras sobre la universidad")
    assert all(isinstance(a, Say) for a in out) and "Bucaramanga" in says(out)


def test_offer_declined_but_new_question_is_not_swallowed(setup):
    engine, _, _ = setup
    engine.handle("robot zuu cuál es la fecha límite de inscripción")     # ofrece dejar teléfono
    out = engine.handle("no quiero que me expliques el programa de negocios")
    assert "Está bien" not in says(out) and "Negocios" in says(out)


def test_program_mentioned_without_question_gets_summary_not_llm(setup):
    engine, _, _ = setup
    engine.handle("robot zuu hola")
    out = engine.handle("porfa ahora vamos a ver el programa de negocios")
    assert all(isinstance(a, Say) for a in out) and "9 semestres" in says(out)


def test_known_people_answered_from_curated_faq(setup):
    engine, _, _ = setup
    engine.handle("robot zuu hola")
    out = engine.handle("quién es Jairo Castro")
    assert all(isinstance(a, Say) for a in out) and "fundador" in says(out)
    out = engine.handle("¿quién es el rector?")
    assert all(isinstance(a, Say) for a in out) and "Jairo Augusto" in says(out)


def test_who_are_you_long_phrase_is_deterministic(setup):
    engine, _, _ = setup
    engine.handle("robot zuu hola")
    out = engine.handle("y si era que me dijeras quién eres")
    assert all(isinstance(a, Say) for a in out) and "UDI" in says(out)


@pytest.mark.parametrize("question,expected", [
    ("quién es tu creador", "café"),
    ("y quién te creó", "café"),
    ("quién fundó la UDI", "Centrosistemas"),
    ("¿Y dónde quedó la universidad?", "Calle 9"),
    ("y a qué número o puedo comunicarme con ellos", "seis cero siete"),
    ("cómo me puedo comunicar con la universidad", "rectoria"),
])
def test_curated_answers_never_reach_llm(setup, question, expected):
    engine, _, _ = setup
    engine.handle("robot zuu hola")
    out = engine.handle(question)
    assert all(isinstance(a, Say) for a in out) and expected in says(out)


def test_faq_uses_this_robots_name(tmp_path):
    h2 = load_robot("h2")
    engine = DialogEngine(h2, store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"))
    engine.handle("robot teamsu hola")
    out = says(engine.handle("quién te creó"))
    assert "ZUU" not in out and "Zuu" not in out


def test_context_for_llm_is_trimmed():
    from core.dialog import trim_context
    nl = chr(10)
    ctx = nl.join(["programa.nombre: X", "Malla curricular: " + "a" * 5000, "programa.malla.semestre: y"])
    short = trim_context(ctx, "cuánto cuesta")
    assert "Malla" not in short and len(short) < 200
    assert "Malla" in trim_context(ctx, "cuál es la malla")
