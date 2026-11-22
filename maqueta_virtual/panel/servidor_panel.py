"""
SMA Movilidad y Seguridad CTG - Servidor del panel web (maqueta virtual)
========================================================================
Se suscribe a sma/# en Mosquitto y reenvía cada mensaje al navegador por WebSocket.
El navegador muestra el gemelo digital 3D de Bocagrande (o el mapa 2D), los semáforos,
los planes de desvío activos, la bitácora de agentes y las alertas.

Uso:   python maqueta_virtual/panel/servidor_panel.py
       abrir http://localhost:8000
Opciones: --broker 192.168.1.7   --puerto-web 8000   --host 0.0.0.0 (para verlo desde otro equipo)
"""

import argparse
import asyncio
import csv
import json
import math
import random
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

CARPETA = Path(__file__).resolve().parent
RUTA_CRUCES = CARPETA.parent / "cruces.json"
RUTA_MAPA_3D = CARPETA.parent / "bocagrande" / "bocagrande.json"
DATOS_REALES = CARPETA.parent.parent / "modelos" / "datos"     # generados por modelos/datos_reales.py
TIPOS_EVENTO = {"accidente", "congestion", "aglomeracion", "placa_reportada", "persona_solicitada"}
REGISTRO_ROSTROS = CARPETA.parent.parent / "vision" / "registro" / "registro.json"   # Caso 2 (ignorado por Git)
try:
    sys.path.insert(0, str(CARPETA.parent.parent / "agentes"))
    from conocimiento import CAMARAS                                  # cámara -> cruce que vigila
except ImportError:
    CAMARAS = {}

args = None
clientes_ws: set[WebSocket] = set()
estados = {}                    # último sma/estado/<cruce>
planes = {}                     # planes activos (sma/plan/<cruce>) por id de alerta
mentes = {}                     # último sma/agentes/<agente>: reglas, parámetros y estado interno de cada agente
bitacora = deque(maxlen=300)    # últimos mensajes para quien abra la página tarde
trazas = deque(maxlen=6000)     # sma/trazas: mensajes entre agentes para el diagrama de secuencia (/secuencia)
RUTA_TRAZAS = CARPETA / "trazas" / "trazas.jsonl"   # historial en disco (ignorado por Git; sin datos biométricos)


def cargar_trazas():
    try:
        lineas = RUTA_TRAZAS.read_text(encoding="utf-8").splitlines()[-trazas.maxlen:]
    except OSError:
        return
    for linea in lineas:
        try:
            trazas.append(json.loads(linea))
        except ValueError:
            pass


def guardar_traza(dato):
    try:
        RUTA_TRAZAS.parent.mkdir(exist_ok=True)
        with RUTA_TRAZAS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dato, ensure_ascii=False))
            f.write("\n")
    except OSError:
        pass
cliente_mqtt = None


def log(texto):
    print(f"[{datetime.now():%H:%M:%S}] {'Panel':<13} | {texto}", flush=True)


def cruces():
    return json.loads(RUTA_CRUCES.read_text(encoding="utf-8"))["cruces"]


async def difundir(paquete):
    """Envía un paquete JSON a todos los navegadores conectados."""
    texto = json.dumps(paquete, ensure_ascii=False)
    for ws in list(clientes_ws):
        try:
            await ws.send_text(texto)
        except Exception:
            clientes_ws.discard(ws)


def al_mensaje_mqtt(loop):
    def manejar(cliente, userdata, msg):
        cuerpo = msg.payload.decode(errors="ignore")
        try:
            datos = json.loads(cuerpo)
        except ValueError:
            datos = cuerpo
        paquete = {"tema": msg.topic, "datos": datos, "hora": f"{datetime.now():%H:%M:%S}", "recibido": time.time()}
        partes = msg.topic.split("/")
        if len(partes) == 3 and partes[1] == "sensores":
            return                      # los publica el propio navegador; no hace falta reenviarlos
        if msg.topic == "sma/trazas" and isinstance(datos, dict):
            trazas.append(datos)
            guardar_traza(datos)
        elif len(partes) == 3 and partes[1] == "agentes":
            mentes[partes[2]] = paquete
        elif len(partes) == 3 and partes[1] == "estado":
            estados[partes[2]] = paquete
        elif len(partes) == 3 and partes[1] == "plan" and isinstance(datos, dict):
            if datos.get("activo"):
                planes[datos.get("alerta")] = paquete
            else:
                planes.pop(datos.get("alerta"), None)
        else:
            bitacora.append(paquete)
        # los callbacks de paho corren en otro hilo -> pasar al bucle de asyncio
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(difundir(paquete)))
    return manejar


