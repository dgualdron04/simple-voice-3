import re

from src.structured_answer import (
    detect_program,
    get_all_programs,
    row_get,
    normalize,
    format_money,
    get_aliases_for_program,
)


def money_to_int(value: str) -> int | None:
    if not value:
        return None

    digits = "".join(char for char in str(value) if char.isdigit())

    if not digits:
        return None

    return int(digits)


def extract_number(text: str) -> int | None:
    match = re.search(r"\d+", str(text))

    if not match:
        return None

    return int(match.group(0))


def get_semesters(program) -> int | None:
    duracion = row_get(program, "duracion")
    return extract_number(duracion)


def get_credits(program) -> int | None:
    return extract_number(row_get(program, "creditos"))


def get_cost_by_question(program, question: str):
    q = normalize(question)

    if "virtual" in q:
        return row_get(program, "valor_virtual"), "modalidad virtual"

    if any(word in q for word in ["nocturna", "noche", "nocturno"]):
        return row_get(program, "valor_nocturna"), "jornada nocturna"

    if any(word in q for word in ["diurna", "dia", "diurno"]):
        return row_get(program, "valor_diurna"), "jornada diurna"

    value = (
        row_get(program, "valor_diurna")
        or row_get(program, "valor_nocturna")
        or row_get(program, "valor_virtual")
        or row_get(program, "valor_general")
    )

    return value, "valor disponible"


def get_primary_cost(program) -> int | None:
    value = (
        row_get(program, "valor_diurna")
        or row_get(program, "valor_nocturna")
        or row_get(program, "valor_virtual")
        or row_get(program, "valor_general")
    )

    return money_to_int(value)


def asks_total_cost(question: str) -> bool:
    q = normalize(question)

    signals = [
        "total",
        "toda la carrera",
        "carrera completa",
        "completa",
        "todos los semestres",
        "cuanto me cuesta estudiar",
        "cuanto cuesta estudiar",
        "cuanto me saldria",
        "cuanto saldria",
        "costo total",
        "valor total",
    ]

    return any(signal in q for signal in signals)


def asks_custom_semesters_cost(question: str) -> bool:
    q = normalize(question)

    return (
        "semestre" in q
        and any(word in q for word in ["cuanto", "costo", "valor", "precio", "pago", "pagaria"])
        and extract_number(q) is not None
    )


def asks_semesters_to_months(question: str) -> bool:
    q = normalize(question)

    return (
        "semestre" in q
        and "mes" in q
        and extract_number(q) is not None
    )


def asks_program_duration_in_months(question: str) -> bool:
    q = normalize(question)

    return (
        "mes" in q
        and any(word in q for word in ["cuanto dura", "duracion", "dura", "tiene"])
    )


def asks_affordable_semesters(question: str) -> bool:
    q = normalize(question)

    signals = [
        "cuantos semestres puedo pagar",
        "cuantos semestres me alcanza",
        "hasta cuantos semestres",
        "si tengo",
        "me alcanza para",
    ]

    return any(signal in q for signal in signals)


def asks_compare_cost(question: str) -> bool:
    q = normalize(question)

    signals = [
        "mas barata",
        "mas barato",
        "mas economica",
        "mas economico",
        "mas costosa",
        "mas caro",
        "comparar costo",
        "comparar costos",
        "compara costos",
        "cual cuesta mas",
        "cual cuesta menos",
    ]

    return any(signal in q for signal in signals)


def asks_compare_duration(question: str) -> bool:
    q = normalize(question)

    signals = [
        "dura menos",
        "dura mas",
        "menor duracion",
        "mayor duracion",
        "cual es mas corta",
        "comparar duracion",
    ]

    return any(signal in q for signal in signals)


def asks_compare_credits(question: str) -> bool:
    q = normalize(question)

    signals = [
        "mas creditos",
        "menos creditos",
        "cual tiene mas creditos",
        "cual tiene menos creditos",
        "comparar creditos",
    ]

    return any(signal in q for signal in signals)


