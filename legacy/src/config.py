from pathlib import Path
import os
import re
import yaml
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = BASE_DIR / "config.yaml"
ENV_PATH = BASE_DIR / ".env"

_settings = None


def _replace_env_vars(raw_text: str) -> str:
    """
    Reemplaza variables tipo ${VARIABLE} dentro del config.yaml
    usando las variables cargadas desde .env.
    """

    pattern = re.compile(r"\$\{([A-Z0-9_]+)\}")

    def replacer(match):
        var_name = match.group(1)
        value = os.getenv(var_name)

        if value is None:
            raise ValueError(
                f"No encontré la variable {var_name} en el archivo .env"
            )

        return value

    return pattern.sub(replacer, raw_text)


def get_settings():
    global _settings

    if _settings is not None:
        return _settings

    if not ENV_PATH.exists():
        raise FileNotFoundError(f"No encontré el archivo .env en: {ENV_PATH}")

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No encontré el archivo de configuración: {CONFIG_PATH}"
        )

    load_dotenv(ENV_PATH)

    raw_config = CONFIG_PATH.read_text(encoding="utf-8")
    expanded_config = _replace_env_vars(raw_config)

    _settings = yaml.safe_load(expanded_config)

    return _settings


def get_base_dir() -> Path:
    return BASE_DIR


def resolve_path(path_value: str | Path) -> Path:
    """
    Convierte una ruta relativa del config.yaml en una ruta absoluta
    basada en la carpeta raíz del proyecto.
    """
    path = Path(path_value)

    if path.is_absolute():
        return path

    return BASE_DIR / path