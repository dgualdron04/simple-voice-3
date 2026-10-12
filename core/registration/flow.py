"""Flujo de pre-registro como máquina de estados determinista (sin LLM).

Nodos (equivalentes a los de Pipecat Flows):
  1 consentimiento (Ley 1581 de 2012)  2 datos personales  3 interés académico
  4 confirmación (lee los datos y permite corregir)  5 cierre
Ruta corta: solo teléfono -> un asesor llamará.

Nada se guarda antes de tener consentimiento Y confirmación. Cancelar en cualquier
punto descarta todo. El parseo y la validación son código; el LLM no interviene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.registration import validators as v
from core.registration.store import RegistrationStore, utc_now

CANCEL_PHRASES = ("cancela", "cancelar", "cancelalo", "mejor no", "olvidalo", "olvida eso",
                  "ya no quiero", "no quiero continuar", "deja asi", "dejalo asi", "salir del registro",
                  "no quiero inscribirme")

CONSENT_TEXT = (
    "Antes de empezar: para tu pre-registro necesito tratar tus datos personales, como nombre, "
    "teléfono y correo, solo para que admisiones de la UDI te contacte. Puedes pedir que los borren "
    "cuando quieras. ¿Autorizas el tratamiento de tus datos, según la Ley 1581 de 2012?"
)

FIELD_LABELS = {"nombre": "nombre", "telefono": "teléfono", "correo": "correo",
                "programa": "programa", "modalidad": "modalidad", "jornada": "jornada"}
CORRECT_OPTIONS = {
    "nombre": ["nombre"], "telefono": ["telefono", "numero", "celular"], "correo": ["correo", "email", "mail"],
    "programa": ["programa", "carrera"], "modalidad": ["modalidad"], "jornada": ["jornada", "horario"],
}


@dataclass
class ProgramInfo:
    nombre: str
    modalidades: list[str]   # subconjunto de ["presencial", "virtual"]
    jornadas: list[str]      # subconjunto de ["diurna", "nocturna"]


ProgramLookup = Callable[[str], "ProgramInfo | None"]


@dataclass
class FlowReply:
    say: list[str]
    done: bool = False
    saved_id: int | None = None
    gesture: str | None = None


@dataclass
class RegistrationFlow:
    store: RegistrationStore
    robot_id: str
    lookup_program: ProgramLookup
    retries: int = 2
    kind: str = "full"           # full | phone
    state: str = "consent"
    data: dict = field(default_factory=dict)
    program: ProgramInfo | None = None
    consent_at: str | None = None
    failures: int = 0
    queue: list[str] = field(default_factory=list)   # campos por (re)preguntar antes de confirmar

    # ---------------------------------------------------------------- inicio
    def start(self) -> FlowReply:
        return FlowReply([CONSENT_TEXT])

    # --------------------------------------------------------------- entrada
    def handle(self, text: str) -> FlowReply:
        t = v.norm(text)
        if self.state != "consent" and any(p in t for p in CANCEL_PHRASES):
            return self._cancel()

        handler = getattr(self, f"_on_{self.state}")
        return handler(text)

    # ------------------------------------------------------------ utilidades
    def _cancel(self) -> FlowReply:
        return FlowReply(["Listo, cancelé el registro y no guardé ningún dato. Si quieres, retomamos cuando gustes."], done=True)

    def _fail(self, again: str, on_exhausted: Callable[[], FlowReply]) -> FlowReply:
        self.failures += 1
        if self.failures > self.retries:
            self.failures = 0
            return on_exhausted()
        return FlowReply([again])

    def _give_up(self) -> FlowReply:
        return FlowReply(["No logro entenderte bien por aquí, y no quiero registrar datos incorrectos. "
                          "Cancelo el registro; puedes acercarte a admisiones de la UDI."], done=True)

    def _next_field(self) -> FlowReply:
        """Avanza al siguiente campo pendiente o a la confirmación."""
        self.failures = 0
        prefix: list[str] = []
        while self.queue:
            fieldname = self.queue.pop(0)
            if fieldname == "modalidad" and self.program:
                options = self.program.modalidades
                if len(options) <= 1:
                    self.data["modalidad"] = options[0] if options else "presencial"
                    prefix.append(f"Este programa es {self.data['modalidad']}.")
                    continue
            if fieldname == "jornada" and self.program:
                options = self.program.jornadas
                if len(options) <= 1:
                    self.data["jornada"] = options[0] if options else None
                    if options:
                        prefix.append(f"Este programa se ofrece en jornada {options[0]}.")
                    continue
            self.state = fieldname
            return FlowReply(prefix + [self._prompt(fieldname)])
        return self._confirm_prompt(prefix)

    def _prompt(self, fieldname: str) -> str:
        if fieldname == "nombre":
            return "Perfecto. ¿Cuál es tu nombre completo?"
        if fieldname == "telefono":
            return "¿Cuál es tu número de teléfono o celular? Dímelo dígito por dígito."
        if fieldname == "correo":
            return "¿Cuál es tu correo electrónico? Si prefieres no darlo, di omitir."
        if fieldname == "programa":
            return "¿Qué carrera te interesa?"
        if fieldname == "modalidad":
            return "¿Prefieres modalidad presencial o virtual?"
        if fieldname == "jornada":
            return "¿Prefieres jornada diurna o nocturna?"
        raise ValueError(fieldname)

    # ------------------------------------------------------------ consent
    def _on_consent(self, text: str) -> FlowReply:
        answer = v.is_yes(text)
        if answer is True:
            self.consent_at = utc_now()
            self.queue = ["telefono"] if self.kind == "phone" else ["nombre", "telefono", "correo", "programa", "modalidad", "jornada"]
            return self._next_field()
        if answer is False:
            return FlowReply(["Entendido, no guardaré ningún dato. Puedes acercarte a admisiones cuando quieras."], done=True)
        return self._fail("Necesito un sí o un no: ¿autorizas el tratamiento de tus datos personales?",
                          lambda: FlowReply(["No te escuché claro, así que no guardaré nada. Podemos intentarlo de nuevo cuando quieras."], done=True))

    # ------------------------------------------------------- datos personales
    def _on_nombre(self, text: str) -> FlowReply:
        name = v.parse_name(text)
        if name:
            if len(name.split()) < 2 and self.failures == 0:
                self.failures += 1
                self.data["_nombre_parcial"] = name
                return FlowReply(["¿Me das también tu apellido?"])
            if "_nombre_parcial" in self.data and len(name.split()) < 2:
                name = f"{self.data.pop('_nombre_parcial')} {name}"
            self.data.pop("_nombre_parcial", None)
            self.data["nombre"] = name
            return self._next_field()
        if self.failures >= self.retries and v.words(text):
            self.data["nombre"] = " ".join(w.capitalize() for w in v.words(text)[:4])
            return self._next_field()
        return self._fail("No te entendí el nombre. ¿Me lo repites despacio?", self._give_up)

    def _on_telefono(self, text: str) -> FlowReply:
        phone = v.parse_phone(text)
        if phone:
            self.data["telefono"] = phone
            return self._next_field()
        return self._fail("No alcancé a captar el teléfono. Repítelo dígito por dígito, por favor.", self._give_up)

    def _on_correo(self, text: str) -> FlowReply:
        if v.wants_skip(text):
            self.data["correo"] = None
            return self._next_field()
        email = v.parse_email(text)
        if email:
            self.data["correo"] = email
            return self._next_field()
        return self._fail("No entendí el correo. Dilo así: nombre arroba gmail punto com. O di omitir.",
                          lambda: (self.data.__setitem__("correo", None), self._next_field())[1])

    # ------------------------------------------------------ interés académico
    def _on_programa(self, text: str) -> FlowReply:
        info = self.lookup_program(text)
        if info:
            self.program = info
            self.data["programa"] = info.nombre
            return self._next_field()
        return self._fail("No ubiqué esa carrera en mi lista. Dime el nombre, por ejemplo: ingeniería de sistemas o derecho.",
                          self._give_up)

    def _on_modalidad(self, text: str) -> FlowReply:
        choice = v.pick_choice(text, {"presencial": ["presencial", "presencialmente"], "virtual": ["virtual", "distancia"]})
        if choice and (not self.program or choice in self.program.modalidades):
            self.data["modalidad"] = choice
            return self._next_field()
        if choice:
            return self._fail(f"Ese programa no se ofrece {choice}. Elige: {' o '.join(self.program.modalidades)}.", self._give_up)
        return self._fail("¿Presencial o virtual?", self._give_up)

    def _on_jornada(self, text: str) -> FlowReply:
        choice = v.pick_choice(text, {"diurna": ["diurna", "dia", "manana", "diurno"], "nocturna": ["nocturna", "noche", "nocturno"]})
        if choice and (not self.program or choice in self.program.jornadas):
            self.data["jornada"] = choice
            return self._next_field()
        if choice:
            return self._fail(f"Ese programa no tiene jornada {choice}. Elige: {' o '.join(self.program.jornadas)}.", self._give_up)
        return self._fail("¿Diurna o nocturna?", self._give_up)

    # ------------------------------------------------------------ confirmación
    def _summary(self) -> str:
        d = self.data
        parts = []
        if d.get("nombre"):
            parts.append(f"nombre {d['nombre']}")
        parts.append(f"teléfono {v.format_phone_for_speech(d['telefono'])}")
        if d.get("correo"):
            parts.append(f"correo {v.format_email_for_speech(d['correo'])}")
        if d.get("programa"):
            parts.append(f"programa {d['programa']}")
        if d.get("modalidad"):
            parts.append(f"modalidad {d['modalidad']}")
        if d.get("jornada"):
            parts.append(f"jornada {d['jornada']}")
        return "; ".join(parts)

    def _confirm_prompt(self, prefix: list[str] | None = None) -> FlowReply:
        self.state = "confirm"
        self.failures = 0
        return FlowReply((prefix or []) + [f"Confirmemos tus datos: {self._summary()}. ¿Es correcto?"])

    def _on_confirm(self, text: str) -> FlowReply:
        answer = v.is_yes(text)
        if answer is True:
            return self._save()
        if answer is False:
            self.state = "correct"
            self.failures = 0
            options = "teléfono" if self.kind == "phone" else "nombre, teléfono, correo, programa, modalidad o jornada"
            return FlowReply([f"¿Qué dato quieres corregir? {options}."])
        return self._fail("¿Los datos son correctos? Di sí o no.", self._give_up)

    def _on_correct(self, text: str) -> FlowReply:
        options = {k: s for k, s in CORRECT_OPTIONS.items() if self.kind == "full" or k == "telefono"}
        which = v.pick_choice(text, options)
        if not which:
            return self._fail("No entendí cuál dato corregir. Dime por ejemplo: el teléfono.", self._give_up)
        self.queue = [which]
        if which == "programa":
            self.queue += ["modalidad", "jornada"]
            self.data.pop("modalidad", None)
            self.data.pop("jornada", None)
        return self._next_field()

    def _save(self) -> FlowReply:
        d = self.data
        saved = self.store.save(
            tipo="solo_telefono" if self.kind == "phone" else "preregistro",
            robot_id=self.robot_id, consent_at=self.consent_at or utc_now(),
            nombre=d.get("nombre"), telefono=d.get("telefono"), correo=d.get("correo"),
            programa=d.get("programa"), modalidad=d.get("modalidad"), jornada=d.get("jornada"),
        )
        if self.kind == "phone":
            msg = "Listo, anoté tu número. Un asesor de admisiones te llamará pronto. ¡Gracias!"
        else:
            msg = ("Listo, quedó tu pre-registro. Ojo: no es la inscripción definitiva; "
                   "un asesor de admisiones te llamará para completarla. ¡Gracias!")
        return FlowReply([msg], done=True, saved_id=saved)
