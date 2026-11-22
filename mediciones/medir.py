"""
SMA Movilidad y Seguridad CTG - MEDICIONES Y RESULTADOS
=======================================================
Experimentos reproducibles (semillas fijas) cuyos resultados van a docs/resultados/ (CSV, JSON, PNG y resultados.md).

  python mediciones/medir.py caso1        Caso 1 en SUMO: accidentes REALES del DATT, semáforos fijos vs plan del SMA
  python mediciones/medir.py caso2        Caso 2 en SUMO: cuánto retrasa la retención semafórica al solicitado
                                          (cámaras cam1/cam2/cam3) y cuánto le cuesta al resto del tráfico
  python mediciones/medir.py comunicador  avisos reales redactados por el LLM: % que pasa el control y tiempo
  python mediciones/medir.py latencia     tiempo de respuesta de la cadena de agentes (con los agentes DETENIDOS:
                                          levanta los suyos, como las pruebas)
  python mediciones/medir.py informe      junta todo (y las métricas de la T-GCN y la DQN) en resultados.md
  python mediciones/medir.py todo         caso1 + caso2 + comunicador + informe
  python mediciones/medir.py restaurar    si la latencia se cortó: devuelve la memoria y el registro de rostros

El plan de cada caso se calcula con las MISMAS funciones que usan los agentes (generar_planes, evaluar_con_modelo,
ruta_huida, retencion_semaforos), así lo medido es lo que el SMA haría.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "resultados"
sys.path.insert(0, str(RAIZ / "agentes"))
sys.path.insert(0, str(RAIZ / "modelos"))
os.environ.setdefault("SMA_SIN_TELEGRAM", "1")         # las mediciones nunca escriben a los grupos reales

import conocimiento as K  # noqa: E402


def guardar(nombre, filas):
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / f"{nombre}.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    if filas and isinstance(filas, list):
        cols = list(dict.fromkeys(k for f in filas for k in f if not isinstance(f[k], (dict, list))))
        lineas = [";".join(cols)] + [";".join(str(f.get(c, "")) for c in cols) for f in filas]
        (SALIDA / f"{nombre}.csv").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def media_ic(xs):
    """media e intervalo de confianza del 95 % (t de Student)."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, None
    if len(xs) == 1:
        return xs[0], 0.0
    t = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26}.get(len(xs), 2.0)
    return st.mean(xs), t * st.stdev(xs) / math.sqrt(len(xs))


# ====================================================================== utilidades de SUMO
def aplicar(env, modos):
    """Igual que EntornoBocagrande.step: cambia los tiempos conservando la fase (cerco = todo rojo)."""
    from entorno_sumo import fases
    for c in env.modo:
        m = modos.get(c, "normal")
        if m != env.modo[c]:
            env.modo[c] = m
            f = env.fase[c]
            f[1] = min(f[1], fases(m)[f[0]][2] - 1)
            env._luces(c)