def asks_general_comparison(question: str) -> bool:
    q = normalize(question)

    signals = [
        "diferencia",
        "diferencias",
        "diferencia entre",
        "que diferencia",
        "en que se diferencian",
        "comparar",
        "compara",
        "comparacion",
        "versus",
        " vs ",
    ]

    return any(signal in q for signal in signals)


def asks_best_program(question: str) -> bool:
    q = normalize(question)

    signals = [
        "cual es mejor",
        "que carrera es mejor",
        "mejor carrera",
        "cual me recomiendas",
    ]

    return any(signal in q for signal in signals)


def asks_all_programs(question: str) -> bool:
    q = normalize(question)

    signals = [
        "todas las carreras",
        "todos los programas",
        "que carreras hay",
        "programas disponibles",
        "carreras disponibles",
        "lista de carreras",
        "lista de programas",
        "carreras que oferta",
        "carreras que ofertas",
        "carreras que ofrecen",
        "programas que oferta",
        "programas que ofertas",
        "que oferta la udi",
        "que ofertas",
    ]

    return any(signal in q for signal in signals)


def asks_programs_by_jornada(question: str) -> bool:
    q = normalize(question)

    return (
        any(word in q for word in ["carreras", "programas"])
        and any(word in q for word in ["nocturna", "nocturno", "noche", "diurna", "diurno", "virtual"])
    )


def asks_program_jornada_availability(question: str) -> bool:
    q = normalize(question)

    return (
        any(word in q for word in ["hay", "tiene", "ofrece", "maneja"])
        and any(word in q for word in ["nocturna", "nocturno", "noche", "diurna", "diurno", "virtual"])
    )


def find_programs_in_question(question: str) -> list:
    q = normalize(question)
    found = []

    for program in get_all_programs():
        nombre = row_get(program, "nombre")
        titulo = row_get(program, "titulo")

        candidates = [nombre, titulo]
        candidates.extend(get_aliases_for_program(nombre))

        for candidate in candidates:
            candidate_norm = normalize(candidate)

            if not candidate_norm:
                continue

            if candidate_norm in q:
                found.append(program)
                break

    unique = []
    seen = set()

    for program in found:
        nombre = row_get(program, "nombre")

        if nombre not in seen:
            seen.add(nombre)
            unique.append(program)

    return unique


def answer_total_cost(program, question: str) -> str | None:
    nombre = row_get(program, "nombre")
    semesters = get_semesters(program)

    value, label = get_cost_by_question(program, question)
    semester_cost = money_to_int(value)

    if not semesters or not semester_cost:
        return None

    total = semester_cost * semesters

    return (
        f"{nombre} cuesta {format_money(str(semester_cost))} por semestre según el {label}. "
        f"Como dura {semesters} semestres, el costo total aproximado es {format_money(str(total))}."
    )


def answer_custom_semesters_cost(program, question: str) -> str | None:
    q = normalize(question)
    nombre = row_get(program, "nombre")

    semesters = extract_number(q)
    value, label = get_cost_by_question(program, question)
    semester_cost = money_to_int(value)

    if not semesters or not semester_cost:
        return None

    total = semester_cost * semesters

    return (
        f"{nombre} cuesta {format_money(str(semester_cost))} por semestre según el {label}. "
        f"Para {semesters} semestres, el valor aproximado sería {format_money(str(total))}."
    )


def answer_semesters_to_months(question: str) -> str | None:
    q = normalize(question)
    semesters = extract_number(q)

    if not semesters:
        return None

    months = semesters * 6

    return f"{semesters} semestres equivalen aproximadamente a {months} meses."


def answer_program_duration_months(program, question: str) -> str | None:
    nombre = row_get(program, "nombre")
    semesters = get_semesters(program)

    if not semesters:
        return None

    months = semesters * 6

    return f"{nombre} dura {semesters} semestres, aproximadamente {months} meses."


