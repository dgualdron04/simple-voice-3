"""Correcciones tras la prueba en el H2 (2026-10-15): «sí» mal reconocido, eco del prompt de Whisper,
bucles, vocativos («Atom, ¿…?»), markdown/enlaces leídos en voz alta y precios pegados («esdos millones»)."""
import pytest

from core.config import load_robot
from core.dialog import DialogEngine
from core.registration import validators as v
from core.stt_guard import is_degenerate, looks_like_prompt_echo
from core.tts_text import clean_tts_text

PROMPT = ("Conversación en español de Colombia con un asistente de la universidad UDI. Habla con Teamsu. "
          "Rector: Jairo Augusto Castro Castro. Presidente: Jairo Castro Castro. "
          "Programas: Derecho, Ingeniería Civil, Ingeniería de Sistemas.")


# ---------------------------------------------------------------- sí / no
@pytest.mark.parametrize("text", ["Sí.", "¿Sí?", "Pues sí", "Bueno, sí autorizo", "Así es", "Así.", "De acuerdo",
                                  "Está bien", "Claro que sí", "Yo autorizo", "Hágale", "Eso.", "Ajá", "Eh, sí",
                                  "Con gusto", "De una", "Sí señor", "Síl.", "SIV", "Siga.", "Sigue."])
def test_yes_variants(text):
    assert v.is_yes(text) is True


@pytest.mark.parametrize("text", ["No.", "Mejor no", "Todavía no", "Pues no", "No quiero", "No, gracias", "Ni loco",
                                  "Nunca", "De ninguna manera"])
def test_no_variants(text):
    assert v.is_yes(text) is False


@pytest.mark.parametrize("text", ["Rector. Jairo Augusto Castro.", "Presidente de la Universidad UDI.",
                                  "¿Quién es tu actual dueño?", "Gracias.", "Sin", "Sin duda no sé", "Siv algo más", "Así no se puede hablar contigo",
                                  "Eso que dijiste no lo entendí bien"])
def test_ambiguous_is_not_consent(text):
    assert v.is_yes(text) is None          # nunca se asume consentimiento


# ------------------------------------------------------------- Whisper
@pytest.mark.parametrize("text", ["Rector. Jairo Augusto Castro.", "Jairo Castro. Presidente de la Universidad UDI.",
                                  "Rector. Presidente de la Universidad UDI.", "Presidente de la Universidad UD.",
                                  "Presidente & Ciencias de Derecho, Ingeniería Civil, In"])
def test_prompt_echo_detected(text):
    assert looks_like_prompt_echo(text, PROMPT)


@pytest.mark.parametrize("text", ["Sí.", "¿Me puedo inscribir contigo?", "Quisiera conocer qué carreras tienes.",
                                  "Mi nombre es Ana María Pérez"])
def test_real_speech_is_not_prompt_echo(text):
    assert not looks_like_prompt_echo(text, PROMPT)


def test_no_prompt_means_no_echo():
    assert not looks_like_prompt_echo("Rector. Jairo Augusto Castro.", None)


@pytest.mark.parametrize("text", ["Rector. Rector. Rector. Rector.", "Rector no responde. Rector no responde. Rector no responde.",
                                  "Presidente de Colombia. Presidente de Colombia. Presidente de Colombia.",
                                  "la la la la la"])
def test_loops_are_degenerate(text):
    assert is_degenerate(text)


@pytest.mark.parametrize("text", ["Sí, sí, sí", "No, no.", "Hola, ¿cuánto cuesta derecho?", "Muy muy bien"])
def test_normal_speech_is_not_degenerate(text):
    assert not is_degenerate(text)


# ---------------------------------------------------------------- TTS
def test_tts_reads_link_label_not_markdown():
    out = clean_tts_text("Visita [www.udibucaramanga.edu.co](https://www.udibucaramanga.edu.co).")
    assert "](" not in out and "https" not in out and "udibucaramanga punto edu punto co" in out


def test_tts_reads_email_and_drops_emoji():
    out = clean_tts_text("Escribe a admisiones@udi.edu.co 😄")
    assert "admisiones arroba udi punto edu punto co" in out and "😄" not in out


def test_tts_price_keeps_space():
    assert "es dos millones" in clean_tts_text("El valor es 2.074.400 pesos.")
    assert "diurna dos millones" in clean_tts_text("diurna 2.074.400")


def test_tts_bullets_become_pauses():
    assert clean_tts_text("- Derecho\n- Ingeniería Civil") == "Derecho, Ingeniería Civil"


# ------------------------------------------------------------- diálogo
def _engine():
    engine = DialogEngine(load_robot("h2"))
    engine.handle("hola robot teamsu")
    return engine


