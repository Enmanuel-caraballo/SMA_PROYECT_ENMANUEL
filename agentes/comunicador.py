"""
SMA Movilidad y Seguridad CTG - CANALES DEL AGENTE COMUNICADOR
==============================================================
- redactar(datos): un LLM redacta el aviso en lenguaje natural a partir de los datos del plan
  (Ollama local por defecto, o Gemini). Si el LLM falla, tarda o se sale de las reglas, se usa la plantilla.
- enviar(receptor, texto): lo manda por Telegram al grupo de la Ciudadanía o al de la Policía.
- Configuración en .env (no se sube a Git; plantilla en .env.ejemplo).

Reglas que el código garantiza (no dependen del LLM):
  - Una alerta de Seguridad va SOLO al grupo de la Policía y el texto dice "posible coincidencia".
  - El LLM no puede agregar datos: si su texto no conserva los cruces/cifras clave, se usa la plantilla.

Uso aparte:
  python agentes/comunicador.py --buscar-chats     encuentra los grupos del bot y guarda sus IDs en .env
  python agentes/comunicador.py --probar           redacta y envía un aviso de prueba a cada grupo
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENV = RAIZ / ".env"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conocimiento as K  # noqa: E402


def cargar_env() -> dict:
    datos = {}
    if ENV.exists():
        for linea in ENV.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def guardar_env(cambios: dict) -> None:
    lineas = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    hechos = set()
    for i, l in enumerate(lineas):
        k = l.split("=", 1)[0].strip()
        if k in cambios:
            lineas[i] = f"{k}={cambios[k]}"
            hechos.add(k)
    lineas += [f"{k}={v}" for k, v in cambios.items() if k not in hechos]
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- Telegram
def telegram(metodo: str, **param):
    token = cargar_env().get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("falta TELEGRAM_TOKEN en .env")
    datos = urllib.parse.urlencode(param).encode() if param else None
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{metodo}", datos,
                                    timeout=K.TELEGRAM_TIMEOUT_S) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:         # sin el token en el mensaje de error
        raise RuntimeError(f"Telegram {metodo}: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram {metodo}: sin conexión ({e.reason})") from None


def chat_de(receptor: str) -> str | None:
    env = cargar_env()
    return env.get("TELEGRAM_CHAT_POLICIA" if receptor == "Policia" else "TELEGRAM_CHAT_CIUDADANIA") or None


def enviar(receptor: str, texto: str) -> str:
    """Envía al grupo del receptor. Devuelve un texto corto para la bitácora (nunca lanza)."""
    if os.environ.get("SMA_SIN_TELEGRAM"):
        return "Telegram desactivado (SMA_SIN_TELEGRAM, prueba automática)"
    chat = chat_de(receptor)
    if not cargar_env().get("TELEGRAM_TOKEN"):
        return "Telegram sin configurar (.env)"
    if not chat:
        return f"sin grupo de {receptor} en .env (python agentes/comunicador.py --buscar-chats)"
    try:
        telegram("sendMessage", chat_id=chat, text=texto[:4000], disable_web_page_preview="true")
        return f"enviado por Telegram al grupo de {receptor}"
    except RuntimeError as e:
        return f"no se pudo enviar por Telegram: {e}"


def buscar_chats() -> None:
    """Lee los últimos mensajes del bot y guarda en .env los grupos cuyo nombre diga Policía / Ciudadanía."""
    grupos = {}
    for u in telegram("getUpdates", allowed_updates=json.dumps(["message", "my_chat_member", "channel_post"])):
        for k in ("message", "my_chat_member", "channel_post"):
            if k in u and u[k]["chat"]["type"] in ("group", "supergroup", "channel"):
                c = u[k]["chat"]
                grupos[c["id"]] = c.get("title", "")
    if not grupos:
        print("No veo grupos. Escriba /start@<su_bot> en cada grupo (o quite y vuelva a agregar el bot) y repita.")
        return
    cambios = {}
    for i, titulo in grupos.items():
        t = titulo.lower()
        if "polic" in t:
            cambios["TELEGRAM_CHAT_POLICIA"] = i
        elif "ciudad" in t:
            cambios["TELEGRAM_CHAT_CIUDADANIA"] = i
        print(f"  {i}  {titulo}")
    if cambios:
        guardar_env(cambios)
        print("Guardado en .env:", ", ".join(cambios))
    else:
        print("Ningún grupo se llama 'Policía' o 'Ciudadanía': copie los IDs a .env a mano.")


# ---------------------------------------------------------------- redacción
def plantilla(datos: dict) -> str:
    a = datos.get("alerta", {})
    minutos = round(datos.get("duracion", 300) / 60, 1)
    if datos.get("receptor") == "Policia":
        return f"🚨 POLICÍA – Alerta {a.get('id', '')}: {datos.get('aviso', '')}"
    return (f"🚦 Aviso de movilidad en Bocagrande: {datos.get('aviso', '')} "
            f"Medida: {datos.get('plan', '-')} (~{minutos:g} min).")


INSTRUCCIONES = """Eres el Comunicador de un sistema de tránsito de Bocagrande (Cartagena). Reescribe el AVISO en español
de Colombia, claro y breve (máximo {max} palabras), para {publico}. Reglas:
- No inventes nada: usa solo los datos del aviso; no agregues indicaciones que no estén en él; conserva los
  nombres de calles y cruces y las cifras.