def answer_affordable_semesters(program, question: str) -> str | None:
    nombre = row_get(program, "nombre")

    value, label = get_cost_by_question(program, question)
    semester_cost = money_to_int(value)

    numbers = re.findall(r"\d+(?:[.,]\d+)*", question)

    if not numbers or not semester_cost:
        return None

    budget = money_to_int(numbers[0])

    if not budget:
        return None

    semesters = budget // semester_cost

    return (
        f"Con {format_money(str(budget))}, podrías pagar aproximadamente {semesters} semestre(s) "
        f"de {nombre}, tomando como base {format_money(str(semester_cost))} por semestre."
    )


def answer_compare_cost(question: str) -> str | None:
    programs = find_programs_in_question(question)

    if len(programs) < 2:
        return None

    compared = []

    for program in programs[:4]:
        value, label = get_cost_by_question(program, question)
        cost = money_to_int(value)

        if cost:
            compared.append((program, cost, label))

    if len(compared) < 2:
        return None

    compared.sort(key=lambda item: item[1])

    cheapest = compared[0]
    expensive = compared[-1]

    return (
        f"La más económica es {row_get(cheapest[0], 'nombre')}, con {format_money(str(cheapest[1]))} por semestre. "
        f"La de mayor valor es {row_get(expensive[0], 'nombre')}, con {format_money(str(expensive[1]))} por semestre."
    )


def answer_compare_duration(question: str) -> str | None:
    programs = find_programs_in_question(question)

    if len(programs) < 2:
        return None

    compared = []

    for program in programs[:4]:
        semesters = get_semesters(program)

        if semesters:
            compared.append((program, semesters))

    if len(compared) < 2:
        return None

    compared.sort(key=lambda item: item[1])

    shortest = compared[0]
    longest = compared[-1]

    return (
        f"La carrera de menor duración es {row_get(shortest[0], 'nombre')}, con {shortest[1]} semestres. "
        f"La de mayor duración es {row_get(longest[0], 'nombre')}, con {longest[1]} semestres."
    )


def answer_compare_credits(question: str) -> str | None:
    programs = find_programs_in_question(question)

    if len(programs) < 2:
        return None

    compared = []

    for program in programs[:4]:
        credits = get_credits(program)

        if credits:
            compared.append((program, credits))

    if len(compared) < 2:
        return None

    compared.sort(key=lambda item: item[1])

    lowest = compared[0]
    highest = compared[-1]

    return (
        f"{row_get(highest[0], 'nombre')} tiene más créditos, con {highest[1]}. "
        f"{row_get(lowest[0], 'nombre')} tiene menos créditos, con {lowest[1]}."
    )


def program_short_info(program) -> str:
    nombre = row_get(program, "nombre")
    titulo = row_get(program, "titulo")
    duracion = row_get(program, "duracion")
    modalidad = row_get(program, "modalidad")
    creditos = row_get(program, "creditos")
    cost = get_primary_cost(program)

    parts = [nombre]

    if titulo:
        parts.append(f"título: {titulo}")

    if duracion:
        parts.append(f"duración: {duracion}")

    if modalidad:
        parts.append(f"modalidad: {modalidad}")

    if creditos:
        parts.append(f"créditos: {creditos}")

    if cost:
        parts.append(f"valor desde {format_money(str(cost))}")

    return ", ".join(parts)


def answer_general_comparison(question: str) -> str | None:
    programs = find_programs_in_question(question)

    if len(programs) < 2:
        return None

    first = programs[0]
    second = programs[1]

    return (
        f"{program_short_info(first)}. "
        f"{program_short_info(second)}."
    )


