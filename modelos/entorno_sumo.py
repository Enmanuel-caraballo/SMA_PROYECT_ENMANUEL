"""
SMA Movilidad y Seguridad CTG - ENTORNO DE SIMULACIÓN SUMO PARA ENTRENAR Y COMPARAR CONTROLADORES
==================================================================================================
Bocagrande en SUMO (modelos/sumo/bocagrande.net.xml) con:
  - Demanda REAL en su forma horaria: perfil del SIMM (modelos/datos/perfil_demanda.json) aplicado a los mismos
    orígenes-destinos del modelo de rutas (zonas, entrada desde el Centro, entrada norte, salida por el cruce5).
  - Accidentes REALES: tramo sorteado con la frecuencia observada en el dataset DATT de Bocagrande
    (modelos/datos/accidentes_resumen.json); vehículos detenidos CIERRAN todos los carriles del tramo durante el episodio
    (como el gemelo 3D y el modelo de rutas) y los demás recalculan su ruta para evitarlo.
  - Los 5 semáforos con los MISMOS modos que el ESP32 y el simulador (normal A8/B6, prioridad_a A20/B5,
    prioridad_b A5/B20; 2 s de amarillo y 1 s de todo rojo), conservando la fase al cambiar de modo.
  - Sensores iguales a los del gemelo 3D: cola por acceso = vehículos a < 2 m/s a menos de 100 m de la línea de pare.

Cada INTERVALO (12 s, como el Agente Semafórico) el controlador elige el modo de cada cruce.

Uso:
    python modelos/entorno_sumo.py --recolectar 40   (episodios con controladores mixtos -> datos para la T-GCN)
"""

import argparse
import json
import math
import random
import sys
import tempfile
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "agentes"))
import conocimiento as K  # noqa: E402

CARPETA = RAIZ / "modelos" / "sumo"
DATOS = RAIZ / "modelos" / "datos"
CRUCES = ["cruce1", "cruce2", "cruce3", "cruce4", "cruce5"]
MODOS = ["normal", "prioridad_a", "prioridad_b"]
TIEMPOS = {"normal": (8, 6), "prioridad_a": (20, 5), "prioridad_b": (5, 20)}
AMARILLO, TODO_ROJO = 2, 1
INTERVALO = K.INTERVALO_CONTROL
DIST_SENSOR, VEL_COLA = 100.0, 2.0


def fases(modo):
    ga, gb = TIEMPOS[modo]
    return [("A", "verde", ga), ("A", "amarillo", AMARILLO), ("-", "rojo", TODO_ROJO),
            ("B", "verde", gb), ("B", "amarillo", AMARILLO), ("-", "rojo", TODO_ROJO)]


def vecinos():
    """Cruces conectados por la red (para el estado del controlador): por distancia en el mapa."""
    mapa = json.loads((CARPETA / "mapa.json").read_text(encoding="utf-8"))
    pos = {c: (d["x"], d["z"]) for c, d in mapa["cruces"].items()}
    return {c: sorted((o for o in CRUCES if o != c), key=lambda o: math.dist(pos[c], pos[o]))[:2] for c in CRUCES}


VECINOS = None


