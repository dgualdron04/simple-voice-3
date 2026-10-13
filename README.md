# Asistente de voz UDI — Zuu (G1), Axl (Go2 X), Teamsu (H2)

Asistente de voz **100 % offline** en español para robots Unitree. Un solo código base; lo que cambia
entre robots vive en `robots/*.yaml`.

| Robot | Nombre | Config | Hardware |
|---|---|---|---|
| Unitree G1 EDU | **Zuu** | `robots/g1.yaml` | Jetson Orin NX (8 o 16 GB, *por verificar*) |
| Unitree Go2 X | **Axl** | `robots/go2x.yaml` | módulo externo Orin Nano Super/NX + altavoz-micrófono USB con AEC |
| Unitree H2 EDU | **Teamsu** | `robots/h2.yaml` | Jetson AGX Thor (el i5 de locomoción no se toca) |
| Portátil (desarrollo) | Zuu | `robots/laptop.yaml` | micrófono y parlante locales |

## Uso rápido (portátil)

```powershell
.venv\Scripts\python.exe -m core.main --robot laptop        # asistente
.venv\Scripts\python.exe -m core.main --devices             # lista dispositivos de audio
.venv\Scripts\python.exe -m pytest                          # pruebas (sin hardware)
.venv\Scripts\python.exe scripts\bench_components.py        # latencia de STT / TTS / LLM
.venv\Scripts\python.exe scripts\selftest_pipeline.py       # prueba extremo a extremo sin hablar (suena el parlante)
.venv\Scripts\python.exe scripts\export_registros.py --robot g1 --out E:\   # exporta a CSV y BORRA
```

Requiere Ollama corriendo con el modelo de `llm.model`. Al arrancar se precarga el LLM y se deja residente
(`keep_alive=-1`), así la primera respuesta no tarda decenas de segundos.
Todo corre en local: `HF_HUB_OFFLINE=1` se fija solo, nada intenta bajar modelos.

## Cómo funciona (y por qué responde rápido y no se “vuelve loco”)

```
mic → VAD Silero → Whisper → DialogProcessor ─┬─ respuesta directa (reglas/RAG/registro/gestos) → Piper → parlante
                                              └─ solo si hace falta: LLM (Ollama, streaming) → Piper por frases
```

* **La mayoría de turnos no usan el LLM**: costos, duración, comparaciones, gestos, comandos y el registro
  son código determinista (`core/dialog.py`, `core/knowledge/`, `core/registration/`). Latencia típica:
  fin de frase → voz ≈ STT (~2 s en CPU) + 0,2 s. Con LLM: + ~0,2–0,7 s al primer token y se habla frase por frase.
* El LLM solo hace **conversación libre** y **redacta con el contexto local** cuando la pregunta es de la UDI
  y no hay respuesta exacta. Si no hay contexto, **no inventa**: dice que no tiene el dato y ofrece dejar el
  teléfono para que un asesor llame.
* **Anti-bucle / anti-eco** (el riesgo principal sin AEC):
  1. *Half-duplex*: mientras el robot habla (y `post_speech_mute_secs` después) el micrófono se ignora
     (`TailMuteStrategy` + segunda barrera en `DialogProcessor`).
  2. Filtro de texto: descarta transcripciones casi idénticas a lo último que dijo el robot.
  3. Filtro de alucinaciones de Whisper (“gracias por ver el video”, “subtítulos…”) y de ruido (palabras sueltas).
  4. Activación por texto: «**robot** + nombre del robot» (p. ej. «robot Zuu», «robot Axl», «robot Teamsu»; también
     «hola robot Zuu»). Whisper deforma el nombre («rotsu», «rhozu»): hay coincidencia difusa además de los `robot.aliases` de cada YAML.
     Luego la sesión dura `wake.session_secs` sin repetirlo. Un robot ignora la frase de otro.
  5. Todo flujo tiene reintentos acotados y caducidad por inactividad; una excepción en el diálogo se
     captura y el robot sigue escuchando.
  6. Si Ollama no está, el robot sigue con respuestas directas y avisa; no se queda mudo ni reintentando.
* **Cultura general fuera de la UDI** («¿quién es Elon Musk?», «capital de Francia»): `dialog.factual_policy: decline` (por defecto)
  responde que no tiene información confiable y reconduce a la UDI, en vez de dejar que el modelo de 3 B invente. Con `llm`
  responde con el LLM (puede alucinar). La charla informal («cuéntame cómo va tu día») sí va al LLM.