def test_vocative_is_stripped_for_knowledge_lookup():
    engine = _engine()
    engine.handle("Atom, ¿cómo se llama el rector de la UDI?")
    assert engine._why == "knowledge:faq"


def test_meaningful_first_word_is_kept():
    engine = _engine()
    engine.handle("Derecho, ¿cuánto cuesta el semestre?")
    assert engine._why == "knowledge:program_db"


def test_consent_accepts_colloquial_yes(tmp_path):
    from core.registration.store import RegistrationStore
    engine = DialogEngine(load_robot("h2"), store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"))
    engine.handle("hola robot teamsu")
    engine.handle("quiero inscribirme")
    assert engine.flow is not None and engine.flow.state == "consent"
    engine.handle("Pues sí, de acuerdo")
    assert engine.flow is not None and engine.flow.state != "consent"


# ------------------------------------------------- 2.ª prueba en el H2 (03:16–03:29)
def _registration_until_program(tmp_path):
    from core.registration.store import RegistrationStore
    engine = DialogEngine(load_robot("h2"), store=RegistrationStore(tmp_path / "r.db", tmp_path / "r.key"))
    for text in ("hola robot teamsu", "háblame de ingeniería civil", "me quiero inscribir", "Sí.",
                 "María García.", "3, 2, 2, 8, 2, 6, 5, 2, 0, 4.", "Omitir"):
        engine.handle(text)
    assert engine.flow.state == "prefill"           # «¿Te interesa Ingeniería Civil, del que hablamos?»
    return engine


def test_decline_prefill_with_other_program_takes_it(tmp_path):
    engine = _registration_until_program(tmp_path)
    engine.handle("No, ingeniería electrónica.")
    assert engine.flow.data["programa"] == "Ingeniería Electrónica" and engine.flow.state == "confirm"


def test_program_is_not_asked_twice_after_decline(tmp_path):
    engine = _registration_until_program(tmp_path)
    engine.handle("No.")
    assert engine.flow.state == "programa"
    engine.handle("Electrónica")                    # una sola palabra basta y no se vuelve a preguntar
    assert engine.flow.data["programa"] == "Ingeniería Electrónica" and engine.flow.state == "confirm"


@pytest.mark.parametrize("text", ["Atom, ¿cuánto cuesta derecho?", "Hola Mega", "Mega, háblame de ingeniería civil",
                                  "robot Atom, ¿qué carreras tienen?", "Átomo, ¿quién es el rector?", "Hola Teamsu"])
def test_atom_and_mega_wake_the_h2(text):
    engine = DialogEngine(load_robot("h2"))
    engine.handle(text)
    assert not engine._why.startswith("ignored")


@pytest.mark.parametrize("text", ["Eso está mega bueno", "el átomo de hidrógeno es pequeño", "un mega descuento"])
def test_names_inside_other_conversations_do_not_wake(text):
    engine = DialogEngine(load_robot("h2"))
    engine.handle(text)
    assert engine._why == "ignored:no_wake"


def test_stale_llm_answer_is_cancelled_and_rag_context_dropped():
    import asyncio
    import time
    from pipecat.processors.aggregators.llm_context import LLMContext
    from core.pipeline import DialogProcessor

    context = LLMContext()
    context.add_message({"role": "user", "content": "PROMPT CON CONTEXTO RAG ... Pregunta: presidente"})
    proc = DialogProcessor(engine=None, gestures=None, context=context, half_duplex=True, tail_secs=1.0)
    calls = []

    async def fake_interruption():
        calls.append("interrupt")

    proc.broadcast_interruption = fake_interruption
    proc.llm_t0 = time.monotonic()
    proc.pending_swap = ("PROMPT CON CONTEXTO RAG ... Pregunta: presidente", "presidente")
    asyncio.run(proc._drop_stale_llm())
    assert calls == ["interrupt"]
    assert proc.llm_t0 is None and proc.pending_swap is None
    assert context.messages[-1]["content"] == "presidente"


def test_answer_quoting_the_robot_is_not_echo():
    engine = DialogEngine(load_robot("h2"))
    engine.note_bot_said("¿Cuál es tu correo electrónico? Si no quieres darlo, di: no tengo correo.")
    assert not engine.is_echo("No tengo correo.")
    assert engine.is_echo("Cuál es tu correo electrónico? Si no quieres darlo")      # eco real: casi toda la frase


def test_skip_email_variants():
    for text in ("No tengo correo.", "No tengo correa.", "Omitir", "Oni tira.", "Omití", "Paso"):
        assert v.wants_skip(text), text
    for text in ("ana arroba gmail punto com", "Pony Teeter", "María"):
        assert not v.wants_skip(text), text
