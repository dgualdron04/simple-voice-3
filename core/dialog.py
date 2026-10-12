"""Motor de diálogo determinista: decide QUÉ hacer con cada frase transcrita.

No depende de Pipecat ni del audio (se prueba con texto). El LLM solo se usa para
conversación libre y para redactar con contexto local; todo lo demás (gestos,
registro, costos, comandos) es código, por rapidez y para que no se desvíe.

Acciones que devuelve handle(): Say, Gesture, AskLLM. Lista vacía = ignorar.
"""
from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from core.registration import validators as v
from core.stt_text import fix_stt_text
from core.registration.flow import ProgramInfo, RegistrationFlow
from core.registration.store import RegistrationStore


@dataclass
class Say:
    text: str


@dataclass
class Gesture:
    name: str


@dataclass
class AskLLM:
    prompt: str          # lo que ve el LLM (puede incluir contexto)
    plain: str           # la pregunta limpia, para el historial


Action = Say | Gesture | AskLLM

HALLUCINATIONS = (
    "gracias por ver", "suscribete", "subtitulos", "amara org", "la comunidad de amara",
    "gracias por su atencion", "hasta la proxima", "nos vemos en el proximo", "dale like",
    "musica", "aplausos", "sous titres",
)
SINGLE_WORDS = {"hola", "gracias", "adios", "chao", "chau", "si", "no", "para", "stop", "silencio",
                "espera", "pausa", "detente", "callate", "chiste"}
STOP_WORDS = {"para", "stop", "silencio", "callate", "espera", "pausa", "detente", "alto"}
BYE = ("adios", "hasta luego", "chao", "chau", "nos vemos", "hasta pronto")
THANKS = ("gracias", "muy amable", "te agradezco")
SMALL_TALK = ("como estas", "como vas", "que haces", "quien eres", "como te llamas", "cual es tu nombre")

PHONE_ONLY = ("que me llamen", "que me llame", "me llamen", "me llame un asesor", "llamenme", "solo mi telefono",
              "solo mi numero", "dejar mi telefono", "dejar mi numero", "dejo mi telefono", "dejo mi numero",
              "un asesor me llame", "que un asesor")
REGISTER = ("inscrib", "preinscrib", "pre inscrib", "pre registro", "preregistro", "registrarme", "registrar me",
            "matricular", "quiero estudiar", "quiero entrar", "quiero ingresar", "me interesa estudiar",
            "como me registro", "proceso de admision", "admisiones")
GESTURES = {
    "dar_mano": ("dame la mano", "dame tu mano", "dame la manito", "choca esa mano", "estrechame la mano",
                 "saludame de mano", "dar la mano", "dame la pata"),
    "saludar": ("saluda", "saludame", "saludanos", "saluda con la mano", "haz un saludo", "mueve la mano",
                "di hola con la mano"),
    "señalar": ("senala", "apunta", "senalame", "indica con la mano"),
}
UDI_KEYWORDS = (
    "udi", "universidad", "programa", "carrera", "facultad", "matricula", "precio", "costo", "cuesta", "valor",
    "semestre", "duracion", "modalidad", "jornada", "snies", "resolucion", "creditos", "contacto", "correo",
    "telefono", "malla", "materias", "asignaturas", "plan de estudios", "campo laboral", "ingenieria", "derecho",
    "negocios", "administracion", "comunicacion", "criminalistica", "diseno", "ecopetrol", "rector", "presidente",
    "requisitos", "inscripcion", "tu creador", "quien te creo", "te hizo", "te crearon", "te desarrollo", "zuu", "tu nombre", "admision", "becas", "beca", "sede", "titulo", "acreditad", "registro calificado",
)
FOLLOW_UP = ("cuanto cuesta", "cuanto vale", "valor", "precio", "costo", "cuanto dura", "duracion", "modalidad",
             "jornada", "malla", "materias", "creditos", "snies", "resolucion", "contacto", "correo", "telefono",
             "nocturna", "nocturno", "noche", "diurna", "diurno", "virtual", "presencial", "semestre",
             "semestres", "costo total", "valor total", "que puedo trabajar", "campo laboral")

