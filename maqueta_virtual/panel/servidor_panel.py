"""
SMA Movilidad y Seguridad CTG - Servidor del panel web (maqueta virtual)
========================================================================
Se suscribe a sma/# en Mosquitto y reenvía cada mensaje al navegador por WebSocket.
El navegador muestra el mapa de Cartagena (Leaflet + OpenStreetMap), los semáforos,
la bitácora de agentes y las alertas.

Uso:   python maqueta_virtual/panel/servidor_panel.py
       abrir http://localhost:8000
Opciones: --broker 192.168.1.7   --puerto-web 8000   --host 0.0.0.0 (para verlo desde otro equipo)
"""

import argparse
import asyncio
import json
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

CARPETA = Path(__file__).resolve().parent
RUTA_CRUCES = CARPETA.parent / "cruces.json"
TIPOS_EVENTO = {"accidente", "congestion", "aglomeracion", "placa_reportada", "persona_solicitada"}

args = None
clientes_ws: set[WebSocket] = set()
estados = {}                    # último sma/estado/<cruce>
bitacora = deque(maxlen=300)    # últimos mensajes para quien abra la página tarde
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
        paquete = {"tema": msg.topic, "datos": datos, "hora": f"{datetime.now():%H:%M:%S}"}
        partes = msg.topic.split("/")
        if len(partes) == 3 and partes[1] == "estado":
            estados[partes[2]] = paquete
        else:
            bitacora.append(paquete)
        # los callbacks de paho corren en otro hilo -> pasar al bucle de asyncio
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(difundir(paquete)))
    return manejar


@asynccontextmanager
async def ciclo_de_vida(app):
    global cliente_mqtt
    loop = asyncio.get_running_loop()
    cliente_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-panel")
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


@app.get("/")
def pagina():
    return FileResponse(CARPETA / "index.html")


@app.get("/api/cruces")
def api_cruces():
    return JSONResponse(cruces())


class Evento(BaseModel):
    cruce: str
    tipo: str = "accidente"


@app.post("/api/evento")
def api_evento(evento: Evento):
    """Simula un evento en un cruce: publica en sma/eventos/<cruce> como lo haría el ESP32."""
    if evento.cruce not in {c["id"] for c in cruces()}:
        raise HTTPException(404, "cruce desconocido")
    if evento.tipo not in TIPOS_EVENTO:
        raise HTTPException(400, "tipo de evento desconocido")
    if cliente_mqtt is None or not cliente_mqtt.is_connected():
        raise HTTPException(503, "sin conexión a Mosquitto")
    cuerpo = "accidente" if evento.tipo == "accidente" else json.dumps({"tipo": evento.tipo})
    cliente_mqtt.publish(f"sma/eventos/{evento.cruce}", cuerpo)
    log(f"evento simulado desde el panel: {evento.tipo} en {evento.cruce}")
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clientes_ws.add(websocket)
    # lo que ya pasó: estados actuales + bitácora reciente
    for paquete in list(estados.values()) + list(bitacora):
        await websocket.send_text(json.dumps(paquete, ensure_ascii=False))
    try:
        while True:
            await websocket.receive_text()   # el navegador no envía nada; solo mantiene viva la conexión
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
