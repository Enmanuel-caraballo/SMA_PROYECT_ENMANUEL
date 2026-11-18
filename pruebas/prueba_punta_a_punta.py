"""Prueba punta a punta del Caso 1: accidente -> orden semafórica.

Usa un broker Mosquitto existente o levanta uno temporal, inicia los 7 agentes,
publica una lectura de sensores y un accidente, y verifica el recorrido completo
hasta restaurar el semáforo a ``normal``.

Uso:
    python pruebas/prueba_punta_a_punta.py
    python pruebas/prueba_punta_a_punta.py --duracion 5

Requisitos: Mosquitto disponible en PATH y ``pip install -r requirements.txt``.
"""
from __future__ import annotations

import argparse
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

RAIZ = Path(__file__).resolve().parents[1]


# En Windows: grupo de procesos propio (para poder enviarle CTRL_BREAK sin afectar a la prueba)
GRUPO = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
ENTORNO = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # tildes iguales en Windows y Linux
_salidas: dict[int, queue.Queue] = {}


def leer_en_segundo_plano(proceso: subprocess.Popen[str]) -> None:
    """readline() bloquea: se lee en un hilo y se pasa por una cola para que el tiempo límite sí funcione."""
    cola: queue.Queue = queue.Queue()
    _salidas[proceso.pid] = cola

    def leer():
        for linea in proceso.stdout:
            cola.put(linea)
        cola.put(None)
    threading.Thread(target=leer, daemon=True).start()


def esperar_linea(proceso: subprocess.Popen[str], texto: str, timeout: float) -> str:
    """Lee la salida del proceso hasta encontrar ``texto`` o agotar el tiempo."""
    limite = time.monotonic() + timeout
    lineas: list[str] = []
    cola = _salidas[proceso.pid]
    while time.monotonic() < limite:
        try:
            linea = cola.get(timeout=0.5)
        except queue.Empty:
            continue
        if linea is None:
            break
        lineas.append(linea.rstrip())
        print(lineas[-1], flush=True)
        if texto in linea:
            return "\n".join(lineas)
    raise TimeoutError(f"No apareció '{texto}' en {timeout:.0f} s")


def terminar(proceso: subprocess.Popen[str] | None) -> None:
    if proceso is None or proceso.poll() is not None:
        return
    if os.name == "nt":
        proceso.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        # SPADE crea auxiliares para el servidor XMPP; termina el grupo
        # completo para no dejar puertos ocupados tras la prueba.
        os.killpg(os.getpgid(proceso.pid), signal.SIGTERM)
    try:
        proceso.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proceso.kill()
        proceso.wait(timeout=5)


def publicar(cliente: mqtt.Client, tema: str, contenido: str) -> None:
    info = cliente.publish(tema, contenido)
    info.wait_for_publish(timeout=5)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"No se pudo publicar en {tema}: rc={info.rc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duracion", type=float, default=3,
                        help="duración del plan en segundos (por defecto: 3)")
    parser.add_argument("--timeout", type=float, default=60,
                        help="tiempo máximo de espera de cada etapa")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")      # la consola de Windows (cp1252) no imprime algunos símbolos

    broker = agentes = None
    broker_propio = False
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-prueba-e2e")
    memoria = RAIZ / "agentes" / "memoria"
    archivos_memoria = (memoria / "experiencia_planes.json", memoria / "q_semaforico.json")
    respaldos_memoria = {ruta: ruta.read_bytes() for ruta in archivos_memoria if ruta.exists()}
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8") as archivo:
        archivo.write("listener 1883 127.0.0.1\nallow_anonymous true\n")
        configuracion = archivo.name

    try:
        # Si el equipo ya tiene Mosquitto corriendo, se reutiliza. Esto permite
        # ejecutar la prueba junto a la demo normal sin competir por el puerto.
        try:
            cliente.connect("127.0.0.1", 1883, keepalive=10)
            cliente.loop_start()              # sin el bucle de red el broker corta la conexión por keepalive
        except OSError:
            broker = subprocess.Popen(
                ["mosquitto", "-c", configuracion],
                cwd=RAIZ,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **GRUPO,
            )
            broker_propio = True
            time.sleep(1)
            if broker.poll() is not None:
                raise RuntimeError("Mosquitto no pudo iniciar en localhost:1883")
            cliente.connect("127.0.0.1", 1883, keepalive=10)
            cliente.loop_start()

        agentes = subprocess.Popen(
            [sys.executable, "agentes/sma_agentes.py", "--duracion", str(args.duracion), "--olvidar"],
            cwd=RAIZ,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=ENTORNO,
            **GRUPO,
        )
        leer_en_segundo_plano(agentes)
        esperar_linea(agentes, "Los 7 agentes del SMA están activos", args.timeout)

        publicar(cliente, "sma/sensores/cruce1", '{"A":8,"B":2}')
        publicar(cliente, "sma/eventos/cruce1", "accidente")

        salida = esperar_linea(agentes, "plan activo", args.timeout)
        salida += "\n" + esperar_linea(agentes, "cerrado", max(args.timeout, args.duracion + 15))
        esperados = (
            "Vigilancia",
            "Clasificador",
            "Coordinador",
            "Consultor",
            "Semaforico",
            "Comunicador",
            "prioridad_a",
            "restaurarPlanNormal",
            "normal",
        )
        faltantes = [texto for texto in esperados if texto not in salida]
        if faltantes:
            raise AssertionError("Faltan evidencias en la bitácora: " + ", ".join(faltantes))
        print("\nPRUEBA 2.7 OK: accidente -> agentes -> prioridad_a -> restauración normal")
        return 0
    except (AssertionError, RuntimeError, TimeoutError, OSError) as error:
        print(f"\nPRUEBA 2.7 FALLÓ: {error}", file=sys.stderr)
        return 1
    finally:
        cliente.loop_stop()
        cliente.disconnect()
        terminar(agentes)
        if broker_propio:
            terminar(broker)
        # La prueba inicia con --olvidar, pero el proceso guarda su estado al
        # salir. Restauramos la memoria para que una prueba no cambie el
        # aprendizaje compartido ni ensucie el commit.
        for ruta in archivos_memoria:
            if ruta in respaldos_memoria:
                ruta.write_bytes(respaldos_memoria[ruta])
            elif ruta.exists():
                ruta.unlink()
        try:
            Path(configuracion).unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