def cruce_mas_cercano(base):
    b = json.loads((RAIZ / "maqueta_virtual" / "bocagrande" / "bocagrande.json").read_text(encoding="utf-8"))
    m = json.loads((RAIZ / "modelos" / "sumo" / "mapa.json").read_text(encoding="utf-8"))
    p = b["aristas"][base]["p"]
    x, z = p[len(p) // 2]
    return min(m["cruces"], key=lambda c: math.hypot(m["cruces"][c]["x"] - x, m["cruces"][c]["z"] - z))


def plan_del_sma(alerta, cruce, nivel="medio"):
    """Lo que haría el Coordinador: planes del catálogo + modelo matemático + utilidad (sin experiencia previa)."""
    import sma_agentes as S
    from aprendizaje import ExperienciaPlanes
    planes = S.generar_planes(alerta, cruce, ExperienciaPlanes(olvidar=True))
    modelo = S.evaluar_con_modelo(alerta, cruce, planes, nivel)
    factor = K.FACTOR_TRAFICO.get(nivel, K.FACTOR_TRAFICO_DESCONOCIDO)
    for p in planes:
        base = K.PESO_MODELO * p["beneficio"] + (1 - K.PESO_MODELO) * p["impacto"] if modelo else p["impacto"]
        p["utilidad"] = round(base * factor - K.PESO_COSTO * p["costo"], 3)
    return max(planes, key=lambda p: p["utilidad"]), planes


# ====================================================================== Caso 1
def controlador_sma(sugerido):
    """Semafórico del agente en SUMO: DQN + T-GCN cada 12 s con su capa de seguridad (bono al modo que sugiere el plan,
    cruce vacío, histéresis DQN_MARGEN). `sugerido()` devuelve los modos del plan vigente ({} si no hay plan)."""
    import torch
    import entorno_sumo as E
    from dqn import MODOS, cargar_politica
    from entrenar_dqn import RUTA_DQN, estados
    from predictor_colas import PredictorColas
    red, _ = cargar_politica(RUTA_DQN)
    predictor = PredictorColas()
    memoria = {"previas": None}

    def decidir(obs):
        predictor.agregar(obs["colas"], obs["hora"])
        s = estados(obs, memoria["previas"] or obs["colas"], predictor.predecir())
        memoria["previas"] = obs["colas"]
        plan = sugerido()
        acciones = {}
        for c in E.CRUCES:
            with torch.no_grad():
                q = red(torch.as_tensor(s[c]).unsqueeze(0))[0].numpy()
            sug = plan.get(c)
            punt = {m: float(v) + (K.DQN_BONO_PLAN if m == sug else 0.0) for m, v in zip(MODOS, q)}
            a, b = obs["colas"][c]
            antes = obs["modo"][c]
            if a < 0.5 and b < 0.5:
                acciones[c] = sug or "normal"
                continue
            mejor = max(punt, key=punt.get)
            cambio_menor = antes in punt and mejor != antes and punt[mejor] - punt[antes] < K.DQN_MARGEN
            acciones[c] = antes if cambio_menor else mejor
        return acciones
    return decidir


POLITICAS_C1 = {"fijo": "Semáforos fijos", "plan": "Solo el plan del Coordinador (modos fijos 5 min)",
                "dqn": "Solo la DQN (sin plan)", "sma": "SMA completo (DQN + plan del Coordinador)"}


def caso1(semillas=10, hora=17, duracion=1800):
    """Accidente real del DATT (tramo sorteado con su frecuencia real) en hora pico. Misma semilla = mismo tráfico y
    mismo accidente para las dos políticas. 'fijo' = semáforos de tiempo fijo; 'sma' = el plan que elige el
    Coordinador, aplicado desde que ocurre el accidente durante SEGUNDOS_PLAN."""
    from entorno_sumo import EntornoBocagrande
    filas = []
    for s in range(semillas):
        plan_info = None
        for politica in POLITICAS_C1:
            env = EntornoBocagrande(semilla=500 + s, hora=hora, accidente="real", duracion=duracion)
            obs = env.reset()
            acc = env.accidente
            if plan_info is None and acc:
                cruce = cruce_mas_cercano(acc["base"])
                alerta = {"id": f"M-{s}", "tipo": "Movilidad", "prioridad": "Alta", "tipo_evento": "accidente",
                          "ubicacion": cruce, "arista": acc["base"]}
                elegido, planes = plan_del_sma(alerta, cruce)
                if elegido["clave"].startswith("contraflujo"):   # SUMO no invierte carriles: se usa el mejor otro plan
                    elegido = max((p for p in planes if not p["clave"].startswith("contraflujo")),
                                  key=lambda p: p["utilidad"])
                plan_info = {"cruce": cruce, "plan": elegido["nombre"], "modos": elegido.get("semaforos", {}),
                             "tstt_modelo_nada": next((p["tstt"] for p in planes if p["clave"] == "solo_avisar"), None),
                             "tstt_modelo_plan": elegido.get("tstt"), "aviso": elegido.get("aviso", "")}
            ventana = {"fin": None}

            def vigente(env=env, acc=acc, ventana=ventana, plan_info=plan_info):
                if acc and acc.get("activo") and ventana["fin"] is None:
                    ventana["fin"] = env.t + K.SEGUNDOS_PLAN
                return plan_info["modos"] if ventana["fin"] is not None and env.t < ventana["fin"] else {}

            if politica in ("dqn", "sma"):
                control = controlador_sma(vigente if politica == "sma" else (lambda: {}))
                fin = False
                while not fin:
                    obs, _, fin, _ = env.step(control(obs))
            else:
                while env.t < duracion and env.traci.simulation.getMinExpectedNumber() > 0:
                    if politica == "plan":
                        aplicar(env, vigente())
                    env._avanzar(1)
            m = env.metricas()
            env.cerrar()
            fila = {"semilla": 500 + s, "politica": politica, "tramo": acc["nombre"] if acc else None,
                    "base": acc["base"] if acc else None, "inicio_s": round(acc["inicio"]) if acc else None,
                    "demora_veh_h": m["demora_veh_h"], "viaje_medio_s": m["viaje_medio_s"], "llegados": m["llegados"]}
            if plan_info:
                fila.update({k: v for k, v in plan_info.items() if k != "modos"},
                            modos=", ".join(f"{c}:{x}" for c, x in plan_info["modos"].items()) or "sin cambios")
            filas.append(fila)
            print(f"caso1 semilla {500 + s} {politica:5s}: {acc['nombre'] if acc else '-'} | demora {m['demora_veh_h']} veh·h,"
                  f" viaje {m['viaje_medio_s']} s", flush=True)
    guardar("caso1_sumo", filas)
    return filas


# ====================================================================== Caso 2
def caso2(semillas=5, hora=17, deteccion=600, limite=1800):
    """El solicitado llega a un cruce con cámara (a 60 m de la línea de pare, por la vía principal) y huye hacia la
    salida de Bocagrande. 'sin' = semáforos normales; 'retencion' = modos de retencion_semaforos (Predictor + Coordinador)
    durante SEGUNDOS_PLAN. Mide cuándo llega a la salida (tiempo para la Policía) y el costo para el resto."""
    import sma_agentes as S
    from entorno_sumo import EntornoBocagrande
    mapa = json.loads((RAIZ / "modelos" / "sumo" / "mapa.json").read_text(encoding="utf-8"))
    llega = {}
    for a in mapa["aristas"]:
        if not a["reserva"]:
            llega.setdefault(a["hasta"], []).append(a)
    salida = max(llega[mapa["geo"]["nodoSalida"]], key=lambda a: a["largo"])["id"]
    filas = []
    for cam, cruce in K.CAMARAS.items():
        huida = S.ruta_huida(cruce)
        alerta = {"tipo": "Seguridad", "camara": cam, "persona": "MEDICION", "similitud": 0.9, "ruta_huida": huida}
        ret = S.retencion_semaforos(alerta, cruce, S.nombre_cruce(cruce), "persona_solicitada")
        accesos = [a for a in mapa["aristas"] if a["control"] and a["control"][0] == cruce and a["control"][1] == "A"]
        for s in range(semillas):
            for politica in ("sin", "retencion"):
                env = EntornoBocagrande(semilla=700 + s, hora=hora, duracion=limite + 300)
                env.reset()
                tr = env.traci
                while env.t < deteccion:
                    env._avanzar(1)
                acceso = min(accesos, key=lambda a: tr.simulation.findRoute(a["id"], salida).travelTime)
                tr.route.add("r_sol", list(tr.simulation.findRoute(acceso["id"], salida).edges))
                tr.vehicle.add("solicitado", "r_sol", typeID="carro", departPos=str(max(0.0, acceso["largo"] - 60)),
                               departLane="best", departSpeed="max")
                tr.vehicle.setColor("solicitado", (255, 23, 68, 255))
                if politica == "retencion":
                    aplicar(env, ret["semaforos"])
                visto = salio = None
                fin = deteccion + K.SEGUNDOS_PLAN
                esperas0, llegados0, viajes0 = env.esperas, env.llegados, len(env.viajes)
                while env.t < limite:
                    if politica == "retencion" and env.t == fin:
                        aplicar(env, {})
                    env._avanzar(1)
                    presente = "solicitado" in tr.vehicle.getIDList()
                    if presente and visto is None:
                        visto = env.t
                    if visto is not None and not presente:
                        salio = env.t
                        break
                ventana = env.t - deteccion
                viajes = env.viajes[viajes0:]
                env.cerrar()
                fila = {"camara": cam, "cruce": cruce, "semilla": 700 + s, "politica": politica,
                        "tiempo_huida_s": (salio - deteccion) if salio else None, "escapo_antes_del_limite": bool(salio),
                        "demora_resto_veh_h": round((env.esperas - esperas0) / 3600, 2),
                        "viaje_medio_resto_s": round(st.mean(viajes), 1) if viajes else None,
                        "ventana_s": ventana, "modos": ", ".join(f"{c}:{m}" for c, m in ret["semaforos"].items()),
                        "ruta_predicha": " → ".join(huida["calles"]), "prob_ruta": huida["confianza"],
                        "intercepcion": huida["intercepcion"]["cruce"]}
                filas.append(fila)
                print(f"caso2 {cam} semilla {700 + s} {politica:9s}: huida {fila['tiempo_huida_s']} s | "
                      f"demora resto {fila['demora_resto_veh_h']} veh·h", flush=True)
    guardar("caso2_sumo", filas)
    return filas


# ====================================================================== Comunicador
def comunicador():
    """Avisos reales del SMA (los del Caso 1 y los del Caso 2) redactados por el LLM, sin enviarlos."""
    import comunicador as C
    import sma_agentes as S
    avisos = []
    c1 = SALIDA / "caso1_sumo.json"
    if c1.exists():
        for f in json.loads(c1.read_text(encoding="utf-8")):
            if f.get("aviso"):
                avisos.append({"receptor": "Ciudadania", "plan": f["plan"], "aviso": f["aviso"]})
    for cam, cruce in K.CAMARAS.items():
        a = {"tipo": "Seguridad", "camara": cam, "persona": "Alias", "similitud": 0.88, "ruta_huida": S.ruta_huida(cruce)}
        avisos.append({"receptor": "Policia", "plan": "Cerco semafórico",
                       "aviso": S.retencion_semaforos(a, cruce, S.nombre_cruce(cruce), "persona_solicitada")["aviso"]})
    filas = []
    for i, d in enumerate(avisos):
        d = {**d, "duracion": K.SEGUNDOS_PLAN, "alerta": {"id": f"M-{i}", "tipo": "Seguridad" if d["receptor"] == "Policia" else "Movilidad"}}
        t0 = time.perf_counter()
        texto, origen = C.redactar(d)
        filas.append({"receptor": d["receptor"], "origen": "LLM" if origen.startswith("LLM") else "plantilla",
                      "motivo": origen, "segundos": round(time.perf_counter() - t0, 2),
                      "palabras": len(texto.split()), "texto": texto})
        print(f"comunicador {i + 1}/{len(avisos)} [{d['receptor']}] {origen} ({filas[-1]['segundos']} s)", flush=True)
    guardar("comunicador_llm", filas)
    return filas


# ====================================================================== Latencia de la cadena de agentes
RESPALDO = RAIZ / "mediciones" / ".respaldo"          # en .gitignore; sobrevive si la medición se corta a la fuerza


def respaldar(archivos):
    RESPALDO.mkdir(parents=True, exist_ok=True)
    estado = {}
    for r in archivos:
        copia = RESPALDO / f"{r.parent.name}__{r.name}"
        if r.exists():
            copia.write_bytes(r.read_bytes())
        estado[str(r)] = copia.name if r.exists() else None
    (RESPALDO / "indice.json").write_text(json.dumps(estado), encoding="utf-8")
    return estado


def restaurar_respaldos(archivos):
    indice = RESPALDO / "indice.json"
    if not indice.exists():
        return
    for ruta, copia in json.loads(indice.read_text(encoding="utf-8")).items():
        r = Path(ruta)
        if copia:
            r.write_bytes((RESPALDO / copia).read_bytes())
        else:
            r.unlink(missing_ok=True)
    indice.unlink()
    print("respaldos restaurados (memoria de los agentes y registro de rostros)")
def latencia(repeticiones=8):
    """Levanta los 7 agentes (como las pruebas), dispara eventos y mide con marcas de tiempo MQTT:
    evento -> plan publicado por el Semafórico (sma/plan/<cruce>) -> aviso redactado por el Comunicador."""
    import subprocess
    import threading

    import numpy as np
    import paho.mqtt.client as mqtt
    sys.path.insert(0, str(RAIZ / "pruebas"))
    from prueba_punta_a_punta import ENTORNO, GRUPO, esperar_linea, leer_en_segundo_plano, terminar
    registro = RAIZ / "vision" / "registro" / "registro.json"
    memoria = RAIZ / "agentes" / "memoria"
    archivos = (memoria / "experiencia_planes.json", memoria / "q_semaforico.json", registro)
    restaurar_respaldos(archivos)             # si una corrida anterior se cortó, primero deja todo como estaba
    respaldos = respaldar(archivos)
    rng = np.random.default_rng(11)
    personas = []
    for i in range(repeticiones):           # una persona sintética distinta por repetición (la alerta no se repite)
        v = rng.normal(size=128); v /= np.linalg.norm(v)
        personas.append((f"MEDICION{i}", v))
    registro.parent.mkdir(parents=True, exist_ok=True)
    registro.write_text(json.dumps({"personas": [{"alias": a, "vector": v.tolist(), "muestras": 1, "consistencia": 1.0,
                                                  "consentimiento": "medicion"} for a, v in personas]}), encoding="utf-8")
    marcas, evento = [], threading.Event()
    actual = {}

    def al_llegar(_c, _u, msg):
        ahora = time.perf_counter()
        try:
            d = json.loads(msg.payload)
        except ValueError:
            return
        if msg.topic != "sma/panel/log" or not actual.get("armado"):
            return
        agente, texto = d.get("agente"), d.get("texto", "")
        if agente == "Coordinador" and texto.startswith("recibí AL-"):
            actual["alerta"] = texto.split()[1]            # el id de ESTA alerta
        elif agente == "Semaforico" and texto.startswith("activarPlan en") and "alerta" in actual \
                and "plan" not in actual:
            actual["plan"] = ahora
        elif agente == "Comunicador" and texto.startswith("[para") and "plan" in actual and "aviso" not in actual:
            actual["aviso"] = ahora
            evento.set()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-mediciones")
    cliente.on_message = al_llegar
    cliente.connect("127.0.0.1", 1883)
    cliente.subscribe([("sma/panel/log", 0)])
    cliente.loop_start()
    sensores_on = threading.Event()

    def sensores():
        g = np.random.default_rng(3)
        while not sensores_on.is_set():
            for i in range(1, 6):
                a, b = (int(x) for x in g.poisson((5, 3)))
                cliente.publish(f"sma/sensores/cruce{i}", json.dumps({"A": a, "B": b}))
            sensores_on.wait(2)

    threading.Thread(target=sensores, daemon=True).start()
    agentes = None
    try:
        agentes = subprocess.Popen([sys.executable, "agentes/sma_agentes.py", "--duracion", "6", "--olvidar"], cwd=RAIZ,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1, env={**ENTORNO, "SMA_SIN_TELEGRAM": "1"}, **GRUPO)
        leer_en_segundo_plano(agentes)
        esperar_linea(agentes, "Los 7 agentes del SMA están activos", 90)
        time.sleep(4)
        cruces = list(K.CAMARAS.values())
        for i in range(repeticiones):
            for caso in ("caso1", "caso2"):
                actual.clear(); evento.clear()
                actual["armado"] = True
                cruce = ["cruce1", "cruce2", "cruce3", "cruce4", "cruce5"][i % 5] if caso == "caso1" else cruces[i % 3]
                t0 = time.perf_counter()
                if caso == "caso1":
                    cliente.publish(f"sma/eventos/{cruce}", "accidente")
                else:
                    cam = [c for c, x in K.CAMARAS.items() if x == cruce][0]
                    v = personas[i][1] + rng.normal(0, 0.04, 128); v /= np.linalg.norm(v)
                    cliente.publish(f"sma/camaras/{cam}", json.dumps({"camara": cam, "embedding": v.tolist()}))
                evento.wait(120)
                marcas.append({"caso": caso, "cruce": cruce,
                               "evento_a_semaforos_s": round(actual["plan"] - t0, 2) if "plan" in actual else None,
                               "evento_a_aviso_s": round(actual["aviso"] - t0, 2) if "aviso" in actual else None})
                print(f"latencia {caso} {cruce}: {marcas[-1]}", flush=True)
                esperar_linea(agentes, "cerrado", 60)
                time.sleep(2)
    finally:
        sensores_on.set()
        cliente.loop_stop(); cliente.disconnect()
        terminar(agentes)
        restaurar_respaldos(archivos)
    guardar("latencia_agentes", marcas)
    return marcas


# ====================================================================== Reconocimiento facial
def seccion_rostros(plt):
    """Lee vision/registro/medicion_rostros.json (de `camara.py --medir`, ignorado por Git) y calcula, con el umbral
    del Consultor: aciertos (identifica al registrado correcto), falsos rechazos, falsos positivos (acepta a quien no es)
    y la curva por umbral. En el informe los alias se reemplazan por P1, P2… (no se publican nombres)."""
    ruta = RAIZ / "vision" / "registro" / "medicion_rostros.json"
    if not ruta.exists():
        return []
    sys.path.insert(0, str(RAIZ / "vision"))
    from camara import acierto
    todas = json.loads(ruta.read_text(encoding="utf-8"))["tomas"]
    limpia = lambda c: c.strip().lower() if any(ch.isalpha() for ch in c) else "frente"   # '}' tecleado por error
    # 1) solo las tomas hechas contra el registro ACTUAL (las de otro registro, p. ej. el sintético de la medición
    #    de latencia, no son comparables)
    registro = RAIZ / "vision" / "registro" / "registro.json"
    actuales = {p["alias"] for p in json.loads(registro.read_text(encoding="utf-8"))["personas"]} if registro.exists() else None
    tomas = [t for t in todas if actuales is None or (t["similitudes"] and set(t["similitudes"][0]) == actuales)]
    # 2) muestras de "cambio de persona": la captura empezó con el participante ANTERIOR aún frente a la cámara
    #    (otro registrado con similitud ≥ 0.6, y es justo el de la toma anterior). No es un error del reconocimiento
    #    (acertó con quien estaba) sino de etiqueta: se excluyen y se informa cuántas.
    muestras, transicion, anterior = [], 0, None
    for t in tomas:
        for s in t["similitudes"]:
            quien = max(s, key=s.get)
            if anterior and quien == anterior != t["real"] and s[quien] >= 0.6:
                transicion += 1
                continue
            muestras.append((t["real"], limpia(t["condicion"]), s))
        anterior = t["real"] if t["real"] != "otro" else anterior
    if not muestras:
        return []
    alias = sorted({a for _, _, s in muestras for a in s})
    anonimo = {a: f"P{i + 1}" for i, a in enumerate(alias)}
    umbral = K.UMBRAL_SIMILITUD_ROSTRO
    genuinas = [s[r] for r, _, s in muestras if r != "otro"]
    impostoras = [v for r, _, s in muestras for a, v in s.items() if a != r]

    def tasas(u):
        reg = [(r, s) for r, _, s in muestras if r != "otro"]
        otros = [s for r, _, s in muestras if r == "otro"]
        acierto_reg = sum(acierto(s, r, u) for r, s in reg) / len(reg) if reg else None
        confunde = sum(1 for r, s in reg if max(s.values()) >= u and max(s, key=s.get) != r) / len(reg) if reg else None
        fp_otros = sum(1 for s in otros if max(s.values()) >= u) / len(otros) if otros else None
        return acierto_reg, confunde, fp_otros

    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    a, c, f = tasas(umbral)
    md = ["## 6. Reconocimiento facial (YuNet + SFace) con compañeros reales", "",
          f"{len(tomas)} tomas, {len(muestras)} muestras; {len(alias)} personas registradas, "
          f"{sum(1 for r, _, _ in muestras if r == 'otro')} muestras de personas NO registradas. Solo se guardaron "
          "similitudes (ni imágenes ni vectores); los alias se muestran como P1, P2…",
          f"Se excluyeron {len(todas) - len(tomas)} toma(s) hechas contra otro registro y {transicion} muestra(s) de "
          "cambio de persona (la captura empezó con el participante anterior aún frente a la cámara; el sistema lo "
          "reconoció a él con similitud ≥ 0.6, así que no son errores del reconocimiento).", "",
          f"Con el umbral del Consultor ({umbral}):", "",
          "| Métrica | Valor |", "|---|---|",
          f"| Registrado identificado correctamente (verdadero positivo) | {pct(a)} |",
          f"| Registrado confundido con OTRO registrado | {pct(c)} |",
          f"| No registrado tomado como solicitado (falso positivo) | {pct(f)} |",
          f"| Similitud con la persona correcta (media) | {st.mean(genuinas):.3f} |" if genuinas else "",
          f"| Similitud con otra persona (media) | {st.mean(impostoras):.3f} |" if impostoras else "", ""]
    condiciones = sorted({cnd for _, cnd, _ in muestras})
    if len(condiciones) > 1:
        md += ["| Condición | Muestras | Decisiones correctas |", "|---|---|---|"]
        for cnd in condiciones:
            xs = [(r, s) for r, c_, s in muestras if c_ == cnd]
            md.append(f"| {cnd} | {len(xs)} | {pct(sum(acierto(s, r) for r, s in xs) / len(xs))} |")
        md.append("")
    md += ["Efecto del umbral:", "", "| Umbral | Acierto de registrados | Confusiones | Falsos positivos (no registrados) |",
           "|---|---|---|---|"]
    for u in (0.25, 0.30, 0.363, 0.40, 0.45, 0.50, 0.55):
        a, c, f = tasas(u)
        md.append(f"| {u}{' (actual)' if u == umbral else ''} | {pct(a)} | {pct(c)} | {pct(f)} |")
    porpersona = [(anonimo.get(r, "no registrado"), sum(acierto(s, r) for r_, _, s in muestras if r_ == r),
                   sum(1 for r_, _, _ in muestras if r_ == r)) for r in sorted({r for r, _, _ in muestras})]
    md += ["", "| Persona | Decisiones correctas |", "|---|---|"]
    md += [f"| {p} | {n}/{t} |" for p, n, t in porpersona]
    if genuinas and impostoras:
        fig, ax = plt.subplots(figsize=(6.5, 3.4))
        ax.hist(impostoras, bins=30, alpha=0.7, color="#9aa5b1", label="con otra persona")
        ax.hist(genuinas, bins=30, alpha=0.8, color="#1f6feb", label="con la persona correcta")
        ax.axvline(umbral, color="#d1242f", ls="--", label=f"umbral {umbral}")
        ax.set_xlabel("similitud coseno (SFace)"); ax.set_ylabel("muestras"); ax.legend(frameon=False)
        ax.set_title("Reconocimiento facial: similitudes medidas")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); fig.savefig(SALIDA / "rostros_similitudes.png", dpi=150); plt.close(fig)
        md += ["", "![Rostros](rostros_similitudes.png)"]
    return md + [""]


