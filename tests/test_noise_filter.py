"""Pruebas rápidas sin micrófono ni modelos. Ejecutar: python tests/test_noise_filter.py"""
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mock de dependencias pesadas antes de importar los módulos.
for name in ["vosk", "sounddevice", "numpy", "soundfile", "piper", "pyaudio"]:
    sys.modules.setdefault(name, mock.MagicMock())


def load_voice():
    import importlib
    stubs = {}
    for mod in ["src.assistant", "src.tts", "src.llm"]:
        stubs[mod] = mock.MagicMock()
    with mock.patch.dict(sys.modules, stubs):
        try:
            return importlib.import_module("src.assistant_voice")
        except Exception as e:
            print("import falló:", e)
            raise


def test_voice():
    v = load_voice()
    assert v.has_wake_word("resultado") is False
    assert v.has_wake_word("usuario") is False
    assert v.has_wake_word("su") is False
    assert v.has_wake_word("zuu cuanto cuesta derecho") is True
    assert v.has_wake_word("oye zuu hola") is True
    assert v.looks_like_noise("valor") is True
    assert v.looks_like_noise("") is True
    assert v.looks_like_noise("gracias") is False
    assert v.looks_like_noise("cuanto cuesta") is False
    assert v.is_rejection("Solo puedo ayudarte con información relacionada con la UDI.")
    assert v.is_rejection("No escuché una pregunta. ¿Puedes repetirla?")
    assert not v.is_rejection("La UDI está en Bucaramanga.")


def test_stt():
    import src.stt_vosk as stt

    def run(result):
        rec = mock.MagicMock()
        rec.FinalResult.return_value = json.dumps(result)
        wav = mock.MagicMock()
        wav.getnchannels.return_value = 1
        wav.getsampwidth.return_value = 2
        wav.getcomptype.return_value = "NONE"
        wav.getframerate.return_value = 16000
        wav.readframes.return_value = b""
        with mock.patch.object(stt, "wave") as w,              mock.patch.object(stt, "KaldiRecognizer", return_value=rec),              mock.patch.object(stt, "get_vosk_model", return_value=mock.MagicMock()),              mock.patch.object(stt, "build_grammar", return_value=[]),              tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            w.open.return_value.__enter__.return_value = wav
            return stt.transcribe_audio(tmp.name)

    w = lambda t, c=0.9: {"word": t, "conf": c}
    assert run({"text": "[unk]", "result": [w("[unk]")]}) == ""
    assert run({"text": "[unk] [unk]", "result": [w("[unk]"), w("[unk]")]}) == ""
    assert run({"text": "a [unk] [unk]", "result": [w("cuanto"), w("[unk]"), w("[unk]")]}) == ""
    assert run({"text": "cuanto cuesta", "result": [w("cuanto"), w("cuesta")]}) == "cuanto cuesta"
    assert run({"text": "cuanto cuesta", "result": [w("cuanto", .3), w("cuesta", .4)]}) == ""


if __name__ == "__main__":
    test_voice()
    test_stt()
    print("OK")
