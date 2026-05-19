from pathlib import Path
import yaml


CONFIG_PATH = Path("config.yaml")
_settings = None


def get_settings():
    global _settings

    if _settings is None:
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"No encontré el archivo de configuración: {CONFIG_PATH}")

        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            _settings = yaml.safe_load(file)

    return _settings