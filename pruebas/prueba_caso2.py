"""Prueba punta a punta del Caso 2: persona solicitada -> cerco semafórico en la ruta de huida.

No usa la webcam ni fotos: registra una persona de PRUEBA con un vector sintético (se borra al terminar), publica
en sma/camaras/cam2 (cruce3) primero un rostro que NO coincide y luego "otra toma" de la persona registrada, y
verifica toda la cadena: Vigilancia -> Consultor (similitud) -> Clasificador (Seguridad, Crítica) -> Coordinador
-> Predictor (ruta de huida) -> Semafórico (cerco en todos los cruces de la ruta) -> Comunicador (solo Policía).
Hace de gemelo 3D: publica sensores de cola de los 5 cruces cada 2 s (sma/sensores/<cruce>), así el Consultor
tiene estado vial y el Coordinador puede medir el efecto del plan.

Uso:
    python pruebas/prueba_caso2.py            (con los agentes del SMA detenidos: la prueba levanta los suyos)
"""
from __future__ import annotations

import json
import sys
import threading
import time

import numpy as np
import paho.mqtt.client as mqtt

from prueba_punta_a_punta import (ENTORNO, GRUPO, RAIZ, esperar_linea, leer_en_segundo_plano, publicar,
                                  terminar)
import subprocess

REGISTRO = RAIZ / "vision" / "registro" / "registro.json"


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    rng = np.random.default_rng(7)
    persona = rng.normal(size=128); persona /= np.linalg.norm(persona)
    extrano = rng.normal(size=128); extrano /= np.linalg.norm(extrano)
    otra_toma = persona + rng.normal(0, 0.04, 128); otra_toma /= np.linalg.norm(otra_toma)

    memoria = RAIZ / "agentes" / "memoria"
    respaldos = {r: r.read_bytes() for r in (memoria / "experiencia_planes.json", memoria / "q_semaforico.json",
                                             REGISTRO) if r.exists()}
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO.write_text(json.dumps({"personas": [{"alias": "PRUEBA", "vector": persona.tolist(), "muestras": 1,
                                                  "consistencia": 1.0, "consentimiento": "prueba"}]}), encoding="utf-8")
    vistos = {"cerco": set(), "resultados": []}
    parar = threading.Event()

    def sensores():          # lo que mide el gemelo 3D: colas por acceso de cada cruce cada 2 s
        g = np.random.default_rng(3)
        while not parar.is_set():
            for i in range(1, 6):
                a, b = (int(x) for x in g.poisson((6, 3) if i in (3, 5) else (3, 2)))
                cliente.publish(f"sma/sensores/cruce{i}", json.dumps({"A": a, "B": b}))
            parar.wait(2)

    def al_llegar(_c, _u, msg):
        texto = msg.payload.decode(errors="ignore")
        if msg.topic.startswith("sma/semaforo/") and texto == "cerco":
            vistos["cerco"].add(msg.topic.split("/")[-1])
        elif msg.topic.endswith("/resultado"):
            vistos["resultados"].append(json.loads(texto)["texto"])

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-prueba-caso2")
    cliente.on_message = al_llegar
    agentes = None
    try:
        cliente.connect("127.0.0.1", 1883, keepalive=10)
        cliente.subscribe([("sma/semaforo/#", 0), ("sma/camaras/cam2/resultado", 0)])
        cliente.loop_start()
        threading.Thread(target=sensores, daemon=True).start()
        agentes = subprocess.Popen([sys.executable, "agentes/sma_agentes.py", "--duracion", "4", "--olvidar"], cwd=RAIZ,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1, env=ENTORNO, **GRUPO)
        leer_en_segundo_plano(agentes)
        esperar_linea(agentes, "Los 7 agentes del SMA están activos", 60)
        time.sleep(2)

        # 1) un rostro que no está registrado: se descarta, no hay alerta
        publicar(cliente, "sma/camaras/cam2", json.dumps({"camara": "cam2", "embedding": extrano.tolist()}))
        time.sleep(3)
        if not any("sin coincidencia" in r for r in vistos["resultados"]):
            raise AssertionError(f"el rostro desconocido no se descartó: {vistos['resultados']}")

        # 2) otra toma de la persona registrada: posible coincidencia -> cerco en la ruta de huida
        publicar(cliente, "sma/camaras/cam2", json.dumps({"camara": "cam2", "embedding": otra_toma.tolist()}))
        salida = esperar_linea(agentes, "plan activo", 60)
        salida += "\n" + esperar_linea(agentes, "cerrado", 40)
        if "Telegram desactivado" not in salida:      # el Comunicador redacta con el LLM: puede tardar
            salida += "\n" + esperar_linea(agentes, "Telegram desactivado", 90)
        esperados = ["posible coincidencia", "Seguridad, prioridad Critica", "rutas probables de huida",
                     "cerco en", "[para Policia]"]
        faltan = [e for e in esperados if e not in salida]
        if faltan:
            raise AssertionError("faltan en la bitácora: " + ", ".join(faltan))
        if "sin sensores" in salida or "sin datos de sensores" in salida:
            raise AssertionError("el Consultor no recibió los sensores de cola")
        if "[para Ciudadania]" in salida:
            raise AssertionError("una alerta de Seguridad no debe avisar a la ciudadanía")
        if len(vistos["cerco"]) < 2:
            raise AssertionError(f"el cerco debía cubrir la ruta de huida (varios cruces): {sorted(vistos['cerco'])}")
        print(f"\nPRUEBA CASO 2 OK: rostro desconocido descartado; posible coincidencia -> cerco en "
              f"{sorted(vistos['cerco'])} -> aviso solo a la Policía -> restauración")
        return 0
    except (AssertionError, RuntimeError, TimeoutError, OSError) as error:
        print(f"\nPRUEBA CASO 2 FALLÓ: {error}", file=sys.stderr)
        return 1
    finally:
        parar.set()
        cliente.loop_stop(); cliente.disconnect()
        terminar(agentes)
        for ruta, datos in respaldos.items():
            ruta.write_bytes(datos)
        if REGISTRO not in respaldos:
            REGISTRO.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