- {regla}
- Sin saludos ni despedidas, sin markdown, sin comillas. Un solo párrafo, puede empezar con un emoji.
- La MEDIDA es lo que hizo el sistema de semáforos (no es una vía a evitar); menciónala tal cual y su duración.
AVISO: {aviso}
MEDIDA (la aplican los semáforos del sistema): {plan} durante ~{minutos} min."""


def _prompt(datos: dict) -> str:
    policia = datos.get("receptor") == "Policia"
    return INSTRUCCIONES.format(
        max=K.LLM_MAX_PALABRAS, publico="la Policía (operativo)" if policia else "la ciudadanía (conductores)",
        regla=("Di 'posible coincidencia' (no es una identificación confirmada) e incluye la ruta probable y el punto "
               "de intercepción." if policia else "Di qué evitar y qué ruta tomar; tono sereno."),
        aviso=datos.get("aviso", ""), plan=datos.get("plan", "-"),
        minutos=round(datos.get("duracion", 300) / 60, 1))


def _post_json(url: str, cuerpo: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _llm(prompt: str, env: dict) -> str:
    proveedor = env.get("LLM", "ollama").lower()
    if proveedor == "gemini":
        clave = env.get("GEMINI_API_KEY")
        if not clave:
            raise RuntimeError("falta GEMINI_API_KEY")
        modelo = env.get("GEMINI_MODELO", "gemini-2.5-flash")
        r = _post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={clave}",
                       {"contents": [{"parts": [{"text": prompt}]}]}, K.LLM_TIMEOUT_S)
        return r["candidates"][0]["content"]["parts"][0]["text"]
    if proveedor == "ollama":
        r = _post_json(env.get("OLLAMA_URL", "http://localhost:11434") + "/api/generate",
                       {"model": env.get("OLLAMA_MODELO", "llama3.2:3b"), "prompt": prompt, "stream": False,
                        "options": {"temperature": 0, "num_predict": 200}}, K.LLM_TIMEOUT_S)
        return r["response"]
    raise RuntimeError(f"LLM '{proveedor}' desconocido")


def _claves(texto: str) -> set:
    """Datos que el LLM debe conservar: cruces, números de calles/carreras y cifras enteras (155 s).
    Los decimales (~2.8 min, probabilidad 0.58) puede redondearlos."""
    return (set(re.findall(r"cruce\d", texto)) | set(re.findall(r"«([^»]+)»", texto))     # «alias» del solicitado
            | set(re.findall(r"(?<![\d.,])\d+(?![\d]|[.,]\d)", texto)))


def sin_ids(texto: str) -> str:
    """'cruce1 (Av. San Martín × Calle 6)' -> 'Av. San Martín × Calle 6'; quita también '(cruce5)' sueltos."""
    salida, i = [], 0
    for m in re.finditer(r"cruce\d \(", texto):
        if m.start() < i:
            continue
        nivel, j = 1, m.end()
        while j < len(texto) and nivel:
            nivel += {"(": 1, ")": -1}.get(texto[j], 0)
            j += 1
        salida.append(texto[i:m.start()] + texto[m.end():j - 1])
        i = j
    texto = "".join(salida) + texto[i:]
    return re.sub(r"\s*\(cruce\d\)", "", texto)


def redactar(datos: dict) -> tuple[str, str]:
    """(texto, origen). origen = 'LLM <proveedor>' o 'plantilla (<motivo>)'."""
    if datos.get("receptor") != "Policia":       # la ciudadanía no conoce los ids internos (cruce1…cruce5)
        datos = {**datos, "aviso": sin_ids(datos.get("aviso", ""))}
    base = plantilla(datos)
    env = cargar_env()
    marca = "PRUEBA DEL SISTEMA: "
    prueba = datos.get("aviso", "").startswith(marca)
    if prueba:                                   # la marca de prueba no se deja en manos del LLM
        datos = {**datos, "aviso": datos["aviso"][len(marca):]}
    if env.get("LLM", "ollama").lower() in ("", "no", "ninguno"):
        return base, "plantilla"
    try:
        texto = _llm(_prompt(datos), env).strip().strip('"')
    except urllib.error.URLError as e:
        motivo = ("Ollama no está corriendo" if env.get("LLM", "ollama").lower() == "ollama"
                  and "10061" in str(e.reason) else str(e.reason)[:80])
        return base, f"plantilla ({motivo})"
    except Exception as e:  # noqa: BLE001  (sin LLM el aviso sale igual)
        return base, f"plantilla (LLM no disponible: {str(e)[:80]})"
    texto = re.sub(r"[*_#`]", "", texto)
    if datos.get("receptor") == "Policia":
        texto = texto.lstrip("🚨 ").strip()
    policia = datos.get("receptor") == "Policia"
    faltan = _claves(datos.get("aviso", "")) - _claves(texto)
    if not texto or len(texto.split()) > 2 * K.LLM_MAX_PALABRAS:
        return base, "plantilla (respuesta del LLM vacía o larga)"
    if policia and "posible coincidencia" not in texto.lower():
        return base, "plantilla (el LLM omitió 'posible coincidencia')"
    if faltan:
        return base, f"plantilla (el LLM omitió {', '.join(sorted(faltan)[:4])})"
    prefijo = f"🚨 POLICÍA – Alerta {datos.get('alerta', {}).get('id', '')}: " if policia else ""
    return prefijo + (marca if prueba else "") + texto, f"LLM {env.get('LLM', 'ollama')}"


def _probar() -> None:
    ejemplos = [
        {"receptor": "Ciudadania", "plan": "Contraflujo Carrera 1", "duracion": 300,
         "alerta": {"id": "PRUEBA-1", "tipo": "Movilidad"},
         "aviso": "PRUEBA DEL SISTEMA: accidente en Av. San Martín × Calle 15 (cruce5); salga por la Carrera 1."},
        {"receptor": "Policia", "plan": "Cerco semafórico", "duracion": 300,
         "alerta": {"id": "PRUEBA-2", "tipo": "Seguridad"},
         "aviso": "PRUEBA DEL SISTEMA: posible coincidencia en Av. San Martín × Calle 10 (cruce3); ruta probable de huida "
                  "por Carrera 1 → Calle 15; intercepción sugerida en cruce5 (~155 s)."},
    ]
    for d in ejemplos:
        texto, origen = redactar(d)
        print(f"[{d['receptor']}] ({origen}) {texto}\n  -> {enviar(d['receptor'], texto)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    if "--buscar-chats" in sys.argv:
        buscar_chats()
    elif "--probar" in sys.argv:
        _probar()
    else:
        print(__doc__)