class EntornoBocagrande:
    def __init__(self, semilla=0, hora=17, accidente=None, duracion=3600, escala=1.0, gui=False):
        """accidente: None | "real" (sorteado del dataset) | {"cruce": "cruce5", "sentido": "salida"} | {"base": i}"""
        global VECINOS
        self.semilla, self.hora, self.duracion, self.escala, self.gui = semilla, hora, duracion, escala, gui
        self.accidente_pedido = accidente
        self.mapa = json.loads((CARPETA / "mapa.json").read_text(encoding="utf-8"))
        self.perfil = json.loads((DATOS / "perfil_demanda.json").read_text(encoding="utf-8"))
        VECINOS = VECINOS or vecinos()
        self.rng = random.Random(semilla)

    # ---------------------------------------------------------------- demanda y arranque
    def _rutas(self):
        from rutas import red
        R = red()
        g = self.mapa["geo"]
        arista = {a["id"]: a for a in self.mapa["aristas"]}
        sale = {}
        llega = {}
        for a in self.mapa["aristas"]:
            if not a["reserva"]:
                sale.setdefault(a["desde"], []).append(a)
                llega.setdefault(a["hasta"], []).append(a)
        desde_nodo = lambda n: max(sale[n], key=lambda a: a["largo"])["id"]
        hasta_nodo = lambda n: max(llega[n], key=lambda a: a["largo"])["id"]
        # la salida al Centro: tramo que llega al nodo de salida del cruce5
        salida = max(llega[g["nodoSalida"]], key=lambda a: a["largo"])["id"]
        origen_centro = f"e{g['entradaCentro']}"
        origen_norte = f"e{g['entradaNorte']}"
        factor = self.perfil["habil"][int(self.hora) % 24] * self.escala
        pico = (R.demanda_pico or K.DEMANDA_PICO_VEH_H) * factor * K.SUMO_FRACCION_DEMANDA
        rep = K.REPARTO_DEMANDA
        nz = len(self.mapa["zonas"])
        flujos = []
        for z in self.mapa["zonas"]:
            flujos.append((desde_nodo(z), salida, pico * rep["salir"] / nz))
            flujos.append((origen_centro, hasta_nodo(z), pico * rep["entrar_centro"] / nz))
            flujos.append((origen_norte, hasta_nodo(z), pico * rep["entrar_norte"] / nz))
            for w in self.mapa["zonas"]:
                if w != z:
                    flujos.append((desde_nodo(z), hasta_nodo(w), pico * rep["interna"] / (nz * (nz - 1))))
        lineas = ['<routes>', '  <vType id="carro" accel="2.6" decel="4.5" sigma="0.5" length="4.5" minGap="2.0" maxSpeed="16"/>']
        for i, (o, d, q) in enumerate(flujos):
            if o != d and q > 0.5:
                lineas.append(f'  <flow id="f{i}" type="carro" from="{o}" to="{d}" begin="0" end="{self.duracion}" '
                              f'vehsPerHour="{q:.1f}" departLane="best" departSpeed="max"/>')
        lineas.append('</routes>')
        ruta = Path(tempfile.gettempdir()) / f"sma_bocagrande_{self.semilla}_{random.random():.6f}.rou.xml"
        ruta.write_text("\n".join(lineas), encoding="utf-8")
        return ruta, arista

    def reset(self):
        import libsumo as traci
        self.traci = traci
        ruta, self.arista = self._rutas()
        try:
            traci.close()
        except Exception:
            pass
        traci.start(["sumo", "-n", str(CARPETA / "bocagrande.net.xml"), "-r", str(ruta), "--seed", str(self.semilla),
                     "--no-step-log", "true", "--no-warnings", "true", "--time-to-teleport", "240",
                     "--device.rerouting.probability", "1", "--device.rerouting.period", "60",
                     "--device.rerouting.adaptation-steps", "18", "--ignore-route-errors", "true",
                     "--step-length", "1"])
        # tramos de reserva del contraflujo: cerrados
        for a in self.mapa["aristas"]:
            if a["reserva"]:
                for k in range(a["carriles"]):
                    traci.lane.setAllowed(f"{a['id']}_{k}", [])
        # enlaces de cada semáforo -> grupo A / B según la vía de entrada
        control = {a["id"]: tuple(a["control"]) if a["control"] else None for a in self.mapa["aristas"]}
        self.grupos = {}
        self.accesos = {c: {"A": [], "B": []} for c in CRUCES}
        for a in self.mapa["aristas"]:
            if a["control"]:
                self.accesos[a["control"][0]][a["control"][1]].append(a["id"])
        for c in CRUCES:
            enlaces = traci.trafficlight.getControlledLinks(c)
            grupo = []
            for enlace in enlaces:
                if not enlace:
                    grupo.append("-")
                    continue
                arista = enlace[0][0].rsplit("_", 1)[0]
                ctl = control.get(arista)
                grupo.append(ctl[1] if ctl and ctl[0] == c else "*")   # "*" = interno del cruce: siempre permitido
            self.grupos[c] = grupo
        self.modo = {c: "normal" for c in CRUCES}
        self.fase = {c: [0, 0.0] for c in CRUCES}                 # índice de fase, tiempo en la fase
        for c in CRUCES:
            self.fase[c][0] = self.rng.randrange(6)               # desfase entre cruces
            self._luces(c)
        self.t = 0
        self.esperas = 0.0
        self.llegados = 0
        self.salidas = {}
        self.viajes = []
        self.accidente = None
        self._preparar_accidente()
        self.historia = []
        self.colas_prev = {c: (0.0, 0.0) for c in CRUCES}
        self._avanzar(30)                                         # que entren los primeros carros
        return self.observar()

    def _preparar_accidente(self):
        pedido = self.accidente_pedido
        if not pedido:
            return
        g = self.mapa["geo"]
        if pedido == "real":
            resumen = json.loads((DATOS / "accidentes_resumen.json").read_text(encoding="utf-8"))
            bases = [int(b) for b in resumen["por_arista"]]
            pesos = list(resumen["por_arista"].values())
            for _ in range(20):
                base = self.rng.choices(bases, pesos)[0]
                cand = [a for a in self.mapa["aristas"] if a["base"] == base and not a["reserva"] and a["largo"] > 40]
                if cand:
                    a = self.rng.choice(cand)
                    pos = self.rng.uniform(15, a["largo"] - 15)
                    break
            else:
                return
        elif pedido.get("cruce") == "cruce5":
            a = self.arista[f"e{g['accEntrada'] if pedido.get('sentido') == 'entrada' else g['accSalida']}"]
            pos = min(100, a["largo"] - 20) if pedido.get("sentido") == "entrada" else max(10, a["largo"] - 100)
        else:
            return
        self.accidente = {"arista": a["id"], "pos": round(pos, 1), "base": a["base"], "nombre": a["nombre"],
                          "inicio": self.rng.uniform(120, 600)}

    def _insertar_accidente(self):
        a = self.accidente
        tr = self.traci
        try:
            tr.route.add("r_acc", [a["arista"]])
            for k in range(tr.edge.getLaneNumber(a["arista"])):      # el accidente cierra TODOS los carriles
                v = f"accidente_{k}"
                tr.vehicle.add(v, "r_acc", typeID="carro", departPos=str(a["pos"]), departLane=str(k), departSpeed="0")
                tr.vehicle.setStop(v, a["arista"], pos=min(a["pos"] + 1, self.arista[a["arista"]]["largo"] - 1),
                                   laneIndex=k, duration=self.duracion)
                tr.vehicle.setColor(v, (255, 80, 0, 255))
            tr.edge.adaptTraveltime(a["arista"], 1e6)               # el ruteo (device.rerouting) lo evita
            for v in tr.vehicle.getIDList():                         # los que ya iban hacia allá recalculan su ruta
                if not v.startswith("accidente") and a["arista"] in tr.vehicle.getRoute(v)[tr.vehicle.getRouteIndex(v) + 1:]:
                    tr.vehicle.rerouteTraveltime(v, currentTravelTimes=False)
            a["activo"] = True
        except Exception as error:
            a["error"] = str(error)

    # ---------------------------------------------------------------- semáforos
    def _luces(self, c):
        i, _ = self.fase[c]
        grupo_fase, color, _ = fases(self.modo[c])[i]
        letra = {"verde": "g", "amarillo": "y", "rojo": "r"}[color]
        estado = "".join("g" if g == "*" else (letra if g == grupo_fase else "r") if g in "AB" else "r"
                         for g in self.grupos[c])
        self.traci.trafficlight.setRedYellowGreenState(c, estado)

    def _avanzar(self, segundos):
        tr = self.traci
        for _ in range(int(segundos)):
            for c in CRUCES:
                f = self.fase[c]
                f[1] += 1
                if f[1] >= fases(self.modo[c])[f[0]][2]:
                    f[0], f[1] = (f[0] + 1) % 6, 0.0
                    self._luces(c)
            if self.accidente and not self.accidente.get("activo") and self.t >= self.accidente["inicio"]:
                self._insertar_accidente()
            tr.simulationStep()
            self.t += 1
            for v in tr.simulation.getDepartedIDList():
                self.salidas[v] = self.t
            for v in tr.simulation.getArrivedIDList():
                if v in self.salidas:
                    self.viajes.append(self.t - self.salidas.pop(v))
            self.llegados += tr.simulation.getArrivedNumber()
            if self.t % 5 == 0:   # demora: vehículos detenidos (veh·s), muestreado cada 5 s
                self.esperas += 5 * sum(tr.edge.getLastStepHaltingNumber(e) for e in self._todas())

    def _todas(self):
        if not hasattr(self, "_aristas_reales"):
            self._aristas_reales = [a["id"] for a in self.mapa["aristas"] if not a["reserva"]]
        return self._aristas_reales

    # ---------------------------------------------------------------- sensores y estado
    def colas(self):
        tr = self.traci
        salida = {}
        for c in CRUCES:
            q = {}
            for g in "AB":
                n = 0
                for e in self.accesos[c][g]:
                    largo = self.arista[e]["largo"]
                    for v in tr.edge.getLastStepVehicleIDs(e):
                        if not v.startswith("accidente") and tr.vehicle.getSpeed(v) < VEL_COLA and largo - tr.vehicle.getLanePosition(v) < DIST_SENSOR:
                            n += 1
                q[g] = n
            salida[c] = (q["A"], q["B"])
        return salida

    def observar(self):
        q = self.colas()
        obs = {"colas": q, "modo": dict(self.modo), "hora": self.hora + self.t / 3600, "t": self.t}
        self.colas_prev = q
        return obs

    def step(self, acciones):
        """acciones: {cruce: modo}. Avanza INTERVALO s. Devuelve (obs, recompensas, terminado, info)."""
        cambios = {}
        for c, m in acciones.items():
            if m in TIEMPOS:
                cambios[c] = m != self.modo[c]
                self.modo[c] = m                    # conserva la fase: solo cambian los tiempos
                f = self.fase[c]
                f[1] = min(f[1], fases(m)[f[0]][2] - 1)
        antes = self.colas_prev
        sub = INTERVALO // 3
        muestras = []
        for _ in range(3):                          # el agente promedia las lecturas del intervalo
            self._avanzar(sub)
            muestras.append(self.colas())
        q = {c: tuple(float(np.mean([m[c][k] for m in muestras])) for k in range(2)) for c in CRUCES}
        self.colas_prev = q
        recompensas = {c: recompensa(q, c, cambios.get(c, False)) for c in CRUCES}
        terminado = self.t >= self.duracion or self.traci.simulation.getMinExpectedNumber() == 0
        obs = {"colas": q, "modo": dict(self.modo), "hora": self.hora + self.t / 3600, "t": self.t, "antes": antes}
        return obs, recompensas, terminado, {}

    def metricas(self):
        return {"llegados": self.llegados, "demora_veh_h": round(self.esperas / 3600, 1),
                "viaje_medio_s": round(float(np.mean(self.viajes)), 1) if self.viajes else None,
                "teleports": self.traci.simulation.getStartingTeleportNumber() if hasattr(self, "traci") else 0,
                "accidente": self.accidente}

    def cerrar(self):
        try:
            self.traci.close()
        except Exception:
            pass


