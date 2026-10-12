from src.config import get_settings


settings = get_settings()

TTS_PROVIDER = settings["tts"].get("provider", "piper").lower().strip()


if TTS_PROVIDER == "xtts":
    from src.tts_xtts import speak, warm_up_tts, speak_to_file
elif TTS_PROVIDER == "piper":
    from src.tts_piper import speak, warm_up_tts, speak_to_file
else:
    raise ValueError(
        f"Proveedor TTS no soportado: {TTS_PROVIDER}. "
        "Usa 'piper' o 'xtts' en config.yaml."
    )