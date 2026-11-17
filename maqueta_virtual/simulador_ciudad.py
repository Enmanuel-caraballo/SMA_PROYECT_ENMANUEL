"""
SMA Movilidad y Seguridad CTG - Simulador de los cruces virtuales
=================================================================
Simula los semáforos de los cruces de cruces.json con EL MISMO contrato MQTT
que el ESP32 (esp32/esp32_cruce/esp32_cruce.ino), así los agentes no distinguen
un cruce real de uno virtual:

  escucha   sma/semaforo/<cruce>   normal | corredor | cerco  (compat.: verde=corredor, rojo=normal)
  publica   sma/estado/<cruce>     JSON retenido {"modo":"normal","A":"verde","B":"rojo"}

Uso:
  python maqueta_virtual/simulador_ciudad.py                    (cruce2..cruce8; cruce1 lo maneja el ESP32)
  python maqueta_virtual/simulador_ciudad.py --incluir-cruce1   (también simula el cruce físico)
  python maqueta_virtual/simulador_ciudad.py --broker 192.168.1.7
"""

import argparse
import json
import queue
import random
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

RUTA_CRUCES = Path(__file__).resolve().parent / "cruces.json"

# Mismos tiempos y fases que el ESP32: (luz A, luz B, segundos)
CICLO = [
    ("verde", "rojo", 8.0),
    ("amarillo", "rojo", 2.0),
    ("rojo", "rojo", 1.0),
    ("rojo", "verde", 6.0),
    ("rojo", "amarillo", 2.0),
    ("rojo", "rojo", 1.0),
]
ORDENES = {"normal": "normal", "rojo": "normal",
           "corredor": "corredor", "verde": "corredor",
           "cerco": "cerco"}


def log(texto):
    print(f"[{datetime.now():%H:%M:%S}] {'Simulador':<13} | {texto}", flush=True)


class Cruce:
    """Réplica en Python de la máquina de estados del ESP32."""

    def __init__(self, id_cruce, cliente, desfase=0.0):
        self.id = id_cruce
        self.cliente = cliente
        self.modo = "normal"
        self.fase = 0
        self.inicio_fase = time.monotonic() - desfase   # desfase: que no cambien todos a la vez
        while time.monotonic() - self.inicio_fase >= CICLO[self.fase][2]:
            self.inicio_fase += CICLO[self.fase][2]
            self.fase = (self.fase + 1) % len(CICLO)
        self.luces = None            # (A, B) publicadas por última vez
        self.forzar_publicacion = True

    def cambiar_modo(self, modo):
        self.modo = modo
        self.fase = 0
        self.inicio_fase = time.monotonic()
        self.forzar_publicacion = True
        log(f"{self.id}: modo -> {modo}")

    def actualizar(self):
        if self.modo == "corredor":
            luces = ("verde", "rojo")
        elif self.modo == "cerco":
            luces = ("rojo", "rojo")
        else:
            if time.monotonic() - self.inicio_fase >= CICLO[self.fase][2]:
                self.fase = (self.fase + 1) % len(CICLO)
                self.inicio_fase = time.monotonic()
            luces = CICLO[self.fase][:2]
        if luces != self.luces or self.forzar_publicacion:
            self.luces = luces
            self.forzar_publicacion = False
            estado = {"modo": self.modo, "A": luces[0], "B": luces[1]}
            self.cliente.publish(f"sma/estado/{self.id}", json.dumps(estado), retain=True)


def main():
    p = argparse.ArgumentParser(description="Simulador de cruces virtuales de Cartagena")
    p.add_argument("--incluir-cruce1", action="store_true",
                   help="simula también cruce1 (el cruce físico) cuando el ESP32 no está conectado")
    p.add_argument("--broker", default="localhost", help="IP del broker Mosquitto (por defecto localhost)")
    p.add_argument("--puerto", type=int, default=1883)
    args = p.parse_args()

    datos = json.loads(RUTA_CRUCES.read_text(encoding="utf-8"))
    ids = [c["id"] for c in datos["cruces"] if args.incluir_cruce1 or not c.get("fisico")]

    ordenes = queue.Queue()   # los callbacks de paho corren en otro hilo

    def al_conectar(cliente, userdata, flags, codigo, propiedades):
        for i in ids:
            cliente.subscribe(f"sma/semaforo/{i}")
        log(f"conectado a Mosquitto {args.broker}:{args.puerto}; escuchando {len(ids)} cruces")

    def al_llegar(cliente, userdata, msg):
        ordenes.put((msg.topic.split("/")[-1], msg.payload.decode(errors="ignore").strip().lower()))

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sma-simulador-{random.randint(0, 9999)}")
    cliente.on_connect = al_conectar
    cliente.on_message = al_llegar
    try:
        cliente.connect(args.broker, args.puerto)
    except OSError:
        log(f"ERROR: no pude conectarme a Mosquitto en {args.broker}:{args.puerto}. ¿Está corriendo?")
        return
    cliente.loop_start()

    duracion_ciclo = sum(f[2] for f in CICLO)
    cruces = {i: Cruce(i, cliente, desfase=random.uniform(0, duracion_ciclo)) for i in ids}
    log(f"simulando: {', '.join(ids)}" + ("" if args.incluir_cruce1 else "  (cruce1 = ESP32 real)"))

    try:
        while True:
            while not ordenes.empty():
                id_cruce, texto = ordenes.get()
                modo = ORDENES.get(texto)
                if modo and id_cruce in cruces:
                    log(f"orden recibida para {id_cruce}: '{texto}'")
                    cruces[id_cruce].cambiar_modo(modo)
                elif id_cruce in cruces:
                    log(f"orden desconocida para {id_cruce}: '{texto}'")
            for c in cruces.values():
                c.actualizar()
            time.sleep(0.1)
    except KeyboardInterrupt:
        log("detenido")
    finally:
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()