@asynccontextmanager
async def ciclo_de_vida(app):
    global cliente_mqtt
    loop = asyncio.get_running_loop()
    cargar_trazas()
    cliente_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sma-panel-{args.puerto_web}")
    cliente_mqtt.on_connect = lambda c, u, f, rc, p: (c.subscribe("sma/#"),
                                                      log(f"conectado a Mosquitto {args.broker}; escuchando sma/#"))
    cliente_mqtt.on_message = al_mensaje_mqtt(loop)
    try:
        cliente_mqtt.connect(args.broker, args.puerto)
        cliente_mqtt.loop_start()
    except OSError:
        log(f"AVISO: no pude conectarme a Mosquitto en {args.broker}:{args.puerto}")
    log(f"panel en http://localhost:{args.puerto_web}")
    yield
    cliente_mqtt.loop_stop()
    cliente_mqtt.disconnect()


app = FastAPI(title="Maqueta virtual SMA CTG", lifespan=ciclo_de_vida)


@app.middleware("http")
async def sin_cache_vieja(request, call_next):
    """El navegador debe revalidar (ETag) el mapa, los scripts y las páginas: si se regenera bocagrande.json o se
    cambia bocagrande3d.js, un recargar normal ya muestra la versión nueva (antes quedaba la red vieja en caché)."""
    respuesta = await call_next(request)
    if not request.url.path.startswith("/api/"):
        respuesta.headers["Cache-Control"] = "no-cache"
    return respuesta


app.mount("/static", StaticFiles(directory=CARPETA / "static"), name="static")


@app.get("/")
def pagina():
    """Gemelo digital 3D de Bocagrande."""
    return FileResponse(CARPETA / "index.html")


@app.get("/mapa2d")
def pagina_2d():
    """Mapa 2D (Leaflet) con los mismos cruces."""
    return FileResponse(CARPETA / "mapa2d.html")


@app.get("/agentes")
def pagina_agentes():
    """Mente de los agentes: reglas, parámetros, aprendizaje y decisiones en vivo."""
    return FileResponse(CARPETA / "agentes.html")


@app.get("/secuencia")
def pagina_secuencia():
    """Diagrama de secuencia dinámico: la conversación real entre agentes de cada incidente."""
    return FileResponse(CARPETA / "secuencia.html")


@app.get("/api/trazas")
def api_trazas():
    """Mensajes entre agentes registrados (sma/trazas), del más antiguo al más reciente."""
    return JSONResponse(list(trazas))


@app.get("/datos/bocagrande.json")
def datos_bocagrande():
    return FileResponse(RUTA_MAPA_3D, media_type="application/json")


@app.get("/api/cruces")
def api_cruces():
    return JSONResponse(cruces())


class Evento(BaseModel):
    cruce: str
    tipo: str = "accidente"
    sentido: str | None = None      # solo cruce5: "salida" (hacia el Centro) o "entrada" (hacia Bocagrande)


@app.post("/api/evento")
def api_evento(evento: Evento):
    """Simula un evento en un cruce: publica en sma/eventos/<cruce> como lo haría el ESP32."""
    if evento.cruce not in {c["id"] for c in cruces()}:
        raise HTTPException(404, "cruce desconocido")
    if evento.tipo not in TIPOS_EVENTO:
        raise HTTPException(400, "tipo de evento desconocido")
    if cliente_mqtt is None or not cliente_mqtt.is_connected():
        raise HTTPException(503, "sin conexión a Mosquitto")
    if evento.sentido not in (None, "salida", "entrada"):
        raise HTTPException(400, "sentido debe ser 'salida' o 'entrada'")
    if evento.tipo == "accidente" and not evento.sentido:
        cuerpo = "accidente"
    else:
        cuerpo = json.dumps({"tipo": evento.tipo, **({"sentido": evento.sentido} if evento.sentido else {})})
    cliente_mqtt.publish(f"sma/eventos/{evento.cruce}", cuerpo)
    log(f"evento simulado desde el panel: {evento.tipo} en {evento.cruce}" + (f" ({evento.sentido})" if evento.sentido else ""))
    return {"ok": True}