# ====================================================================== Informe
def informe():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def leer(n):
        p = SALIDA / f"{n}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    md = ["# Resultados del SMA Movilidad y Seguridad – Bocagrande", "",
          f"Generado con `python mediciones/medir.py informe` el {time.strftime('%d-%m-%Y %H:%M')}. "
          "Media ± intervalo de confianza del 95 % (t de Student). Semillas fijas: los experimentos se pueden repetir.", ""]

    c1 = leer("caso1_sumo")
    if c1:
        md += ["## 1. Caso 1 – Accidente real → plan de desvío (SUMO, hora pico 17 h, 30 min)", "",
               "Cada semilla sortea un accidente real del DATT (tramo con su frecuencia real en Bocagrande). Todas las "
               "políticas ven el MISMO tráfico y el MISMO accidente; el plan es el que elige el Coordinador (modelo de "
               "equilibrio + utilidad) y rige 5 min desde que ocurre el accidente.", "",
               "| Política | Demora total (veh·h) | Viaje medio (s) | Vehículos que llegaron |", "|---|---|---|---|"]
        for pol, nombre in POLITICAS_C1.items():
            xs = [f for f in c1 if f["politica"] == pol]
            if not xs:
                continue
            d, v, l = (media_ic([f[k] for f in xs]) for k in ("demora_veh_h", "viaje_medio_s", "llegados"))
            md.append(f"| {nombre} | {d[0]:.1f} ± {d[1]:.1f} | {v[0]:.1f} ± {v[1]:.1f} | {l[0]:.0f} ± {l[1]:.0f} |")
        fijo = {f["semilla"]: f for f in c1 if f["politica"] == "fijo"}
        md += ["", "Mejora frente a semáforos fijos, pareada por semilla (mismo tráfico y mismo accidente):", "",
               "| Política | Reducción de la demora | Reducción del viaje medio | Accidentes en que baja la demora |",
               "|---|---|---|---|"]
        for pol, nombre in list(POLITICAS_C1.items())[1:]:
            xs = [f for f in c1 if f["politica"] == pol and f["semilla"] in fijo]
            if not xs:
                continue
            dd = [100 * (fijo[f["semilla"]]["demora_veh_h"] - f["demora_veh_h"]) / fijo[f["semilla"]]["demora_veh_h"] for f in xs]
            vv = [100 * (fijo[f["semilla"]]["viaje_medio_s"] - f["viaje_medio_s"]) / fijo[f["semilla"]]["viaje_medio_s"]
                  for f in xs]
            d, v = media_ic(dd), media_ic(vv)
            md.append(f"| {nombre} | **{d[0]:.1f} % ± {d[1]:.1f}** | {v[0]:.1f} % ± {v[1]:.1f} | "
                      f"{sum(x > 0 for x in dd)} de {len(dd)} |")
        sma = {f["semilla"]: f for f in c1 if f["politica"] == "sma"}
        md += ["", "| Semilla | Tramo del accidente | Cruce | Plan elegido | Modos del plan | Demora fijo → SMA completo (veh·h) |",
               "|---|---|---|---|---|---|"]
        for sem, a_ in fijo.items():
            md.append(f"| {sem} | {a_['tramo']} | {a_.get('cruce', '')} | {a_.get('plan', '')} | {a_.get('modos', '')} | "
                      f"{a_['demora_veh_h']} → {sma.get(sem, {}).get('demora_veh_h', '-')} |")
        fig, ax = plt.subplots(figsize=(7.5, 3.8))
        sems = sorted(fijo)
        colores = {"fijo": "#9aa5b1", "plan": "#f0b429", "dqn": "#57a773", "sma": "#1f6feb"}
        for k, pol in enumerate(POLITICAS_C1):
            val = {f["semilla"]: f["demora_veh_h"] for f in c1 if f["politica"] == pol}
            ax.bar([i + (k - 1.5) * 0.2 for i in range(len(sems))], [val.get(x, 0) for x in sems], 0.2,
                   label=POLITICAS_C1[pol], color=colores[pol])
        ax.set_xticks(range(len(sems)), [str(x) for x in sems], fontsize=8)
        ax.set_xlabel("semilla (accidente real distinto)"); ax.set_ylabel("demora total (veh·h)")
        ax.set_title("Caso 1: demora con un accidente real"); ax.legend(frameon=False, fontsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); fig.savefig(SALIDA / "caso1_demora.png", dpi=150); plt.close(fig)
        md += ["", "![Caso 1](caso1_demora.png)", ""]

    c2 = leer("caso2_sumo")
    if c2:
        md += ["## 2. Caso 2 – Retención semafórica del solicitado (SUMO, hora pico)", "",
               "El solicitado es detectado a 60 m de un cruce con cámara y huye hacia la salida de Bocagrande. Se compara "
               "el tiempo que tarda en salir con semáforos normales y con la retención que ordena el SMA (cerco en la "
               "ruta más probable del Predictor y rojo largo en su acceso en las alternas, 5 min).", "",
               "| Cámara (cruce) | Ruta predicha (prob.) | Huida sin retención (s) | Huida con retención (s) | Tiempo ganado para la Policía (s) | Demora extra del resto (veh·h) |",
               "|---|---|---|---|---|---|"]
        etiquetas, sin_, con_ = [], [], []
        for cam in K.CAMARAS:
            xs = [f for f in c2 if f["camara"] == cam]
            if not xs:
                continue
            s_ = media_ic([f["tiempo_huida_s"] for f in xs if f["politica"] == "sin"])
            r_ = media_ic([f["tiempo_huida_s"] or f["ventana_s"] for f in xs if f["politica"] == "retencion"])
            pares = [(a, b) for a in xs if a["politica"] == "sin" for b in xs
                     if b["politica"] == "retencion" and b["semilla"] == a["semilla"]]
            gan = media_ic([(b["tiempo_huida_s"] or b["ventana_s"]) - a["tiempo_huida_s"] for a, b in pares
                            if a["tiempo_huida_s"]])
            extra = media_ic([b["demora_resto_veh_h"] - a["demora_resto_veh_h"] for a, b in pares])
            md.append(f"| {cam} ({xs[0]['cruce']}) | {xs[0]['ruta_predicha']} ({xs[0]['prob_ruta']}) | {s_[0]:.0f} ± {s_[1]:.0f} | "
                      f"{r_[0]:.0f} ± {r_[1]:.0f} | **{gan[0]:.0f} ± {gan[1]:.0f}** | {extra[0]:.1f} ± {extra[1]:.1f} |")
            etiquetas.append(f"{cam}\n({xs[0]['cruce']})"); sin_.append(s_[0]); con_.append(r_[0])
        md += ["", "La retención le da a la Policía minutos en vez de segundos para llegar al punto de intercepción; el "
                   "costo es demora para los demás conductores durante el cerco (por eso dura solo 5 min y solo en la "
                   "ruta probable).", ""]
        fig, ax = plt.subplots(figsize=(6.5, 3.6))
        xs = range(len(etiquetas))
        ax.bar([x - 0.2 for x in xs], sin_, 0.4, label="sin retención", color="#9aa5b1")
        ax.bar([x + 0.2 for x in xs], con_, 0.4, label="con retención del SMA", color="#d1242f")
        ax.set_xticks(list(xs), etiquetas); ax.set_ylabel("segundos hasta salir de Bocagrande")
        ax.set_title("Caso 2: tiempo de huida del solicitado"); ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); fig.savefig(SALIDA / "caso2_huida.png", dpi=150); plt.close(fig)
        md += ["![Caso 2](caso2_huida.png)", ""]

    lat = leer("latencia_agentes")
    if lat:
        md += ["## 3. Tiempo de respuesta de la cadena de agentes", "",
               "Desde que llega el evento (botón/cámara) hasta que los semáforos reciben el plan y hasta que el "
               "Comunicador tiene el aviso redactado (incluye el modelo de equilibrio y el LLM).", "",
               "| Caso | Evento → semáforos (s) | Evento → aviso redactado (s) | n |", "|---|---|---|---|"]
        for caso, nombre in (("caso1", "Caso 1 (accidente)"), ("caso2", "Caso 2 (persona solicitada)")):
            xs = [f for f in lat if f["caso"] == caso]
            a, b = media_ic([f["evento_a_semaforos_s"] for f in xs]), media_ic([f["evento_a_aviso_s"] for f in xs])
            if a[0] is not None:
                md.append(f"| {nombre} | {a[0]:.1f} ± {a[1]:.1f} | {b[0]:.1f} ± {b[1]:.1f} | {len(xs)} |")
        md.append("")

    com = leer("comunicador_llm")
    if com:
        md += ["## 4. Comunicador: redacción con LLM local (Ollama llama3.2:3b)", "",
               "Avisos reales del SMA. 'LLM' = el texto del modelo pasó el control (conserva cruces, calles, cifras, el "
               "alias y 'posible coincidencia'); si no, sale la plantilla.", "",
               "| Receptor | Avisos | Redactados por el LLM | Plantilla | Tiempo de redacción (s) |", "|---|---|---|---|---|"]
        for r in ("Ciudadania", "Policia"):
            xs = [f for f in com if f["receptor"] == r]
            if xs:
                t = media_ic([f["segundos"] for f in xs])
                n = sum(f["origen"] == "LLM" for f in xs)
                md.append(f"| {r} | {len(xs)} | {n} ({100 * n / len(xs):.0f} %) | {len(xs) - n} | {t[0]:.1f} ± {t[1]:.1f} |")
        md.append("")

    md += seccion_rostros(plt)

    tg = RAIZ / "modelos" / "entrenados" / "metricas_bocagrande.json"
    cc = RAIZ / "modelos" / "entrenados" / "comparacion_controladores.json"
    if tg.exists() or cc.exists():
        md += ["## 5. Redes neuronales (ya medidas al entrenarlas)", ""]
        if tg.exists():
            md += ["- Predictor (T-GCN): `modelos/entrenados/metricas_bocagrande.json` y `prediccion_bocagrande.png`."]
        if cc.exists():
            md += ["- Semafórico (DQN) frente a fijo, tabla Q y max-presión: `modelos/entrenados/comparacion_controladores.*`."]
        md.append("")
    (SALIDA / "resultados.md").write_text("\n".join(md), encoding="utf-8")
    print(f"informe -> {SALIDA / 'resultados.md'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Mediciones del SMA")
    p.add_argument("experimento", choices=["caso1", "caso2", "comunicador", "latencia", "informe", "todo", "restaurar"])
    p.add_argument("--semillas", type=int, default=None)
    a = p.parse_args()
    if a.experimento in ("caso1", "todo"):
        caso1(a.semillas or 10)
    if a.experimento in ("caso2", "todo"):
        caso2(a.semillas or 5)
    if a.experimento in ("comunicador", "todo"):
        comunicador()
    if a.experimento == "restaurar":       # si una medición de latencia se cortó a la fuerza
        restaurar_respaldos([])
    if a.experimento == "latencia":
        latencia(a.semillas or 8)
    if a.experimento in ("informe", "todo"):
        informe()