def recompensa(q, c, cambio):
    """Recompensa del controlador de un cruce: menos cola propia (y algo de la de sus vecinos), sin cambiar a cada rato."""
    propia = sum(q[c])
    vec = sum(sum(q[o]) for o in VECINOS[c]) / max(1, len(VECINOS[c]))
    return -(propia + K.DQN_PESO_VECINOS * vec) / 10.0 - (K.PENALIZACION_CAMBIO * 0.4 if cambio else 0.0)


# ---------------------------------------------------------------- controladores de referencia
def control_fijo(obs):
    return {c: "normal" for c in CRUCES}


def control_max_presion(obs, umbral=3):
    """Max-pressure (Varaiya 2013), versión por grupos: da más verde al acceso con más cola si la diferencia es clara."""
    acc = {}
    for c, (a, b) in obs["colas"].items():
        acc[c] = "prioridad_a" if a - b > umbral else "prioridad_b" if b - a > umbral else "normal"
    return acc


def control_tabla_q():
    from aprendizaje import QSemaforo
    q = QSemaforo()
    q.epsilon = 0.0

    def decidir(obs):
        return {c: q.elegir(c, q.estado(*obs["colas"][c]), None, obs["modo"][c])[0] for c in CRUCES}
    return decidir


def escenario_aleatorio(rng):
    """Hora con su demanda real, accidente (ninguno / real del DATT / cruce5) y variación de demanda."""
    hora = rng.choice([7, 8, 9, 11, 12, 13, 15, 16, 17, 18, 19, 21])
    acc = rng.choice([None, None, "real", "real", {"cruce": "cruce5", "sentido": rng.choice(["salida", "entrada"])}])
    return hora, acc, rng.uniform(0.85, 1.3)