* `barge_in: true` (interrumpir al robot hablando) **solo** donde hay AEC por hardware (Go2 X con altavoz-micrófono USB).

## Seguridad (crítico en el H2: 1,82 m, ~70 kg)

* El LLM **nunca** controla locomoción ni elige acciones del robot. Los gestos los dispara código por frases
  fijas, y `GestureRunner` solo ejecuta nombres de la lista blanca `gestures.allowed` del YAML.
* `adapters/g1.py` solo publica poses de brazo por `rt/arm_sdk` con rampa de peso y aborta con inclinación > 10°.
  **No usa `LocoClient`** (el prototipo tenía una función `_stand_tall` con `HighStand()`; nunca se llamaba y se eliminó).
* `tests/test_safety.py` falla si alguien importa locomoción en `core/`, `adapters/` o `scripts/`.
* H2 y Go2 X traen `gestures.enabled: false` hasta verificar su SDK (ver “Por verificar”).

## Pre-registro (Ley 1581 de 2012)

Máquina de estados en código (`core/registration/flow.py`), con los 5 nodos acordados:
consentimiento → datos personales → interés académico → confirmación (lee los datos en voz alta y permite corregir
campo por campo) → cierre. Ruta corta “solo teléfono”. Validaciones en código (`validators.py`): teléfono
colombiano (dígitos hablados o escritos), correo (“arroba”, “punto”), nombre, sí/no, carrera/modalidad/jornada
según lo que ofrezca cada programa en la base.

* Nada se guarda sin consentimiento **y** confirmación. Cancelar o abandonar descarta todo.
* Minimización: no se pide cédula; correo opcional.
* **Almacenamiento**: SQLite con cifrado **AES-256-GCM por campo** (nombre, teléfono, correo) vía `cryptography`.
  La clave está en `data/registros.key` (se genera sola; protegerla con permisos del SO). Es cifrado a nivel de
  campo, no de archivo completo (SQLCipher no está instalado; *por verificar* en Jetson si se prefiere).
* Retención: se purgan registros con más de `registration.retention_days` (30) al arrancar.
* Exportación: `scripts/export_registros.py` escribe el CSV, **verifica** que quedó completo y recién ahí borra.
* **Texto de consentimiento**: está en `CONSENT_TEXT` (`core/registration/flow.py`). **Debe revisarlo el área
  jurídica de la UDI** (finalidad, responsable, canal para ejercer derechos). El módulo no inventa datos de contacto.

> Nota de diseño: se instaló Pipecat 1.12, que ya incluye `pipecat.flows`. Para el registro **no** se usó Flows
> con LLM: un modelo de 3–4 B falla al extraer campos por function calling y puede desviarse. El LLM no participa
> en el registro; los nodos son los mismos, pero deterministas. Si más adelante se quiere Flows, la lógica de
> `validators.py` y `store.py` se reutiliza tal cual.

## Descargas pendientes (hazlas tú; el desarrollo en portátil NO necesita ninguna)

Nada se descargó automáticamente. El portátil usa lo que ya tienes: `faster-whisper small` (caché HF),
Ollama `qwen2.5:3b-instruct-zuu`, voz Piper `voices/test_epoch10100/zuu_10100`, y Silero (incluido en Pipecat).

> Los tags exactos de Ollama/HF cambian: **verifícalos** con `ollama search` / la página del modelo antes de bajar.

### Zuu — G1 (Orin NX)
| Qué | Detalle | Tamaño aprox. |
|---|---|---|
| Whisper small (CTranslate2) | `Systran/faster-whisper-small` (ya en tu caché del portátil: cópiala) | ~0,5 GB |
| ctranslate2 con CUDA para aarch64/JetPack | wheel/compilación para Jetson — *por verificar*; alternativa: whisper.cpp + CUDA (requiere nuevo servicio STT) | — |
| LLM 3–4 B Q4 | `qwen3:4b-instruct` (tag *por verificar*) o `gemma3:4b` | 2,5–3,3 GB |
| Voz Piper | tu `zuu_10100.onnx` + `.json`, o `es_MX-claude-high` (más nítida, 63 MB, ya la tienes) | 0,06 GB |

