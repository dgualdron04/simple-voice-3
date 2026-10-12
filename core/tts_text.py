import re


DIGITOS = {
    "0": "cero",
    "1": "uno",
    "2": "dos",
    "3": "tres",
    "4": "cuatro",
    "5": "cinco",
    "6": "seis",
    "7": "siete",
    "8": "ocho",
    "9": "nueve",
}


UNIDADES = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
    "dieciocho", "diecinueve"
]


DECENAS = {
    20: "veinte",
    30: "treinta",
    40: "cuarenta",
    50: "cincuenta",
    60: "sesenta",
    70: "setenta",
    80: "ochenta",
    90: "noventa",
}


CENTENAS = {
    100: "cien",
    200: "doscientos",
    300: "trescientos",
    400: "cuatrocientos",
    500: "quinientos",
    600: "seiscientos",
    700: "setecientos",
    800: "ochocientos",
    900: "novecientos",
}


def fix_mojibake(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    for _ in range(2):
        if "Ã" in text or "Â" in text:
            try:
                fixed = text.encode("latin1").decode("utf-8")
                if fixed != text:
                    text = fixed
                    continue
            except UnicodeError:
                pass

            text = text.replace("Â", "")
            break

    return text


def digits_to_words(text: str) -> str:
    digits = re.sub(r"\D", "", str(text))
    return " ".join(DIGITOS[digit] for digit in digits if digit in DIGITOS)


def normalize_phone_for_speech(match):
    main_number = match.group(1)
    extension = match.group(2)

    spoken = f"teléfono {digits_to_words(main_number)}"

    if extension:
        spoken += f", extensión {digits_to_words(extension)}"

    return spoken


def normalize_extension_for_speech(match):
    extension = match.group(1)
    return f"extensión {digits_to_words(extension)}"


def number_to_spanish(n: int) -> str:
    if n < 20:
        return UNIDADES[n]

    if n < 30:
        if n == 20:
            return "veinte"
        return "veinti" + UNIDADES[n - 20]

    if n < 100:
        decena = (n // 10) * 10
        unidad = n % 10

        if unidad == 0:
            return DECENAS[decena]

        return f"{DECENAS[decena]} y {UNIDADES[unidad]}"

    if n < 1000:
        if n in CENTENAS:
            return CENTENAS[n]

        centena = (n // 100) * 100
        resto = n % 100

        if centena == 100:
            return f"ciento {number_to_spanish(resto)}"

        return f"{CENTENAS[centena]} {number_to_spanish(resto)}"

    if n < 1_000_000:
        miles = n // 1000
        resto = n % 1000

        if miles == 1:
            text = "mil"
        else:
            text = f"{number_to_spanish(miles)} mil"

        if resto:
            text += f" {number_to_spanish(resto)}"

        return text

    if n < 1_000_000_000:
        millones = n // 1_000_000
        resto = n % 1_000_000

        if millones == 1:
            text = "un millón"
        else:
            text = f"{number_to_spanish(millones)} millones"

        if resto:
            text += f" {number_to_spanish(resto)}"

        return text

    return str(n)


def normalize_price_for_speech(match):
    raw_number = match.group(1)
    digits = re.sub(r"\D", "", raw_number)

    if not digits:
        return raw_number

    number = int(digits)
    return f"{number_to_spanish(number)} pesos colombianos"


def clean_tts_text(text: str) -> str:
    text = fix_mojibake(text).strip()

    text = text.replace("ZUU", "Zú")
    text = text.replace("UDI", "U D I")

    text = text.replace("**", "")
    text = text.replace("*", "")
    text = text.replace("#", "")

    # Teléfonos con extensión: teléfono 6352525 Ext. 129
    text = re.sub(
        r"\b(?:tel[eé]fono|telefono|tel\.?)\s*:?\s*(\d[\d\s().-]{5,})(?:\s*(?:ext\.?|extensi[oó]n)\s*\.?:?\s*(\d+))?",
        normalize_phone_for_speech,
        text,
        flags=re.IGNORECASE
    )

    # Extensiones sueltas: Ext. 129
    text = re.sub(
        r"\b(?:ext\.?|extensi[oó]n)\s*\.?:?\s*(\d+)\b",
        normalize_extension_for_speech,
        text,
        flags=re.IGNORECASE
    )

    # Precios: 1.699.200, 1,699,200, $1.699.200, 1.699.200 pesos
    text = re.sub(
        r"\$?\s*(\d{1,3}(?:[.,]\d{3})+)(?:\s*(?:pesos(?:\s+colombianos)?|cop))?",
        normalize_price_for_speech,
        text,
        flags=re.IGNORECASE
    )

    text = text.replace("$", "")
    text = text.replace(":", ". ")
    text = text.replace(";", ". ")
    text = text.replace("/", " o ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()