def recolectar(episodios, semilla0=1000):
    """Serie de colas por acceso cada INTERVALO (12 s, el mismo paso que ve el agente) para entrenar la T-GCN."""
    import pandas as pd
    filas = []
    controles = [("fijo", control_fijo), ("max_presion", control_max_presion), ("tabla_q", control_tabla_q())]
    for ep in range(episodios):
        rng = random.Random(semilla0 + ep)
        hora, acc, escala = escenario_aleatorio(rng)
        nombre, control = controles[ep % len(controles)]
        env = EntornoBocagrande(semilla=semilla0 + ep, hora=hora, accidente=acc, duracion=2400, escala=escala)
        obs = env.reset()
        terminado = False
        while not terminado:
            acciones = control(obs) if rng.random() > 0.2 else {c: rng.choice(MODOS) for c in CRUCES}
            obs, _, terminado, _ = env.step(acciones)
            filas.append({"episodio": ep, "hora_sim": obs["hora"],
                          **{f"{c}_{g}": round(obs["colas"][c][k], 2) for c in CRUCES for k, g in enumerate("AB")}})
        m = env.metricas()
        env.cerrar()
        print(f"episodio {ep + 1}/{episodios}: {nombre}, hora {hora}, accidente {acc}, llegados {m['llegados']}, "
              f"demora {m['demora_veh_h']} veh·h", flush=True)
    pd.DataFrame(filas).to_csv(DATOS / "bocagrande_colas.csv.gz", index=False)
    print(f"guardado {len(filas)} lecturas en {DATOS / 'bocagrande_colas.csv.gz'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--recolectar", type=int, default=0, help="episodios para la serie de la T-GCN")
    p.add_argument("--probar", action="store_true", help="un episodio corto con control fijo")
    args = p.parse_args()
    if args.probar:
        import time
        t = time.time()
        env = EntornoBocagrande(semilla=1, hora=17, accidente={"cruce": "cruce5", "sentido": "salida"}, duracion=900)
        obs = env.reset()
        fin = False
        while not fin:
            obs, r, fin, _ = env.step(control_fijo(obs))
        print(obs["colas"], env.metricas(), f"{time.time() - t:.1f} s")
        env.cerrar()
    if args.recolectar:
        recolectar(args.recolectar)