### Teamsu — H2 (Thor 128 GB)
| Qué | Detalle | Tamaño aprox. |
|---|---|---|
| Whisper large-v3-turbo | `deepdml/faster-whisper-large-v3-turbo-ct2` (*por verificar*) | ~1,6 GB |
| LLM ~30 B | `qwen3:30b-a3b-instruct-2507` (tag *por verificar*) en Ollama/llama.cpp | ~19 GB |
| TTS Kokoro (opcional) | voces en español *por verificar*; **no integrado aún** (`tts.engine: kokoro` lanza error); mientras tanto Piper | ~0,3 GB |

### Axl — Go2 X (Orin Nano Super/NX)
| Qué | Detalle | Tamaño aprox. |
|---|---|---|
| Whisper base o small | `Systran/faster-whisper-base` (más rápido) o `-small` | 0,15–0,5 GB |
| LLM pequeño | `qwen3:4b-instruct` / `gemma3:4b`, o 1,7 B si la memoria no alcanza | 1–3,3 GB |
| Voz Piper | igual que Zuu | 0,06 GB |

### Opcional
* **openWakeWord**: no se usa; la activación es por texto. Si hace falta, requiere entrenar un modelo para cada nombre
  (“Zuu”, “Axl”, “Teamsu”) — trabajo aparte.

### Espacio que puedes liberar en el portátil (ya no lo usa el sistema nuevo)
`model/vosk-model-es-0.42` (2,3 GB) y `model/vosk-model-es-0.42.zip` (1,4 GB); `~/.cache/whisper/*.pt` (2,1 GB,
openai-whisper); `faster-whisper-large-v3` (2,9 GB) y `-medium` (1,5 GB) en la caché HF si no los vas a usar;
`voices/test_epoch10009/*.ckpt` (checkpoint de entrenamiento). **No se borró nada.**

## Por verificar (no confirmable desde código/documentación disponible)

* Memoria del Orin NX del G1 (8 o 16 GB) → decide `small` vs `base` y 4 B vs menor.
* Salida de audio **por el parlante del G1** vía DDS (`AudioClient.PlayStream`): el prototipo lo hacía por chunks de 3 s
  (`legacy/src/robot_speaker.py`); **no está portado** (latencia alta). Hoy la salida es un dispositivo local/USB
  (`audio.output_device`). Si se quiere el parlante interno, hay que escribir un sumidero de audio Pipecat.
* Nombres de dispositivo del arreglo de 4 micrófonos del G1 (`arecord -l`) y si el parlante de 5 W permite AEC por software.
* ¿Exponen el H2 y el Go2 X API de gestos de brazo/cuerpo y por qué interfaz (Ethernet / WiFi propio)? Los adapters
  `h2`/`go2x` no existen: `gestures.adapter: simulated`, `enabled: false`. Solo el adapter G1 está portado (sin probar en esta rama).
* `poses_guardadas/` (hola_*.json, dar_mano_*.json) vivía fuera del repo; el adapter G1 la busca en `gestures.g1.poses_dir`.
* `onnxruntime-gpu` / `use_cuda` para Piper en Jetson, y Pipecat en aarch64 con JetPack.
* Function calling del LLM en español: **no se usa**; los gestos
  salen por frases. Medir antes de activarlo.
* El `PiperTTSService` de Pipecat usa `piper-tts` (**GPL-3.0**): revisar implicaciones de licencia si se distribuye el software.

## Estructura

```
core/            pipeline, diálogo, servicios, config, registro (cifrado, validadores, flujo), conocimiento (RAG local)
adapters/        gestos: base (lista blanca + simulado), g1 (portado), [h2, go2x pendientes]
robots/          laptop.yaml, g1.yaml, h2.yaml, go2x.yaml
scripts/         export_registros.py, bench_components.py, selftest_pipeline.py
tests/           registro, diálogo, seguridad
data/            zuu.db (conocimiento UDI), rag/*.xml; (local, ignorado) registros.db/key
legacy/          prototipo anterior (Vosk+Ollama por /api/generate, visión, emociones); solo referencia, ya no corre
```

Reingestar conocimiento (tras editar `data/rag/*.xml`): `python -m core.knowledge.xml_ingest` (reconstruye la base completa, sin duplicados).
Datos básicos de la UDI (dirección, teléfonos, correo, personería, reconocimiento): `data/rag/udi_institucional.xml`, respondidos sin LLM (`core/knowledge/institution.py`).
`data/rag/udi.xml` aún describe a «ZUU»: para Axl y Teamsu conviene una versión con su nombre.

## Qué se dejó fuera (decisión: ver plan aprobado)
Visión (llava), motor de emociones, modo imitación/mandarín y chistes por LLM quedaron en `legacy/`.