def answer_best_program(question: str) -> str | None:
    programs = find_programs_in_question(question)

    if len(programs) >= 2:
        names = " y ".join(row_get(program, "nombre") for program in programs[:2])
        return (
            f"No puedo decir cuál es mejor entre {names} sin un criterio. "
            f"Puedo compararlas por costo, duración, créditos, modalidad o campo laboral."
        )

    return (
        "No puedo decir cuál carrera es mejor sin un criterio. "
        "Puedo compararlas por costo, duración, créditos, modalidad o campo laboral."
    )


def answer_all_programs(question: str) -> str | None:
    programs = get_all_programs()

    names = [
        row_get(program, "nombre")
        for program in programs
        if row_get(program, "nombre")
    ]

    if not names:
        return None

    return "Los programas registrados en mi base local son: " + ", ".join(names) + "."


def answer_programs_by_jornada(question: str) -> str | None:
    q = normalize(question)
    programs = get_all_programs()
    results = []

    for program in programs:
        nombre = row_get(program, "nombre")
        jornada = normalize(row_get(program, "jornada"))
        observacion = normalize(row_get(program, "observacion"))

        if any(word in q for word in ["nocturna", "nocturno", "noche"]):
            if row_get(program, "valor_nocturna") or "nocturna" in jornada or "nocturna" in observacion:
                results.append(nombre)

        elif any(word in q for word in ["diurna", "diurno"]):
            if row_get(program, "valor_diurna") or "diurna" in jornada:
                results.append(nombre)

        elif "virtual" in q:
            if row_get(program, "valor_virtual") or "virtual" in jornada:
                results.append(nombre)

    if not results:
        return "No encontré programas con esa jornada en mi base local de la UDI."

    return "En mi base local aparecen estos programas con esa jornada: " + ", ".join(results) + "."


def answer_program_jornada_availability(program, question: str) -> str | None:
    q = normalize(question)
    nombre = row_get(program, "nombre")

    jornada = row_get(program, "jornada")
    observacion = row_get(program, "observacion")

    if any(word in q for word in ["nocturna", "nocturno", "noche"]):
        if row_get(program, "valor_nocturna") or "nocturna" in normalize(jornada) or "nocturna" in normalize(observacion):
            return f"Sí, {nombre} registra información de jornada nocturna en mi base local."
        return f"No encontré jornada nocturna para {nombre} en mi base local."

    if any(word in q for word in ["diurna", "diurno"]):
        if row_get(program, "valor_diurna") or "diurna" in normalize(jornada):
            return f"Sí, {nombre} registra información de jornada diurna en mi base local."
        return f"No encontré jornada diurna para {nombre} en mi base local."

    if "virtual" in q:
        if row_get(program, "valor_virtual") or "virtual" in normalize(jornada):
            return f"Sí, {nombre} registra información de modalidad virtual en mi base local."
        return f"No encontré modalidad virtual para {nombre} en mi base local."

    return None


def answer_reasoning_question(question: str) -> str | None:
    program = detect_program(question)

    if asks_all_programs(question):
        return answer_all_programs(question)

    if asks_programs_by_jornada(question):
        return answer_programs_by_jornada(question)

    if asks_best_program(question):
        return answer_best_program(question)

    if asks_compare_cost(question):
        return answer_compare_cost(question)

    if asks_compare_duration(question):
        return answer_compare_duration(question)

    if asks_compare_credits(question):
        return answer_compare_credits(question)

    if asks_general_comparison(question):
        return answer_general_comparison(question)

    if asks_semesters_to_months(question):
        return answer_semesters_to_months(question)

    if program is None:
        return None

    if asks_program_jornada_availability(question):
        return answer_program_jornada_availability(program, question)

    if asks_program_duration_in_months(question):
        return answer_program_duration_months(program, question)

    if asks_affordable_semesters(question):
        return answer_affordable_semesters(program, question)

    if asks_total_cost(question):
        return answer_total_cost(program, question)

    if asks_custom_semesters_cost(question):
        return answer_custom_semesters_cost(program, question)

    return None