@app.get("/api/datos_reales")
def api_datos_reales():
    """Perfil horario de demanda (SIMM Medellín) y resumen de accidentes reales de Bocagrande (DATT Cartagena)."""
    salida = {}
    for clave, archivo in (("perfil", "perfil_demanda.json"), ("accidentes", "accidentes_resumen.json")):
        try:
            salida[clave] = json.loads((DATOS_REALES / archivo).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            salida[clave] = None
    return JSONResponse(salida)


ENTRENADOS = CARPETA.parent.parent / "modelos" / "entrenados"


@app.get("/api/modelos")
def api_modelos():
    """Métricas de las redes neuronales (T-GCN, DQN) y comparación de controladores en SUMO."""
    def leer(nombre):
        try:
            return json.loads((ENTRENADOS / nombre).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    imagenes = sorted(p.name for p in ENTRENADOS.glob("*.png")) if ENTRENADOS.exists() else []
    return JSONResponse({"metricas_simm": leer("metricas_simm.json"), "metricas_bocagrande": leer("metricas_bocagrande.json"),
                         "comparacion": leer("comparacion_controladores.json"), "imagenes": imagenes,
                         "datos": api_datos_reales_dict()})


@app.get("/modelos/img/{nombre}")
def imagen_modelo(nombre: str):
    ruta = (ENTRENADOS / nombre).resolve()
    if ruta.parent != ENTRENADOS.resolve() or ruta.suffix != ".png" or not ruta.exists():
        raise HTTPException(404, "imagen no encontrada")
    return FileResponse(ruta, media_type="image/png")


def api_datos_reales_dict():
    salida = {}
    for clave, archivo in (("perfil", "perfil_demanda.json"), ("accidentes", "accidentes_resumen.json")):
        try:
            salida[clave] = json.loads((DATOS_REALES / archivo).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            salida[clave] = None
    return salida


def accidentes_reales():
    try:
        with open(DATOS_REALES / "accidentes_bocagrande.csv", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


class Persona(BaseModel):
    cruce: str


@app.post("/api/persona")
def api_persona(pedido: Persona):
    """Caso 2 sin webcam: simula que la cámara del cruce ve a una persona registrada (su vector + ruido), así pasa por
    Vigilancia -> Consultor (comparación) -> Clasificador -> ... igual que con la cámara real. Si el cruce no tiene
    cámara o no hay nadie registrado, publica el evento persona_solicitada directamente (marcado como simulado)."""
    if cliente_mqtt is None or not cliente_mqtt.is_connected():
        raise HTTPException(503, "sin conexión a Mosquitto")
    camara = next((c for c, cr in CAMARAS.items() if cr == pedido.cruce), None)
    try:
        personas = json.loads(REGISTRO_ROSTROS.read_text(encoding="utf-8")).get("personas", [])
    except (OSError, ValueError):
        personas = []
    if camara and personas:
        v = random.choice(personas)["vector"]
        ruido = [x + random.gauss(0, 0.04) for x in v]          # otra toma de la misma cara
        cliente_mqtt.publish(f"sma/camaras/{camara}", json.dumps({"camara": camara, "ubicacion": pedido.cruce,
                                                                  "embedding": ruido, "simulado": True}))
        log(f"persona solicitada simulada: la cámara {camara} ve a una persona registrada en {pedido.cruce}")
        return {"modo": "camara", "camara": camara}
    cliente_mqtt.publish(f"sma/eventos/{pedido.cruce}", json.dumps({"tipo": "persona_solicitada",
                                                                     "camara": "panel (simulado)"}))
    return {"modo": "evento", "motivo": "el cruce no tiene cámara" if not camara else "nadie registrado"}


class AccidenteReal(BaseModel):
    hora: int | None = None          # hora del día simulada: se prefieren accidentes reales a esa hora


@app.post("/api/accidente_real")
def api_accidente_real(pedido: AccidenteReal):
    """Reproduce un accidente REAL de Bocagrande (dataset DATT, limpio) en su tramo y posición reales."""
    filas = accidentes_reales()
    if not filas:
        raise HTTPException(404, "no hay datos: corra python modelos/datos_reales.py")
    if cliente_mqtt is None or not cliente_mqtt.is_connected():
        raise HTTPException(503, "sin conexión a Mosquitto")
    a_esa_hora = [f for f in filas if pedido.hora is not None and abs(int(f["hora_dia"]) - pedido.hora) <= 1]
    f = random.choice(a_esa_hora or filas)
    x, z = float(f["x"]), float(f["z"])
    cruce = f["cruce"] or min(cruces_3d(), key=lambda c: math.hypot(c["x"] - x, c["z"] - z))["id"]
    via = " x ".join(v for v in (f["via1"], f["via2"]) if v) or f["via_osm"]
    real = {"ipat": f["ipat"], "momento": f["momento"][:16], "gravedad": f["gravedad"].lower(),
            "clase": f["clase_accidente"].lower(), "via": via, "x": x, "z": z}
    cuerpo = {"tipo": "accidente", "arista": int(f["arista"]), "real": real}
    if cruce == "cruce5":
        cuerpo["sentido"] = "salida"
    cliente_mqtt.publish(f"sma/eventos/{cruce}", json.dumps(cuerpo, ensure_ascii=False))
    log(f"accidente REAL del DATT (IPAT {real['ipat']}, {real['momento']}, {via}) reproducido en {cruce}")
    return {"ok": True, "cruce": cruce, **real}


def cruces_3d():
    return json.loads(RUTA_MAPA_3D.read_text(encoding="utf-8"))["cruces"]


class Terminar(BaseModel):
    alerta: str | None = None
    cruce: str = "cruce1"


@app.post("/api/terminar")
def api_terminar(t: Terminar):
    """El operador da por terminada una contingencia antes de tiempo (lo recibe el Agente Vigilancia)."""
    if cliente_mqtt is None or not cliente_mqtt.is_connected():
        raise HTTPException(503, "sin conexión a Mosquitto")
    cliente_mqtt.publish(f"sma/eventos/{t.cruce}", json.dumps({"tipo": "fin_contingencia", "alerta": t.alerta}))
    log(f"el operador termina la contingencia {t.alerta or ''} en {t.cruce}")
    return {"ok": True}


def publicar_sensores(datos):
    """Colas por acceso que mide el gemelo digital -> sma/sensores/<cruce> (las usan Semafórico y Consultor)."""
    validos = {c["id"] for c in cruces()}
    if not isinstance(datos, dict) or cliente_mqtt is None or not cliente_mqtt.is_connected():
        return
    for cruce, v in datos.items():
        if cruce in validos and isinstance(v, dict):
            cliente_mqtt.publish(f"sma/sensores/{cruce}",
                                 json.dumps({"A": float(v.get("A", 0)), "B": float(v.get("B", 0))}))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clientes_ws.add(websocket)
    # lo que ya pasó: estados actuales + bitácora reciente + planes en curso (marcado como historial
    # para que la vista 3D no vuelva a disparar accidentes viejos; los planes sí se redibujan)
    for paquete in list(estados.values()) + list(mentes.values()) + list(bitacora) + list(planes.values()):
        await websocket.send_text(json.dumps({**paquete, "historial": True}, ensure_ascii=False))
    try:
        while True:
            texto = await websocket.receive_text()   # el navegador envía las lecturas de sus sensores de cola
            try:
                datos = json.loads(texto)
            except ValueError:
                continue
            if isinstance(datos, dict) and datos.get("tipo") == "sensores":
                publicar_sensores(datos.get("datos"))
    except WebSocketDisconnect:
        pass
    finally:
        clientes_ws.discard(websocket)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Panel web de la maqueta virtual")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--puerto", type=int, default=1883)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--puerto-web", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run(app, host=args.host, port=args.puerto_web, log_level="warning")