NO_INFO = ("No tengo ese dato en mi información. Si quieres, dejo tu teléfono para que un asesor de "
           "admisiones te llame. ¿Lo hacemos?")


def tokens(text: str) -> list[str]:
    return v.words(text)


def clean_for_voice(text: str, max_chars: int) -> str:
    text = re.sub(r"[*#_`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        out = candidate
    return out or text[:max_chars]


class DialogEngine:
    def __init__(self, settings: dict, store: RegistrationStore | None = None,
                 knowledge=None, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self.robot_name = settings["robot"]["name"]
        self.aliases = {v.norm(a) for a in settings["robot"].get("aliases", [])} | {v.norm(self.robot_name)}
        self.wake_mode = settings["wake"]["mode"]
        self.session_secs = settings["wake"]["session_secs"]
        self.greetings = {v.norm(g) for g in settings["wake"]["greetings"]}
        self.wake_prefix = v.norm(settings["wake"]["prefix"])
        self.mode = settings["dialog"]["mode"]
        self.echo_similarity = settings["dialog"]["echo_similarity"]
        self.store = store
        self.k = knowledge if knowledge is not None else _Knowledge()
        self.session_until = 0.0
        self.flow: RegistrationFlow | None = None
        self.flow_last = 0.0
        self.offer_pending = False
        self.last_program: str | None = None
        self.last_said = ""
        self._recent_bot: deque[str] = deque(maxlen=5)

    # ------------------------------------------------------------- estado
    def note_bot_said(self, text: str) -> None:
        self._recent_bot.append(v.norm(text))
        self.last_said = text

    @property
    def in_session(self) -> bool:
        return self.clock() < self.session_until or self.flow is not None

    def _touch(self) -> None:
        self.session_until = self.clock() + self.session_secs

    def _max_chars(self) -> int:
        d = self.settings["dialog"]
        return d["feria_max_voice_chars"] if self.mode == "feria" else d["max_voice_chars"]

    # ------------------------------------------------------------- filtros
    def is_noise(self, text: str) -> bool:
        t = " ".join(tokens(text))
        if len(t) < self.settings["stt"]["min_chars"] and t not in SINGLE_WORDS:
            return True
        if any(h in t for h in HALLUCINATIONS):
            return True
        w = t.split()
        if len(w) >= 6 and len(set(w)) <= 2:          # "ja ja ja ja ja ja"
            return True
        return False

    def is_echo(self, text: str) -> bool:
        t = " ".join(tokens(text))
        if len(t) < 8:
            return False
        for said in self._recent_bot:
            if t in said or SequenceMatcher(None, t, said).ratio() >= self.echo_similarity:
                return True
        return False

    def _wake(self, text: str) -> tuple[bool, str]:
        """(activó, resto). Frase de activación: «robot» + nombre del robot, en las primeras palabras.

        Acepta «hola robot zuu», «oye robot axl» y nombres partidos por Whisper («team su»).
        """
        original = text.split()
        normalized = [(v.words(raw) or [""])[0] for raw in original]
        for i, word in enumerate(normalized[:4]):
            if word != self.wake_prefix:
                continue
            nxt = normalized[i + 1] if i + 1 < len(normalized) else ""
            nxt2 = normalized[i + 2] if i + 2 < len(normalized) else ""
            if nxt in self.aliases:
                return True, " ".join(original[i + 2:]).strip(" ,.")
            if nxt and nxt2 and (nxt + nxt2) in self.aliases:
                return True, " ".join(original[i + 3:]).strip(" ,.")
        return False, text

    # --------------------------------------------------------------- manejo
    def handle(self, text: str) -> list[Action]:
        text = (text or "").strip()
        if not text or self.is_noise(text) or self.is_echo(text):
            return []

        now = self.clock()
        if self.flow is not None:
            if now - self.flow_last > self.settings["registration"]["idle_timeout_secs"]:
                self.flow = None   # abandonó a medias: no se guarda nada
            else:
                return self._flow_turn(text)

        text = fix_stt_text(text) or text      # corrige nombres de carreras mal oídos
        woke = False
        if self.wake_mode == "text" and now >= self.session_until:
            woke, rest = self._wake(text)
            if not woke:
                return []
            text = rest
            if not text:
                self._touch()
                return [Say(f"Hola, soy {self.robot_name}. ¿En qué te puedo ayudar?")]

        w = tokens(text)
        if len(w) == 1 and w[0] not in SINGLE_WORDS:
            return []                                        # palabra suelta = ruido
        self._touch()
        return self._route(text)

    def _flow_turn(self, text: str) -> list[Action]:
        self.flow_last = self.clock()
        reply = self.flow.handle(text)
        actions: list[Action] = [Say(s) for s in reply.say]
        if reply.done:
            self.flow = None
            self._touch()
        return actions

    def _start_flow(self, kind: str, intro: str = "") -> list[Action]:
        if self.store is None:
            return [Say("El registro no está disponible en este momento. Puedes acercarte a admisiones de la UDI.")]
        retries = self.settings["registration"]["retries_per_field"]
        self.flow = RegistrationFlow(self.store, self.settings["robot"]["id"], self.k.lookup_program,
                                     retries=retries, kind=kind)
        self.flow_last = self.clock()
        reply = self.flow.start()
        return ([Say(intro)] if intro else []) + [Say(s) for s in reply.say]

    def _route(self, text: str) -> list[Action]:
        t = " ".join(tokens(text))
        w = t.split()

        # oferta pendiente ("¿dejo tu teléfono?")
        if self.offer_pending:
            self.offer_pending = False
            answer = v.is_yes(text)
            if answer is True:
                return self._start_flow("phone")
            if answer is False:
                return [Say("Está bien. Si cambias de opinión, solo dímelo.")]

        if w and w[0] in STOP_WORDS and len(w) <= 3:
            return []                                        # silencio

        if any(t == b or t.startswith(b + " ") or t.endswith(" " + b) for b in BYE):
            self.session_until = 0.0
            return [Say("¡Hasta luego! Que tengas un excelente día.")]

        if any(p in t for p in ("modo feria", "activa modo feria")):
            self.mode = "feria"
            return [Say("Modo feria activado. Responderé más corto.")]
        if any(p in t for p in ("modo normal", "desactiva modo feria")):
            self.mode = "normal"
            return [Say("Modo normal activado.")]
        if t in ("repite", "repite eso", "repite tu respuesta", "otra vez", "como dijiste", "no te entendi") and self.last_said:
            return [Say(self.last_said)]

        # registro
        if any(p in t for p in PHONE_ONLY):
            return self._start_flow("phone", "Claro, con gusto.")
        if any(p in t for p in REGISTER) and not _asks_info(t):
            return self._start_flow("full", "¡Qué bueno que te interese! Te ayudo con tu pre-registro.")

        # gestos (lista blanca; el LLM nunca decide)
        for name, phrases in GESTURES.items():
            if any(p in t for p in phrases):
                return [Gesture(name), Say(_gesture_line(name))]

        # respuestas instantáneas
        if t in self.greetings or t in {f"hola {a}" for a in self.aliases}:
            return [Say(f"¡Hola! Soy {self.robot_name}. ¿En qué te ayudo?")]
        if any(s in t for s in THANKS) and len(w) <= 5:
            return [Say(random.choice(["¡Con gusto!", "¡De nada! Aquí estoy para ayudarte.", "Con mucho gusto."]))]
        if any(s in t for s in SMALL_TALK) and len(w) <= 6:
            if "quien eres" in t or "te llamas" in t or "tu nombre" in t:
                return [Say(f"Soy {self.robot_name}, el asistente virtual de la UDI. Puedo contarte de los programas y ayudarte a inscribirte.")]
            return [Say("Muy bien, gracias. ¿Y tú? ¿En qué te puedo ayudar?")]

        return self._answer(text)

    def _answer(self, text: str) -> list[Action]:
        question = self.k.with_context(text, self)
        direct = self.k.answer(question)
        if direct:
            return [Say(clean_for_voice(direct, self._max_chars()))]

        if self.k.is_udi_related(question):
            context = self.k.context(question)
            if not context:
                self.offer_pending = True
                return [Say(NO_INFO)]
            prompt = (
                "Responde SOLO con el contexto. Si el contexto no contiene la respuesta exacta, di: "
                "\"No tengo ese dato\". Máximo dos frases cortas. No inventes cifras ni nombres.\n"
                f"Contexto:\n{context}\n\nPregunta: {question}"
            )
            return [AskLLM(prompt, question)]

        return [AskLLM(text, text)]      # conversación libre


def _asks_info(t: str) -> bool:
    """'¿cuáles son los requisitos de inscripción?' es consulta, no inicio de registro."""
    return any(p in t for p in ("requisitos", "fecha", "fechas", "cuando", "cuanto cuesta", "costo", "valor",
                                "precio", "que necesito", "documentos"))


def _gesture_line(name: str) -> str:
    return {
        "dar_mano": "¡Con gusto! Aquí tienes mi mano.",
        "saludar": "¡Hola! Te saludo con la mano.",
        "señalar": "Claro, señalo.",
    }.get(name, "Claro.")


class _Knowledge:
    """Fachada sobre la base local (structured_answer / rag_reasoner). Reemplazable en pruebas."""

    def __init__(self):
        self._sa = None
        self._rr = None

    def _load(self):
        if self._sa is None:
            from core.knowledge import rag_reasoner, structured_answer
            self._sa, self._rr = structured_answer, rag_reasoner

    def answer(self, question: str) -> str | None:
        self._load()
        if self._sa.detect_program(question) is None:
            from core.knowledge.institution import institutional_answer
            fact = institutional_answer(question)
            if fact:
                return fact
        return self._rr.answer_reasoning_question(question) or self._sa.answer_exact(question)

    def is_udi_related(self, question: str) -> bool:
        self._load()
        if self._sa.detect_program(question) is not None:
            return True
        t = " ".join(tokens(question))
        return any(k in t for k in UDI_KEYWORDS)

    def context(self, question: str) -> str:
        self._load()
        return self._sa.search_context(question, limit=2) or ""

    def with_context(self, question: str, engine: "DialogEngine") -> str:
        """Si la pregunta es de seguimiento ('¿y cuánto cuesta?'), añade el último programa."""
        self._load()
        detected = self._sa.detect_program(question)
        if detected is not None:
            engine.last_program = self._sa.row_get(detected, "nombre")
            return question
        t = " ".join(tokens(question))
        if engine.last_program and any(m in t for m in FOLLOW_UP):
            return f"{question} de {engine.last_program}"
        return question

    def lookup_program(self, text: str) -> ProgramInfo | None:
        self._load()
        row = self._sa.detect_program(text)
        if row is None:
            return None
        g = lambda key: self._sa.row_get(row, key)
        modal_text = v.norm(g("modalidad"))
        modalidades = [m for m in ("presencial", "virtual") if m in modal_text]
        if g("valor_virtual") and "virtual" not in modalidades:
            modalidades.append("virtual")
        jornadas = []
        if g("valor_diurna") or "diurna" in v.norm(g("jornada")):
            jornadas.append("diurna")
        if g("valor_nocturna") or "nocturna" in v.norm(g("jornada")):
            jornadas.append("nocturna")
        return ProgramInfo(g("nombre"), modalidades or ["presencial"], jornadas)
