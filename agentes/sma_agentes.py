"""
SMA Movilidad y Seguridad CTG - Los 7 agentes del SMA (SPADE + MQTT)
====================================================================
Caso 1 (accidente -> plan de desvío elegido por utilidad) de punta a punta:

  ESP32 / panel --MQTT--> AgenteVigilancia --inform--> AgenteClasificadorAlertas
  --inform--> AgenteCoordinador --query-ref--> AgenteConsultor (lee los sensores de cola)
  AgenteCoordinador --request--> AgenteSemaforico --MQTT--> ESP32 / simulador
  AgenteCoordinador --request--> AgenteComunicador (por ahora imprime el aviso)
  AgentePredictor queda listo para el Caso 2 (responde rutas de huida).

Además el AgenteSemaforico controla los semáforos por su cuenta: percibe las colas de cada acceso
(sma/sensores/<cruce>, las mide el gemelo digital) y da prioridad al acceso más cargado; aprende con
Q-learning cuál modo conviene. El AgenteCoordinador aprende cuánto sirvió cada plan.

  Reglas y parámetros ........ agentes/conocimiento.py
  Aprendizaje ................ agentes/aprendizaje.py   (memoria en agentes/memoria/*.json)
  Ver la mente en vivo ....... http://localhost:8000/agentes

Uso:  python agentes/sma_agentes.py                  (espera eventos por MQTT)
      python agentes/sma_agentes.py --simular        (dispara un accidente de prueba)
      python agentes/sma_agentes.py --duracion 120   (segundos que dura cada plan; por defecto 300)
      python agentes/sma_agentes.py --olvidar        (empieza sin experiencia: borra lo aprendido)

Desde la carpeta de Mosquitto:
      mosquitto_pub -h localhost -t sma/eventos/cruce1 -m accidente
      mosquitto_pub -h localhost -t sma/eventos/cruce5 -m "{\"tipo\":\"accidente\",\"sentido\":\"salida\"}"
      mosquitto_pub -h localhost -t sma/eventos/cruce5 -m "{\"tipo\":\"fin_contingencia\",\"alerta\":\"AL-0001\"}"
"""

import asyncio
import json
import random
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour, PeriodicBehaviour
from spade.message import Message
from spade.template import Template

import conocimiento as K
import comunicador
import rutas
from aprendizaje import ExperienciaPlanes, QSemaforo

# Modelos neuronales (modelos/): si falta PyTorch o un modelo entrenado, los agentes siguen con sus reglas y la tabla Q
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modelos"))
try:
    import numpy as np
    import torch
    from dqn import MODOS as MODOS_DQN, RASGOS as RASGOS_DQN, cargar_politica, vector_estado
    from predictor_colas import PredictorColas
    NEURONAL = True
    _ERROR_NEURONAL = ""
except Exception as _error:          # noqa: BLE001 - cualquier fallo de importación deja el modo clásico
    NEURONAL = False
    _ERROR_NEURONAL = str(_error)
RUTA_DQN = Path(__file__).resolve().parent.parent / "modelos" / "entrenados" / "dqn_semaforico.pt"


def vecinos_cruces():
    """Los 2 cruces más cercanos de cada cruce (mismo criterio que en el entrenamiento en SUMO)."""
    try:
        geo = json.loads((Path(__file__).resolve().parent.parent / "maqueta_virtual" / "bocagrande" / "bocagrande.json")
                         .read_text(encoding="utf-8"))
        pos = {c["id"]: (c["x"], c["z"]) for c in geo["cruces"]}
    except (OSError, ValueError, KeyError):
        return {}
    import math
    return {c: sorted((o for o in pos if o != c), key=lambda o: math.dist(pos[c], pos[o]))[:2] for c in pos}

# la consola de Windows (cp1252) no puede imprimir algunos símbolos: se reemplazan en vez de fallar
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------- Configuración técnica ----------------
DOMINIO = "localhost"          # servidor XMPP embebido de SPADE
CLAVE = "sma123"
BROKER_MQTT = "localhost"      # PC donde corre Mosquitto
PUERTO_MQTT = 1883
TEMA_EVENTOS = "sma/eventos/#"          # lo que publica el ESP32, el panel o mosquitto_pub
TEMA_SEMAFORO = "sma/semaforo/{cruce}"  # lo que escucha el ESP32 / simulador
TEMA_PLAN = "sma/plan/{cruce}"          # plan activo (lo dibuja el gemelo digital 3D)
TEMA_SENSORES = "sma/sensores/#"        # colas por acceso {"A": n, "B": n} (las mide el gemelo digital)
TEMA_MENTE = "sma/agentes/{agente}"     # estado interno de cada agente (página /agentes)
TEMA_PANEL_LOG = "sma/panel/log"        # bitácora que muestra el panel
TEMA_CAMARAS = "sma/camaras/+"          # vectores de rostros de las cámaras (vision/camara.py), Caso 2
REGISTRO_ROSTROS = Path(__file__).resolve().parent.parent / "vision" / "registro" / "registro.json"   # ignorado por Git

SEGUNDOS_PLAN = K.SEGUNDOS_PLAN
if "--duracion" in sys.argv:             # p. ej. --duracion 60 (las decisiones siguen siendo inmediatas)
    SEGUNDOS_PLAN = float(sys.argv[sys.argv.index("--duracion") + 1])
OLVIDAR = "--olvidar" in sys.argv

try:
    _ruta = Path(__file__).resolve().parent.parent / "maqueta_virtual" / "cruces.json"
    NOMBRE_CRUCE = {c["id"]: c["nombre"] for c in json.loads(_ruta.read_text(encoding="utf-8"))["cruces"]}
except (OSError, ValueError, KeyError):
    NOMBRE_CRUCE = {}

JID = {n: f"{n}@{DOMINIO}" for n in
       ["vigilancia", "clasificador", "coordinador", "consultor",
        "predictor", "semaforico", "comunicador"]}

_mqtt_log = None                        # cliente MQTT para la bitácora y la "mente" de los agentes


def conectar_bitacora():
    """Conecta un cliente MQTT para que log() y publicar_mente() lleguen al panel web."""
    global _mqtt_log
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-bitacora")
    try:
        cliente.connect(BROKER_MQTT, PUERTO_MQTT)
        cliente.loop_start()
        _mqtt_log = cliente
    except OSError:
        _mqtt_log = None


def log(agente, texto):
    ahora = datetime.now()
    print(f"[{ahora:%H:%M:%S}] {agente:<13} | {texto}", flush=True)
    if _mqtt_log is not None:
        linea = {"ts": f"{ahora:%H:%M:%S}", "agente": agente, "texto": str(texto)}
        _mqtt_log.publish(TEMA_PANEL_LOG, json.dumps(linea, ensure_ascii=False))


def publicar_mente(agente, datos):
    """Publica (retenido) lo que el agente sabe, sus reglas y su estado interno para la página /agentes."""
    if _mqtt_log is not None:
        datos = {**datos, "agente": agente, "ts": f"{datetime.now():%H:%M:%S}"}
        _mqtt_log.publish(TEMA_MENTE.format(agente=agente.lower()), json.dumps(datos, ensure_ascii=False), retain=True)


def mensaje(para, performativa, accion, contenido, hilo=None):
    """Crea un mensaje FIPA-ACL con el contenido en JSON.
    hilo = conversación (id de la alerta): cada incidente recibe solo sus propias respuestas."""
    m = Message(to=JID[para])
    if hilo:
        m.thread = hilo
    m.set_metadata("performative", performativa)
    m.set_metadata("ontology", "sma-ctg")
    m.set_metadata("action", accion)
    m.body = json.dumps(contenido, ensure_ascii=False)
    return m


def plantilla(performativa, accion=None):
    t = Template()
    t.set_metadata("performative", performativa)
    if accion:
        t.set_metadata("action", accion)
    return t


def responder(msg, accion, contenido):
    r = msg.make_reply()
    r.set_metadata("performative", "inform")
    r.set_metadata("action", accion)
    r.body = json.dumps(contenido, ensure_ascii=False)
    return r


def nombre_cruce(cruce):
    return f"{cruce} ({NOMBRE_CRUCE[cruce]})" if cruce in NOMBRE_CRUCE else cruce


def escuchar_sensores(agente, client_id, al_leer):
    """Suscribe al agente a sma/sensores/#; al_leer(cruce, A, B) corre en el bucle de asyncio."""
    loop = asyncio.get_running_loop()

    def al_llegar(cliente, userdata, msg):
        try:
            d = json.loads(msg.payload.decode(errors="ignore"))
            loop.call_soon_threadsafe(al_leer, msg.topic.split("/")[-1], float(d.get("A", 0)), float(d.get("B", 0)))
        except (ValueError, AttributeError):
            pass

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    cliente.on_message = al_llegar
    cliente.on_connect = lambda c, u, f, rc, p: c.subscribe(TEMA_SENSORES)
    try:
        cliente.connect(BROKER_MQTT, PUERTO_MQTT)
        cliente.loop_start()
        return cliente
    except OSError:
        log(agente, "AVISO: sin Mosquitto no hay sensores de cola")
        return None


# =====================================================================
# 1. AGENTE VIGILANCIA  (reactivo simple): percibe eventos por MQTT
# =====================================================================
class AgenteVigilancia(Agent):
    async def setup(self):
        self.cola = asyncio.Queue()
        self.rostros = asyncio.Queue(maxsize=20)   # vectores de las cámaras (se descartan si se acumulan)
        self.contador = {}
        self.camaras = {"rostros_enviados": 0, "coincidencias": 0, "descartados": 0}
        self.alertado = {}                          # alias -> instante de su última alerta (no repetir durante el cerco)
        loop = asyncio.get_running_loop()

        def al_llegar(cliente, userdata, msg):
            if msg.topic.startswith("sma/camaras/"):
                try:
                    dato = json.loads(msg.payload)
                except ValueError:
                    return
                if not self.rostros.full():
                    loop.call_soon_threadsafe(self.rostros.put_nowait, dato)
                return
            texto = msg.payload.decode(errors="ignore").strip()
            try:
                evento = json.loads(texto)
            except ValueError:
                evento = {"tipo": texto}
            evento.setdefault("ubicacion", msg.topic.split("/")[-1])
            loop.call_soon_threadsafe(self.cola.put_nowait, evento)

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-vigilancia")
        self.mqtt.on_message = al_llegar
        try:
            self.mqtt.connect(BROKER_MQTT, PUERTO_MQTT)
            self.mqtt.subscribe(TEMA_EVENTOS)
            self.mqtt.subscribe(TEMA_CAMARAS)
            self.mqtt.loop_start()
            log("Vigilancia", f"escuchando eventos en MQTT '{TEMA_EVENTOS}' y cámaras en '{TEMA_CAMARAS}'")
        except OSError:
            log("Vigilancia", "AVISO: no pude conectarme a Mosquitto (usa --simular para probar sin él)")
        self.mente()
        self.add_behaviour(self.Percibir())
        self.add_behaviour(self.VerRostros())
        self.add_behaviour(self.RecibirCoincidencia(), plantilla("inform", "Coincidencia"))

    def mente(self, ultimo=None):
        publicar_mente("Vigilancia", {
            "tipo": "Reactivo simple", "rol": "Percibe los eventos de la calle y los reenvía al Clasificador.",
            "percibe": "sma/eventos/<cruce> (botón del ESP32, panel, mosquitto_pub); sma/camaras/<cam> (vectores de rostros)",
            "actua": "inform AlertaNovedad -> Clasificador; inform TerminarAlerta -> Coordinador; "
                     "query-ref consultarPersonaSolicitada -> Consultor",
            "codigo": ["agentes/sma_agentes.py · AgenteVigilancia"],
            "reglas": ["SI llega un evento ENTONCES enviarlo al Clasificador (sin razonar: reflejo)",
                       "SI el evento es fin_contingencia ENTONCES pedir al Coordinador cerrar esa alerta",
                       "SI una cámara ve un rostro ENTONCES preguntar al Consultor si es una persona solicitada",
                       "SI el Consultor confirma una posible coincidencia ENTONCES novedad persona_solicitada "
                       f"(una vez cada {K.SEGUNDOS_ENTRE_ALERTAS_PERSONA} s por persona)"],
            "parametros": {"tema": TEMA_EVENTOS, "camaras": K.CAMARAS},
            "estado": {"eventos_percibidos": self.contador, "camaras": self.camaras, "ultimo": ultimo},
        })

    class Percibir(CyclicBehaviour):
        async def run(self):
            evento = await self.agent.cola.get()
            evento["ts"] = datetime.now().isoformat(timespec="seconds")
            tipo = evento.get("tipo")
            self.agent.contador[tipo] = self.agent.contador.get(tipo, 0) + 1
            self.agent.mente(evento)
            if tipo == "fin_contingencia":
                log("Vigilancia", f"el operador pide terminar la contingencia {evento.get('alerta') or 'en ' + evento['ubicacion']}")
                await self.send(mensaje("coordinador", "inform", "TerminarAlerta", evento))
                return
            log("Vigilancia", f"percibí: {evento}")
            await self.send(mensaje("clasificador", "inform", "AlertaNovedad", evento))

    class VerRostros(CyclicBehaviour):
        """Caso 2: cada rostro que ve una cámara se consulta al Consultor (el vector NO se guarda aquí)."""
        async def run(self):
            dato = await self.agent.rostros.get()
            camara = dato.get("camara")
            if camara not in K.CAMARAS or not isinstance(dato.get("embedding"), list):
                return
            self.agent.camaras["rostros_enviados"] += 1
            await self.send(mensaje("consultor", "query-ref", "consultarPersonaSolicitada",
                                    {"camara": camara, "ubicacion": K.CAMARAS[camara], "embedding": dato["embedding"]}))

    class RecibirCoincidencia(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            ag, r = self.agent, json.loads(msg.body)
            tema = f"sma/camaras/{r.get('camara')}/resultado"
            if not r.get("coincide"):
                ag.camaras["descartados"] += 1
                ag.mqtt.publish(tema, json.dumps({"texto": "sin coincidencia (rostro descartado)"}))
                return
            ag.camaras["coincidencias"] += 1
            alias, ahora = r["persona"]["alias"], time.monotonic()
            texto = f"posible coincidencia ({r['similitud']:.2f})"
            if ahora - ag.alertado.get(alias, -1e9) < K.SEGUNDOS_ENTRE_ALERTAS_PERSONA:
                ag.mqtt.publish(tema, json.dumps({"texto": texto + " - cerco ya activo"}))
                return
            ag.alertado[alias] = ahora
            ag.mqtt.publish(tema, json.dumps({"texto": texto + " - alerta enviada"}))
            evento = {"tipo": "persona_solicitada", "ubicacion": r["ubicacion"], "camara": r["camara"],
                      "persona": alias, "similitud": r["similitud"], "ts": datetime.now().isoformat(timespec="seconds")}
            log("Vigilancia", f"cámara {r['camara']} ({nombre_cruce(r['ubicacion'])}): posible coincidencia con una persona "
                              f"solicitada (similitud {r['similitud']:.2f})")
            ag.mente(evento)
            await self.send(mensaje("clasificador", "inform", "AlertaNovedad", evento))


# =====================================================================
# 2. AGENTE CLASIFICADOR DE ALERTAS  (basado en objetivos)
# =====================================================================
class AgenteClasificador(Agent):
    async def setup(self):
        self.contador = 0
        self.mente()
        self.add_behaviour(self.Clasificar(), plantilla("inform", "AlertaNovedad"))

    def mente(self, ultima=None):
        publicar_mente("Clasificador", {
            "tipo": "Basado en objetivos", "rol": "Convierte cada novedad en una alerta con tipo y prioridad.",
            "percibe": "inform AlertaNovedad (de Vigilancia)", "actua": "inform Alerta -> Coordinador",
            "codigo": ["agentes/sma_agentes.py · AgenteClasificador", "agentes/conocimiento.py · REGLAS_CLASIFICACION"],
            "reglas": [f"SI evento = {e} ENTONCES {t}, prioridad {p}" for e, (t, p) in K.REGLAS_CLASIFICACION.items()]
                      + [f"SI no se conoce ENTONCES {K.CLASIFICACION_POR_DEFECTO[0]}, prioridad {K.CLASIFICACION_POR_DEFECTO[1]}"],
            "parametros": {},
            "estado": {"alertas_emitidas": self.contador, "ultima": ultima},
        })

    class Clasificar(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            novedad = json.loads(msg.body)
            tipo, prioridad = K.REGLAS_CLASIFICACION.get(novedad.get("tipo"), K.CLASIFICACION_POR_DEFECTO)
            self.agent.contador += 1
            alerta = {**novedad, "tipo_evento": novedad.get("tipo"),
                      "id": f"AL-{self.agent.contador:04d}", "tipo": tipo, "prioridad": prioridad}
            log("Clasificador", f"{alerta['id']}: {novedad.get('tipo')} -> {tipo}, prioridad {prioridad}")
            self.agent.mente(alerta)
            await self.send(mensaje("coordinador", "inform", "Alerta", alerta))


# =====================================================================
# 3. AGENTE COORDINADOR  (basado en utilidad + aprende el impacto real de cada plan)
# =====================================================================
def generar_planes(alerta, cruce, experiencia):
    """Planes candidatos según las reglas de conocimiento.py, con el impacto APRENDIDO si ya hay experiencia."""
    evento = alerta.get("tipo_evento")
    lugar = nombre_cruce(cruce)
    seguridad = alerta.get("tipo") == "Seguridad"
    claves = list(K.PLANES_POR_EVENTO["Seguridad" if seguridad else evento] if (seguridad or evento in K.PLANES_POR_EVENTO)
                  else ["solo_avisar"])
    sentido = None
    if evento == "accidente" and cruce == K.SALIDA_BOCAGRANDE:
        # el accidente bloquea la única salida (o entrada) de Bocagrande: se considera el contraflujo
        sentido = "entrada" if alerta.get("sentido") == "entrada" else "salida"
        claves.append(f"contraflujo_{sentido}")
    situacion = experiencia.situacion(evento if not seguridad else "seguridad", cruce, sentido)
    planes = []
    for clave in claves:
        c = K.CATALOGO_PLANES[clave]
        p = {"clave": clave, "nombre": c["nombre"], "impacto": experiencia.impacto(clave, situacion), "costo": c["costo"],
             "veces": experiencia.veces(clave, situacion), "situacion": situacion,
             "receptor": "Policia" if seguridad else "Ciudadania"}
        if clave == "solo_avisar":
            p.update(semaforos={}, aviso=(f"posible coincidencia ({evento}) en {lugar}." if seguridad
                                          else f"{evento} en {lugar}; transite con precaución."))
        elif clave == "cerco":       # Caso 2: retener al solicitado en sus rutas probables de huida (Predictor)
            p.update(retencion_semaforos(alerta, cruce, lugar, evento))
        elif clave.startswith("contraflujo"):
            p.update(semaforos=c["semaforos"], aviso=K.AVISOS_CONTRAFLUJO[clave])
        elif clave == "desvio_local":
            p.update(nombre=f"Desvío local en {cruce}", semaforos={cruce: "prioridad_a"},
                     aviso=f"accidente en {lugar}; más tiempo de verde para la vía libre, use vías alternas.")
        elif clave == "priorizar_principal":
            p.update(semaforos={cruce: "prioridad_a"}, aviso=f"congestión en {lugar}; se amplió el verde de la vía principal.")
        elif clave == "fase_peatonal":
            p.update(semaforos={cruce: "peatonal"},
                     aviso=f"aglomeración de personas en {lugar}; fase peatonal extendida, conduzca despacio.")
        elif clave == "semaforos_optimos":       # los modos los calcula el modelo (evaluar_con_modelo)
            p.update(semaforos={}, aviso=f"{evento} en {lugar}; semáforos del sector ajustados para repartir el flujo.")
        planes.append(p)
    return planes


def retencion_semaforos(alerta, cruce, lugar, evento):
    """Caso 2: el Semafórico RETRASA al solicitado para que la Policía lo intercepte.
    - Ruta más probable (Predictor): cerco (todo rojo) en cada cruce: queda retenido en cada uno.
    - Rutas alternas con probabilidad >= PROB_MINIMA_RUTA_HUIDA: rojo largo SOLO para el acceso por donde llegaría
      (prioridad al otro grupo), así el resto del tráfico sigue pasando.
    - Aviso a la Policía: ruta probable, punto de intercepción sugerido y tiempo estimado de llegada."""
    huida = alerta.get("ruta_huida") or {}
    quien = (f" con «{alerta['persona']}»" + (f" (similitud {alerta['similitud']:.2f})" if alerta.get("similitud") else "")
             if alerta.get("persona") else f" ({evento})")      # alias del solicitado: SOLO va en el aviso a la Policía
    rutas_h = huida.get("rutas") or [{"cruces": huida.get("cruces") or [], "accesos": {}, "probabilidad": 1.0}]
    semaforos = {c: "cerco" for c in dict.fromkeys([cruce] + list(rutas_h[0]["cruces"]))}
    alternas = []
    for r in rutas_h[1:]:
        if r.get("probabilidad", 0) < K.PROB_MINIMA_RUTA_HUIDA:
            continue
        for c, g in r.get("accesos", {}).items():
            if c not in semaforos:
                semaforos[c] = "prioridad_b" if g == "A" else "prioridad_a"     # el acceso del solicitado en rojo largo
                alternas.append(c)
    por = f" por {' → '.join(huida['calles'])}" if huida.get("calles") else ""
    inter = huida.get("intercepcion") or {}
    if not inter.get("cruce"):
        punto = ""
    elif inter.get("segundos", 0) <= 0:
        punto = f" Está junto a la salida de Bocagrande: interceptar de inmediato en {nombre_cruce(inter['cruce'])}."
    else:
        punto = (f" Intercepción sugerida en {nombre_cruce(inter['cruce'])} (llegaría en ~{inter['segundos']} s sin "
                 f"retención; los semáforos anteriores lo retrasan).")
    detalle = f"cerco en {', '.join(c for c, m in semaforos.items() if m == 'cerco')}" +         (f"; acceso del solicitado en rojo largo en {', '.join(alternas)}" if alternas else "")
    return {"semaforos": semaforos, "detalle": detalle,
            "aviso": f"posible coincidencia{quien} en {lugar} (cámara {alerta.get('camara', '-')}); ruta probable de "
                     f"huida{por} (probabilidad {huida.get('confianza', '-')}); {detalle}.{punto}"}


def accidente_de(alerta, cruce):
    """Cómo entra el evento al modelo de rutas: un accidente reduce la capacidad del tramo donde ocurre."""
    if alerta.get("tipo_evento") != "accidente":
        return None
    if alerta.get("arista") is not None:                 # accidente real del dataset: tramo exacto
        return {"arista_base": int(alerta["arista"])}
    return {"cruce": cruce, "sentido": alerta.get("sentido") or "salida"}


def evaluar_con_modelo(alerta, cruce, planes, nivel):
    """Modelo matemático (agentes/rutas.py): para cada plan calcula el equilibrio de tráfico (Frank-Wolfe con costo
    Akçelik/BPR) y su TSTT; propone los semáforos óptimos del sector; deja en cada plan su 'beneficio' (0..1).
    Corre en un hilo aparte (asyncio.to_thread) para no frenar a los demás agentes."""
    if alerta.get("tipo") == "Seguridad":
        return None
    R = rutas.red()
    evento = alerta.get("tipo_evento")
    acc = accidente_de(alerta, cruce)
    escala = K.ESCALA_POR_NIVEL.get(nivel, K.ESCALA_POR_NIVEL["desconocido"]) * K.ESCALA_POR_EVENTO.get(evento, 1.0)
    for p in planes:
        if p["clave"] == "semaforos_optimos":
            modos, _ = R.optimizar_semaforos(None, acc, escala=escala)
            p["semaforos"] = modos
            p["nombre"] = f"Semáforos optimizados ({len(modos)} cruces)" if modos else "Semáforos optimizados (sin cambios)"
            p["detalle"] = ", ".join(f"{c}: {m}" for c, m in modos.items())
    resultados = {}
    for p in planes:
        contra = p["clave"] if p["clave"].startswith("contraflujo") else None
        if contra:   # el contraflujo cambia la red: el modelo ajusta sus semáforos partiendo de los del catálogo
            catalogo = dict(p["semaforos"])
            modos, r = R.optimizar_semaforos(contra, acc, escala=escala, base=catalogo)
            ajustes = {c: (catalogo.get(c, "normal"), m) for c, m in {**{c: "normal" for c in catalogo}, **modos}.items()
                       if catalogo.get(c, "normal") != m}
            p["semaforos"] = {**{c: "normal" for c in catalogo}, **modos}
            p["ajuste_modelo"] = {c: f"{a} → {b}" for c, (a, b) in ajustes.items()}
        else:
            r = R.evaluar(contra, p["semaforos"], acc, escala=escala)
        resultados[p["clave"]] = r
        p["tstt"], p["viaje_min"], p["sin_ruta"] = r["tstt_vh"], r["viaje_medio_min"], r["sin_ruta_vh"]
    base = next((p["tstt"] for p in planes if p["clave"] == "solo_avisar"), max(p["tstt"] for p in planes))
    mejor = min(p["tstt"] for p in planes)
    for p in planes:   # 1 = el plan que más baja el TSTT, 0 = no mejora frente a solo avisar
        p["beneficio"] = round(max(0.0, min(1.0, (base - p["tstt"]) / (base - mejor))), 3) \
            if base - mejor > K.MEJORA_MINIMA * base else 0.0
    return {"escala": round(escala, 2), "demanda_vh": resultados[planes[0]["clave"]]["demanda_vh"],
            "resultados": resultados}


def rutas_sugeridas(resultado):
    """Yen k-rutas más rápidas con los tiempos del equilibrio del plan elegido (para el aviso y el gemelo 3D)."""
    R = rutas.red()
    salida = {}
    for tipo, lista in R.rutas_plan(resultado).items():
        salida[tipo] = [{"calles": x["calles"], "minutos": x["minutos"], "km": x["km"], "nodos": x["nodos"],
                         "reparto": x.get("reparto", 1.0),
                         "p": [[round(v, 1) for v in q] for i in x["aristas"] for q in R.E[i]["p"]]} for x in lista]
    return salida


class AgenteCoordinador(Agent):
    async def setup(self):
        self.experiencia = ExperienciaPlanes(olvidar=OLVIDAR)
        self.cierres = {}        # alerta -> asyncio.Event (para terminar la contingencia antes de tiempo)
        self.activas = {}        # alerta -> resumen del plan en curso
        self.ultima = None       # última decisión (tabla de utilidades)
        self.aprendido = []      # últimas correcciones de impacto
        self.mente()
        self.add_behaviour(self.Atender(), plantilla("inform", "Alerta"))
        self.add_behaviour(self.Terminar(), plantilla("inform", "TerminarAlerta"))

    def mente(self):
        catalogo = [{"clave": k, "nombre": c["nombre"], "costo": c["costo"], "impacto_inicial": c["impacto"]}
                    for k, c in K.CATALOGO_PLANES.items()]
        publicar_mente("Coordinador", {
            "tipo": "Basado en utilidad (aprende el impacto real de cada plan)",
            "rol": "Genera planes para cada alerta, elige el de mayor utilidad, los mantiene y los cierra.",
            "percibe": "inform Alerta; estado vial y colas medias (Consultor); TerminarAlerta (operador)",
            "actua": "request activarPlan / restaurarPlanNormal -> Semafórico; request comunicar -> Comunicador",
            "codigo": ["agentes/sma_agentes.py · AgenteCoordinador, generar_planes, evaluar_con_modelo",
                       "agentes/rutas.py · RedVial (Frank-Wolfe, Akçelik/BPR, Yen)",
                       "agentes/aprendizaje.py · ExperienciaPlanes", "agentes/conocimiento.py · Coordinador"],
            "reglas": [f"utilidad = ({K.PESO_MODELO} × beneficio del modelo + {1 - K.PESO_MODELO:.1f} × impacto aprendido) × factor de tráfico − "
                       f"{K.PESO_COSTO} × costo",
                       "Modelo matemático (agentes/rutas.py): grafo dirigido de Bocagrande (OSM); costo de cada tramo con la función "
                       f"{'Akçelik' if K.FUNCION_COSTO == 'akcelik' else 'BPR'}; equilibrio de usuario (Wardrop) por Frank-Wolfe; "
                       "TSTT = tiempo total de viaje en la red; beneficio = cuánto baja el TSTT frente a solo avisar (1 = el mejor plan)",
                       "El modelo PROPONE 'Semáforos optimizados': descenso por coordenadas del modo de cada cruce minimizando el TSTT",
                       f"Rutas sugeridas: Yen, {K.K_RUTAS} caminos más rápidos con los tiempos del equilibrio (aviso + gemelo 3D)",
                       f"SI accidente en {K.SALIDA_BOCAGRANDE} (única salida de Bocagrande) ENTONCES considerar contraflujo de la Carrera 1",
                       "SI alerta de Seguridad ENTONCES cerco y aviso SOLO a la Policía",
                       "Al cerrar un plan mide las colas de TODO el sector: efecto = (cola antes − cola durante) / máx(cola antes, 3)",
                       f"SI efecto > 0 ENTONCES impacto sube: + {K.ALFA_PLANES}·efecto·(1 − impacto); "
                       f"SI efecto < 0 ENTONCES baja: + {K.ALFA_PLANES}·efecto·impacto",
                       "Lo aprendido se guarda por situación (evento @ cruce / sentido): se comparan planes en las mismas condiciones",
                       "Con probabilidad ε prueba el plan MENOS USADO en esa situación para medirlo con datos reales "
                       f"(ε baja ×{K.EPSILON_PLANES_DECAIMIENTO} por alerta hasta {K.EPSILON_PLANES_MINIMO}); nunca en alertas de "
                       f"Seguridad, y no prueba 'Solo avisar' con prioridad {' o '.join(K.NO_EXPLORAR_SOLO_AVISAR_EN)}"],
            "parametros": {"duracion_plan_s": SEGUNDOS_PLAN, "factor_trafico": K.FACTOR_TRAFICO, "peso_costo": K.PESO_COSTO,
                           "peso_modelo": K.PESO_MODELO, "funcion_costo": K.FUNCION_COSTO, "k_rutas": K.K_RUTAS,
                           "alfa_aprendizaje": K.ALFA_PLANES, "linea_base_s": K.SEGUNDOS_LINEA_BASE,
                           "epsilon_planes": self.experiencia.epsilon},
            "estado": {"catalogo": catalogo, "experiencia": self.experiencia.entradas(),
                       "ultima_decision": self.ultima, "activas": list(self.activas.values()),
                       "aprendido": self.aprendido[-10:]},
        })

    class Atender(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            alerta = json.loads(msg.body)
            log("Coordinador", f"recibí {alerta['id']} ({alerta['tipo']}, {alerta['prioridad']})")
            # Lanza un comportamiento por incidente; solo recibe las respuestas de su conversación
            t = plantilla("inform")
            t.thread = alerta["id"]
            self.agent.add_behaviour(AgenteCoordinador.ResponderAlerta(alerta), t)

    class Terminar(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            d = json.loads(msg.body)
            ids = [d["alerta"]] if d.get("alerta") else [i for i, a in self.agent.activas.items()
                                                          if a["cruce"] == d.get("ubicacion")]
            for i in ids:
                if i in self.agent.cierres:
                    log("Coordinador", f"{i}: el operador dio por terminada la contingencia")
                    self.agent.cierres[i].set()
            if not ids:
                log("Coordinador", "no hay contingencia activa que terminar")

    class ResponderAlerta(OneShotBehaviour):
        def __init__(self, alerta):
            super().__init__()
            self.alerta = alerta

        async def esperar(self, accion, segundos=10):
            r = await self.receive(timeout=segundos)
            if r and r.get_metadata("action") == accion:
                return json.loads(r.body)
            return None

        async def cola_media(self, cruces, segundos, hilo):
            await self.send(mensaje("consultor", "query-ref", "consultarColaMedia",
                                    {"cruces": cruces, "segundos": segundos}, hilo))
            r = await self.esperar("ColaMedia")
            return r if r and r.get("muestras") else None

        async def run(self):
            a, ag = self.alerta, self.agent
            cruce, hilo = a.get("ubicacion", "cruce1"), a["id"]
            # 1) Consultar estado vial (el Consultor lo mide con los sensores de cola)
            await self.send(mensaje("consultor", "query-ref", "consultarEstadoVial", {"sector": cruce}, hilo))
            estado = await self.esperar("EstadoTrafico") or {"nivel": "desconocido"}
            log("Coordinador", f"estado vial en {cruce}: {estado['nivel']}" +
                (f" (cola A={estado['A']:.0f}, B={estado['B']:.0f})" if "A" in estado else ""))
            huida = None
            if a.get("tipo") == "Seguridad":      # Caso 2: ¿por dónde huiría? (Predictor, k-rutas de Yen)
                await self.send(mensaje("predictor", "request", "predecirRutaHuida", {"ubicacion": cruce}, hilo))
                huida = await self.esperar("Ruta", 20)
                if huida:
                    a["ruta_huida"] = huida
                    log("Coordinador", "rutas probables de huida desde " + cruce + ": " + " | ".join(
                        f"{' → '.join(r['calles'])} (p={r['probabilidad']}, cruces {r['cruces']})"
                        for r in huida.get("rutas", [huida])))

            # 2) Generar planes, evaluarlos con el modelo matemático y elegir el de mayor utilidad
            factor = K.FACTOR_TRAFICO.get(estado["nivel"], K.FACTOR_TRAFICO_DESCONOCIDO)
            planes = generar_planes(a, cruce, ag.experiencia)
            try:
                modelo = await asyncio.to_thread(evaluar_con_modelo, a, cruce, planes, estado["nivel"])
            except Exception as error:                         # el modelo nunca debe tumbar al agente
                log("Coordinador", f"AVISO: modelo de rutas no disponible ({error}); decido solo con lo aprendido")
                modelo = None
            for p in planes:
                if modelo:   # utilidad = (modelo matemático + lo aprendido midiendo) x tráfico − costo
                    mezcla = K.PESO_MODELO * p["beneficio"] + (1 - K.PESO_MODELO) * p["impacto"]
                    p["utilidad"] = round(mezcla * factor - K.PESO_COSTO * p["costo"], 2)
                else:
                    p["utilidad"] = round(p["impacto"] * factor - K.PESO_COSTO * p["costo"], 2)
            optimo = max(planes, key=lambda p: p["utilidad"])
            situacion = optimo["situacion"]
            if modelo:
                log("Coordinador", f"modelo de tráfico (equilibrio Frank-Wolfe, {modelo['demanda_vh']:.0f} viajes/h): " + "; ".join(
                    f"{p['nombre']}: TSTT {p['tstt']:.1f} veh·h, viaje medio {p['viaje_min']:.2f} min" for p in planes))
            log("Coordinador", f"situación {situacion} · utilidades: " + ", ".join(
                f"{p['nombre']}={p['utilidad']}" + (f" (medido en {p['veces']} usos)" if p["veces"] else " (sin medir)")
                for p in planes))
            # exploración: a veces prueba el plan menos usado en esta situación para medirlo con datos reales
            mejor, explorado, eps = optimo, False, ag.experiencia.epsilon
            if a.get("tipo") != "Seguridad" and random.random() < eps:
                candidatos = [p for p in planes if p is not optimo and not
                              (p["clave"] == "solo_avisar" and a.get("prioridad") in K.NO_EXPLORAR_SOLO_AVISAR_EN)]
                if candidatos:
                    menos = min(p["veces"] for p in candidatos)
                    mejor = random.choice([p for p in candidatos if p["veces"] == menos])
                    explorado = True
            ag.experiencia.decaer()
            if explorado:
                log("Coordinador", f"exploro (ε={eps:.2f}): pruebo '{mejor['nombre']}' (usado {mejor['veces']} veces en "
                                   f"esta situación) en vez de '{optimo['nombre']}', para medirlo con datos reales")
            log("Coordinador", f"plan elegido: {mejor['nombre']}" + (f" [{mejor['detalle']}]" if mejor.get("detalle") else "")
                + (f" [el modelo ajustó sus semáforos: {mejor['ajuste_modelo']}]" if mejor.get("ajuste_modelo") else ""))
            # rutas alternativas (Yen) con los tiempos del equilibrio del plan elegido
            sugeridas = {}
            if modelo:
                try:
                    sugeridas = await asyncio.to_thread(rutas_sugeridas, modelo["resultados"][mejor["clave"]])
                except Exception as error:
                    log("Coordinador", f"AVISO: no pude calcular rutas ({error})")
            textos = {"salir": "para salir de Bocagrande", "entrar_centro": "para entrar desde el Centro",
                      "entrar_norte": "para entrar por la Carrera 1"}
            frases = [f"{textos[t]}: {' → '.join(rs[0]['calles'])} (~{rs[0]['minutos']:g} min)"
                      for t, rs in sugeridas.items() if rs]
            if frases:
                log("Coordinador", "rutas sugeridas (Yen, k=" + str(K.K_RUTAS) + "): " + " | ".join(frases))
                mejor = {**mejor, "aviso": mejor["aviso"] + " Ruta sugerida " + "; ".join(frases) + "."}
            columnas = ("clave", "nombre", "impacto", "costo", "utilidad", "veces", "tstt", "viaje_min", "beneficio", "semaforos",
                        "detalle", "ajuste_modelo")
            ag.ultima = {"alerta": hilo, "cruce": cruce, "situacion": situacion, "nivel": estado["nivel"], "factor": factor,
                         "elegido": mejor["clave"], "optimo": optimo["clave"], "explorado": explorado,
                         "modelo": {"escala": modelo["escala"], "demanda_vh": modelo["demanda_vh"]} if modelo else None,
                         "rutas": {t: [{k: x[k] for k in ("calles", "minutos", "km")} for x in rs] for t, rs in sugeridas.items()},
                         "planes": [{k: p.get(k) for k in columnas} for p in planes]}

            # línea base para aprender: cola media de todo el sector justo antes de actuar
            cruces_plan = sorted(set(mejor["semaforos"]) | {cruce})
            antes = await self.cola_media([], K.SEGUNDOS_LINEA_BASE, hilo)

            # 3) Ordenar al Semafórico y esperar resultado
            sentido = {"contraflujo_salida": "salida", "contraflujo_entrada": "entrada"}.get(mejor["clave"])
            plan = {"plan": mejor["clave"], "nombre": mejor["nombre"], "semaforos": mejor["semaforos"],
                    "alerta": hilo, "cruce": cruce, "evento": a.get("tipo_evento"),
                    "sentido": a.get("sentido") or sentido, "duracion": SEGUNDOS_PLAN, "rutas": sugeridas}
            if mejor["clave"] == "cerco":
                plan["persecucion"] = True        # el Semafórico mantiene estos modos (la DQN no los cambia)
                if huida and huida.get("p"):     # el gemelo 3D dibuja la ruta de huida
                    plan["rutas"] = {"huida": [{k: huida[k] for k in ("calles", "minutos", "p") if k in huida}]}
            await self.send(mensaje("semaforico", "request", "activarPlan", plan, hilo))
            resultado = await self.esperar("ResultadoAccion") or {"estado": "sin respuesta"}
            log("Coordinador", f"resultado del Semafórico: {resultado}")

            # 4) Pedir al Comunicador que avise (Seguridad: solo a la Policía)
            await self.send(mensaje("comunicador", "request", "comunicar",
                                    {"receptor": mejor["receptor"], "alerta": a, "aviso": mejor["aviso"],
                                     "plan": mejor["nombre"], "duracion": SEGUNDOS_PLAN}, hilo))

            # 5) Mantener el plan SEGUNDOS_PLAN (o hasta que el operador lo termine) y restaurar
            log("Coordinador", f"{hilo}: plan activo {SEGUNDOS_PLAN:g} s ({mejor['nombre']})")
            ag.cierres[hilo] = asyncio.Event()
            ag.activas[hilo] = {"alerta": hilo, "cruce": cruce, "plan": mejor["nombre"], "desde": f"{datetime.now():%H:%M:%S}"}
            ag.mente()
            inicio = time.monotonic()
            try:
                await asyncio.wait_for(ag.cierres[hilo].wait(), timeout=SEGUNDOS_PLAN)
                motivo = "terminado por el operador"
            except asyncio.TimeoutError:
                motivo = "tiempo cumplido"

            # 6) Aprender: ¿cuánto sirvió el plan? (cola media durante el plan vs antes)
            durante = await self.cola_media([], max(10, time.monotonic() - inicio), hilo)
            if antes and durante:
                viejo, nuevo, efecto = ag.experiencia.actualizar(mejor["clave"], situacion, antes["media"], durante["media"])
                log("Coordinador", f"{hilo}: aprendí del plan '{mejor['nombre']}' en {situacion}: cola {antes['media']:.1f} -> "
                                   f"{durante['media']:.1f} (efecto {efecto:+.2f}); impacto {viejo:.2f} -> {nuevo:.2f}")
                ag.aprendido.append({"alerta": hilo, "plan": mejor["nombre"], "situacion": situacion,
                                     "antes": round(antes["media"], 1),
                                     "durante": round(durante["media"], 1), "efecto": round(efecto, 2),
                                     "impacto": [viejo, nuevo]})
            else:
                log("Coordinador", f"{hilo}: sin datos de sensores, no puedo medir el efecto del plan")

            await self.send(mensaje("semaforico", "request", "restaurarPlanNormal", {**plan, "cruces": cruces_plan}, hilo))
            await self.esperar("ResultadoAccion")
            ag.cierres.pop(hilo, None)
            ag.activas.pop(hilo, None)
            ag.mente()
            log("Coordinador", f"{hilo} cerrado ({motivo}): semáforos vuelven a plan normal")


# =====================================================================
# 4. AGENTE CONSULTOR  (reactivo basado en modelos): modelo del tráfico a partir de los sensores
# =====================================================================
class AgenteConsultor(Agent):
    async def setup(self):
        self.cache = {}                                   # (acción, sector) -> (instante, respuesta)
        self.historial = {}                               # cruce -> deque[(instante, A, B)]
        self.ultimo = {}                                  # cruce -> (instante, A, B)
        self.registro, self.registro_mtime = [], None     # Caso 2: vectores de las personas solicitadas (con consentimiento)
        self.rostros = {"consultas": 0, "coincidencias": 0, "descartados": 0}
        self.mqtt = escuchar_sensores("Consultor", "sma-consultor", self.al_leer)
        self.add_behaviour(self.Responder(), plantilla("query-ref"))
        self.add_behaviour(self.Mente(period=5))

    def al_leer(self, cruce, a, b):
        ahora = time.monotonic()
        self.ultimo[cruce] = (ahora, a, b)
        h = self.historial.setdefault(cruce, deque())
        h.append((ahora, a, b))
        while h and ahora - h[0][0] > K.HISTORIAL_SEGUNDOS:
            h.popleft()

    def personas(self):
        """Lee vision/registro/registro.json (solo vectores, sin fotos) y lo recarga si cambió."""
        try:
            mtime = REGISTRO_ROSTROS.stat().st_mtime
        except OSError:
            self.registro, self.registro_mtime = [], None
            return self.registro
        if mtime != self.registro_mtime:
            datos = json.loads(REGISTRO_ROSTROS.read_text(encoding="utf-8"))
            self.registro = [(x["alias"], np.asarray(x["vector"], dtype=float)) for x in datos.get("personas", [])]
            self.registro_mtime = mtime
            log("Consultor", f"registro de personas solicitadas: {len(self.registro)} (con consentimiento)")
        return self.registro

    def comparar(self, vector):
        """Similitud coseno con cada persona registrada; el vector consultado NO se guarda (Ley 1581 de 2012)."""
        v = np.asarray(vector, dtype=float)
        v = v / (np.linalg.norm(v) or 1.0)
        return max(((alias, float(v @ (r / (np.linalg.norm(r) or 1.0)))) for alias, r in self.personas()),
                   key=lambda x: x[1], default=(None, 0.0))

    def nivel(self, cola):
        return "alto" if cola >= K.UMBRAL_NIVEL["alto"] else "medio" if cola >= K.UMBRAL_NIVEL["medio"] else "bajo"

    def estado_vial(self, cruce):
        u = self.ultimo.get(cruce)
        if not u or time.monotonic() - u[0] > 15:
            return {"sector": cruce, "nivel": "desconocido"}, "sin sensores"
        return {"sector": cruce, "nivel": self.nivel(u[1] + u[2]), "A": u[1], "B": u[2]}, "sensores de cola"

    def cola_media(self, cruces, segundos):
        """Suma de las colas medias de esos cruces (lista vacía = todo el sector)."""
        ahora, sumas = time.monotonic(), []
        for c in (cruces or sorted(self.historial)):
            datos = [a + b for t, a, b in self.historial.get(c, ()) if ahora - t <= segundos]
            if datos:
                sumas.append(sum(datos) / len(datos))
        return {"media": sum(sumas), "muestras": len(sumas)}

    class Mente(PeriodicBehaviour):
        async def run(self):
            ag = self.agent
            niveles = {}
            for c in sorted(ag.ultimo):
                e, _ = ag.estado_vial(c)
                niveles[c] = e
            publicar_mente("Consultor", {
                "tipo": "Reactivo basado en modelos",
                "rol": "Mantiene un modelo del tráfico (colas por acceso) y responde consultas con caché.",
                "percibe": "sma/sensores/<cruce> (colas A/B medidas por el gemelo digital); query-ref",
                "actua": "inform EstadoTrafico / ColaMedia",
                "codigo": ["agentes/sma_agentes.py · AgenteConsultor", "agentes/conocimiento.py · Consultor"],
                "reglas": [f"nivel = alto SI cola ≥ {K.UMBRAL_NIVEL['alto']}; medio SI cola ≥ {K.UMBRAL_NIVEL['medio']}; si no, bajo",
                           f"SI la misma consulta se repite en menos de {K.CACHE_SEGUNDOS} s ENTONCES responder desde caché",
                           "SI no hay lecturas en 15 s ENTONCES nivel desconocido",
                           f"SI similitud coseno del rostro ≥ {K.UMBRAL_SIMILITUD_ROSTRO} ENTONCES posible coincidencia; "
                           "SI no ENTONCES descartar el rostro sin guardarlo"],
                "parametros": {"umbral_nivel": K.UMBRAL_NIVEL, "cache_s": K.CACHE_SEGUNDOS, "historial_s": K.HISTORIAL_SEGUNDOS,
                               "umbral_rostro": K.UMBRAL_SIMILITUD_ROSTRO},
                "estado": {"niveles": niveles, "personas_registradas": len(ag.personas()), "rostros": ag.rostros},
            })

    class Responder(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            ag, accion = self.agent, msg.get_metadata("action")
            pedido = json.loads(msg.body)
            if accion == "consultarPersonaSolicitada":
                alias, similitud = ag.comparar(pedido.get("embedding") or [])
                ag.rostros["consultas"] += 1
                coincide = alias is not None and similitud >= K.UMBRAL_SIMILITUD_ROSTRO
                respuesta = {"coincide": coincide, "similitud": round(similitud, 3), "camara": pedido.get("camara"),
                             "ubicacion": pedido.get("ubicacion")}
                if coincide:
                    ag.rostros["coincidencias"] += 1
                    respuesta["persona"] = {"alias": alias}
                    log("Consultor", f"cámara {pedido.get('camara')}: posible coincidencia (similitud {similitud:.2f} ≥ "
                                     f"{K.UMBRAL_SIMILITUD_ROSTRO})")
                else:
                    ag.rostros["descartados"] += 1       # no se registra nada del rostro: se descarta
                await self.send(responder(msg, "Coincidencia", respuesta))
                return
            if accion == "consultarColaMedia":
                r = ag.cola_media(pedido.get("cruces", []), float(pedido.get("segundos", 60)))
                log("Consultor", f"cola media de {', '.join(pedido.get('cruces') or ['todo el sector'])} en {float(pedido.get('segundos', 60)):.0f} s: "
                                 f"{r['media']:.1f}" if r["muestras"] else "sin lecturas de sensores para la cola media")
                await self.send(responder(msg, "ColaMedia", r))
                return
            clave = (accion, pedido.get("sector"))
            guardado = ag.cache.get(clave)
            if guardado and time.monotonic() - guardado[0] < K.CACHE_SEGUNDOS:
                estado, origen = guardado[1], "caché"
            else:
                estado, origen = ag.estado_vial(pedido.get("sector"))
                ag.cache[clave] = (time.monotonic(), estado)
            log("Consultor", f"estado vial de {pedido.get('sector')} desde {origen}: {estado['nivel']}")
            await self.send(responder(msg, "EstadoTrafico", estado))


# =====================================================================
# 5. AGENTE PREDICTOR  (basado en modelos + utilidad) - listo para el Caso 2
# =====================================================================
class AgentePredictor(Agent):
    """Predice cómo van a evolucionar las colas (red neuronal T-GCN entrenada) y la ruta probable de huida (Caso 2)."""

    async def setup(self):
        self.modelo = PredictorColas() if NEURONAL else None
        self.lecturas = {}          # cruce -> [(A, B)] del intervalo
        self.colas = {}             # cruce -> (A, B) media del último intervalo
        self.ultima = None          # última predicción
        self.pendientes = deque(maxlen=60)   # (instante objetivo, cruce, total predicho) para medir el error real
        self.errores = deque(maxlen=200)
        self.comparacion = deque(maxlen=40)  # (hora, real, predicho) del cruce5 para la gráfica del panel
        self.avisado = {}
        self.sensores = escuchar_sensores("Predictor", "sma-predictor-sensores",
                                          lambda c, a, b: self.lecturas.setdefault(c, []).append((a, b)))
        modo = self.modelo.info["modelo"] if self.modelo else f"heurístico (sin PyTorch: {_ERROR_NEURONAL[:60]})"
        log("Predictor", f"modelo de predicción de colas: {modo}")
        self.mente()
        self.add_behaviour(self.Predecir(), plantilla("request", "predecirRutaHuida"))
        self.add_behaviour(self.Anticipar(period=K.INTERVALO_CONTROL))

    def mente(self):
        mae = round(float(sum(self.errores) / len(self.errores)), 2) if self.errores else None
        info = self.modelo.info if self.modelo else {"modelo": "heurístico"}
        publicar_mente("Predictor", {
            "tipo": "Híbrido: basado en modelos (red neuronal T-GCN) + utilidad",
            "rol": "Anticipa las colas de los 5 cruces (36 s) para el Semafórico; predice la ruta de huida (Caso 2).",
            "percibe": "sma/sensores/<cruce>; request predecirRutaHuida",
            "actua": "inform PrediccionColas -> Semafórico (cada 12 s); inform Ruta",
            "codigo": ["modelos/tgcn.py · TGCN (GCN + GRU)", "modelos/predictor_colas.py", "modelos/entrenar_predictor.py",
                       "agentes/rutas.py · Yen (ruta de huida)"],
            "reglas": ["Cada 12 s: cola media por acceso de los 5 cruces -> ventana de 15 pasos (3 min) -> T-GCN -> colas en 12, 24 y 36 s",
                       "La T-GCN mezcla cada cruce con sus vecinos (convolución de grafo) y con su historia (GRU)",
                       "Entrenada con colas de SUMO (demanda horaria real SIMM + accidentes reales DATT); la misma arquitectura "
                       "se validó con flujo real de 156 estaciones de Medellín (modelos/entrenados/metricas_simm.json)",
                       "SI no hay modelo o historia suficiente ENTONCES responde con la cola actual (heurístico)",
                       f"SI predice que una cola sube {K.AVISO_CRECIMIENTO_COLA} veh o más ENTONCES lo anuncia en la bitácora",
                       "Ruta de huida: k-rutas más rápidas (Yen) desde el lugar hacia la salida de Bocagrande"],
            "parametros": info,
            "estado": {"prediccion": self.ultima, "error_medio_abs_veh": mae, "comparacion_cruce5": list(self.comparacion),
                       "colas": {c: [round(a, 1), round(b, 1)] for c, (a, b) in self.colas.items()}},
        })

    class Anticipar(PeriodicBehaviour):
        async def run(self):
            ag = self.agent
            for c, l in list(ag.lecturas.items()):
                if l:
                    ag.colas[c] = (sum(x for x, _ in l) / len(l), sum(y for _, y in l) / len(l))
                    ag.lecturas[c] = []
            if not ag.colas:
                return
            ahora = time.monotonic()
            # error real de lo que se predijo hace 36 s
            while ag.pendientes and ag.pendientes[0][0] <= ahora:
                _, c, pred = ag.pendientes.popleft()
                if c in ag.colas:
                    real = sum(ag.colas[c])
                    ag.errores.append(abs(real - pred))
                    if c == "cruce5":
                        ag.comparacion.append((f"{datetime.now():%H:%M:%S}", round(real, 1), round(pred, 1)))
            hora = datetime.now().hour + datetime.now().minute / 60
            pred = None
            if ag.modelo is not None and ag.modelo.disponible:
                ag.modelo.agregar(ag.colas, hora)
                pred = await asyncio.to_thread(ag.modelo.predecir)
            fuente = "T-GCN" if pred else "heurístico"
            cruces = pred["cruces"] if pred else dict(ag.colas)
            ag.ultima = {"fuente": fuente, "horizonte_s": 36,
                         "cruces": {c: [round(a, 1), round(b, 1)] for c, (a, b) in cruces.items()}}
            if pred:
                for c, (a, b) in cruces.items():
                    ag.pendientes.append((ahora + 36, c, a + b))
                    actual = sum(ag.colas.get(c, (0, 0)))
                    if a + b - actual >= K.AVISO_CRECIMIENTO_COLA and ahora - ag.avisado.get(c, 0) > 60:
                        ag.avisado[c] = ahora
                        log("Predictor", f"la T-GCN anticipa que la cola de {c} sube de {actual:.1f} a {a + b:.1f} veh "
                                         f"en ~36 s (A={a:.1f}, B={b:.1f}); aviso al Semafórico")
            await self.send(mensaje("semaforico", "inform", "PrediccionColas", ag.ultima))
            ag.mente()

    class Predecir(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            pedido = json.loads(msg.body)
            try:
                ruta = await asyncio.to_thread(ruta_huida, pedido.get("ubicacion"))
            except Exception as error:   # el modelo de rutas no debe tumbar al agente
                log("Predictor", f"AVISO: sin modelo de rutas ({error}); uso la ruta por defecto")
                ruta = dict(K.RUTA_HUIDA_POR_DEFECTO)
            log("Predictor", f"ruta probable desde {pedido.get('ubicacion')}: {ruta['cruces']} (confianza {ruta['confianza']})")
            await self.send(responder(msg, "Ruta", ruta))


def ruta_huida(ubicacion):
    """Caso 2: ¿por dónde huiría quien fue visto en `ubicacion`? Modelo de elección de ruta del conductor:
    k rutas más rápidas hacia la salida de Bocagrande (Yen, con los tiempos de la red con poco tráfico) y probabilidad
    de cada una por logit sobre su tiempo (Dial 1971). Para cada ruta: cruces semaforizados en orden, acceso por el que
    llega (A/B, para que el Semafórico lo retenga) y segundos hasta cada cruce. Punto de intercepción sugerido = último
    cruce de la ruta más probable (llega más tarde y ya retrasado por los semáforos anteriores)."""
    import math
    R = rutas.red()
    origen = R.cruces[ubicacion]["nodos"][0] if ubicacion in R.cruces else R.cruces["cruce1"]["nodos"][0]
    base = R.evaluar(escala=0.3)
    candidatas = R.rutas(base, origen, R.geo["nodoSalida"], k=K.K_RUTAS)
    if not candidatas:
        return dict(K.RUTA_HUIDA_POR_DEFECTO)
    minimo = min(c["minutos"] for c in candidatas)
    pesos = [math.exp(-(c["minutos"] - minimo) / K.DISPERSION_RUTAS_MIN) for c in candidatas]
    total = sum(pesos)
    opciones = []
    for c, w in sorted(zip(candidatas, pesos), key=lambda x: -x[1]):
        llegadas, accesos, reloj = {}, {}, 0.0
        if ubicacion in R.cruces:
            llegadas[ubicacion] = 0
        for i in c["aristas"]:
            reloj += float(base["tiempo"][i])
            ctl = R.E[i]["control"]
            if ctl and ctl[0] not in llegadas:
                llegadas[ctl[0]], accesos[ctl[0]] = round(reloj), ctl[1]
        opciones.append({"cruces": list(llegadas), "llegadas": llegadas, "accesos": accesos, "calles": c["calles"],
                         "minutos": c["minutos"], "probabilidad": round(w / total, 2),
                         "p": [[round(v, 1) for v in q] for i in c["aristas"] for q in R.E[i]["p"]]})
    mejor = opciones[0]
    ultimo = mejor["cruces"][-1] if mejor["cruces"] else ubicacion
    return {"cruces": mejor["cruces"], "confianza": mejor["probabilidad"], "calles": mejor["calles"],
            "minutos": mejor["minutos"], "p": mejor["p"],
            "rutas": [{k: o[k] for k in ("cruces", "llegadas", "accesos", "calles", "minutos", "probabilidad")}
                      for o in opciones],
            "intercepcion": {"cruce": ultimo, "segundos": mejor["llegadas"].get(ultimo, 0)}}


# =====================================================================
# 6. AGENTE SEMAFÓRICO  (basado en objetivos + aprendizaje por refuerzo)
#    - Obedece los planes del Coordinador (activarPlan / restaurarPlanNormal).
#    - Por su cuenta, cada INTERVALO_CONTROL s mira las colas de cada acceso y decide el modo
#      (normal / prioridad_a / prioridad_b) con su tabla Q, que corrige con la recompensa observada.
# =====================================================================
ORDEN_ESP32 = {  # acción del agente -> modo (activarPlan trae un modo por cruce)
    "abrirCorredorEmergencia": "corredor",
    "restaurarPlanNormal": "normal",
    "activarCerco": "cerco",
}


class AgenteSemaforico(Agent):
    async def setup(self):
        self.objetivo = "Fluidez"
        self.q = QSemaforo(olvidar=OLVIDAR)
        self.modo = {}          # cruce -> modo publicado por última vez
        self.sugerido = {}      # cruce -> modo que pide el plan del Coordinador (las colas pueden pesar más)
        self.bloqueado = {}     # cruce -> modo fijo del plan (cerco, peatonal): no se adapta
        self.lecturas = {}      # cruce -> [(A, B)] desde la última decisión
        self.previo = {}        # cruce -> (estado, acción, cola total, cambió) de la decisión anterior
        self.decision = {}      # cruce -> resumen de la última decisión (para la página /agentes)
        self.colas = {}         # cruce -> (A, B) media del último intervalo (para el estado de la DQN)
        self.colas_previas = {}
        self.prediccion = None  # última predicción del Agente Predictor (T-GCN)
        self.vecinos = vecinos_cruces()
        self.dqn, self.dqn_info = (cargar_politica(RUTA_DQN) if NEURONAL and K.USAR_DQN else (None, None))
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-semaforico")
        try:
            self.mqtt.connect(BROKER_MQTT, PUERTO_MQTT)
            self.mqtt.loop_start()
            self.conectado = True
        except OSError:
            self.conectado = False
            log("Semaforico", "AVISO: sin Mosquitto, solo simulo los semáforos")
        self.sensores = escuchar_sensores("Semaforico", "sma-semaforico-sensores",
                                          lambda c, a, b: self.lecturas.setdefault(c, []).append((a, b)))
        controlador = (f"DQN neuronal (entrenada {self.dqn_info.get('episodios', '?')} episodios en SUMO) + tabla Q de respaldo"
                       if self.dqn else "tabla Q (sin DQN entrenada)")
        log("Semaforico", f"control adaptativo cada {K.INTERVALO_CONTROL} s con {controlador}; tabla Q: {self.q.pasos} "
                          f"decisiones, epsilon={self.q.epsilon:.3f}")
        self.add_behaviour(self.Obedecer(), plantilla("request"))
        self.add_behaviour(self.RecibirPrediccion(), plantilla("inform", "PrediccionColas"))
        self.add_behaviour(self.ControlAdaptativo(period=K.INTERVALO_CONTROL))

    def ordenar(self, cruce, modo):
        if modo and self.conectado:
            self.mqtt.publish(TEMA_SEMAFORO.format(cruce=cruce), modo)
        self.modo[cruce] = modo

    def mente(self):
        recientes = self.q.recompensas[-50:]
        publicar_mente("Semaforico", {
            "tipo": ("Basado en objetivos + aprendizaje por refuerzo profundo (DQN) con tabla Q de respaldo" if self.dqn
                     else "Basado en objetivos + aprendizaje por refuerzo (Q-learning)"),
            "controlador": "DQN" if self.dqn else "tabla Q",
            "dqn": ({"rasgos": RASGOS_DQN, "episodios": self.dqn_info.get("episodios"),
                     "historia": (self.dqn_info.get("historia") or [])[-60:]} if self.dqn else None),
            "rol": "Obedece los planes del Coordinador y, por su cuenta, da prioridad al acceso con más cola.",
            "percibe": "sma/sensores/<cruce> (cola en A y B); request activarPlan / restaurarPlanNormal",
            "actua": "sma/semaforo/<cruce> = normal | prioridad_a | prioridad_b | peatonal | cerco",
            "codigo": ["agentes/sma_agentes.py · AgenteSemaforico.ControlAdaptativo",
                       "agentes/aprendizaje.py · QSemaforo", "agentes/conocimiento.py · Semafórico"],
            "reglas": [f"DQN (red 13 → 64 → 64 → 3, Double DQN entrenada en SUMO): estado = colas A/B, su tendencia, la "
                       "PREDICCIÓN de la T-GCN (36 s), cola de los 2 cruces vecinos, modo actual y hora -> Q(normal), Q(prioridad_a), Q(prioridad_b)",
                       f"Con la DQN: elige el mayor Q (+{K.DQN_BONO_PLAN} si el plan lo sugiere); cambia solo si mejora más de {K.DQN_MARGEN}",
                       "La tabla Q sigue aprendiendo en paralelo (Q-learning es off-policy) y toma el control si falta la DQN",
                       f"Cada {K.INTERVALO_CONTROL} s: estado = (cubeta cola A, cubeta cola B), cubetas {K.CUBETAS_COLA}",
                       "Elige el modo con mayor Q(estado, modo) (+ bono si el plan del Coordinador lo sugiere);"
                       f" con probabilidad ε explora otro modo",
                       "Recompensa = cola total antes − cola total después − penalización si cambió de modo",
                       f"Q ← Q + {K.Q_ALFA} × (recompensa + {K.Q_GAMMA} × max Q(siguiente) − Q)",
                       "Conocimiento inicial: Q(prioridad hacia el acceso más cargado) = diferencia de cubetas − "
                       f"{K.COSTO_PRIORIDAD_SIN_COLA}; Q(normal) = 0",
                       f"Solo cambia de modo si la mejor opción supera a la actual por más de {K.MARGEN_CAMBIO} (histéresis)",
                       "Con el cruce vacío (a0-b0) queda en normal (o en el modo del plan) y no explora",
                       f"Modos {K.MODOS_BLOQUEADOS} que ordena el Coordinador no se cambian por cuenta propia"],
            "parametros": {"intervalo_s": K.INTERVALO_CONTROL, "cubetas": K.CUBETAS_COLA, "alfa": K.Q_ALFA,
                           "gamma": K.Q_GAMMA, "epsilon": round(self.q.epsilon, 4), "epsilon_min": K.EPSILON_MINIMO,
                           "penalizacion_cambio": K.PENALIZACION_CAMBIO, "bono_plan": K.BONO_PLAN,
                           "margen_cambio": K.MARGEN_CAMBIO},
            "estado": {"decisiones_aprendidas": self.q.pasos,
                       "recompensa_media_50": round(sum(recientes) / len(recientes), 2) if recientes else None,
                       "recompensas": recientes, "cruces": self.decision, "q": self.q.q, "visitas": self.q.visitas,
                       "prediccion": self.prediccion},
        })

    class RecibirPrediccion(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if msg:
                self.agent.prediccion = json.loads(msg.body)

    def elegir_dqn(self, cruce, anterior):
        """Política neuronal con la capa de seguridad del agente (bono del plan, histéresis, cruce vacío)."""
        pred = self.prediccion if self.prediccion and self.prediccion.get("fuente") == "T-GCN" else None
        pred_cruces = {c: tuple(v) for c, v in pred["cruces"].items()} if pred else None
        s = vector_estado(cruce, self.colas, self.colas_previas, anterior, pred_cruces,
                          datetime.now().hour + datetime.now().minute / 60, self.vecinos.get(cruce, []))
        with torch.no_grad():
            q = self.dqn(torch.as_tensor(s).unsqueeze(0))[0].numpy()
        sugerido = self.sugerido.get(cruce)
        puntajes = {m: float(v) + (K.DQN_BONO_PLAN if m == sugerido else 0.0) for m, v in zip(MODOS_DQN, q)}
        a, b = self.colas[cruce]
        if a < 0.5 and b < 0.5:
            return (sugerido or "normal"), "cruce vacío: " + ("modo del plan" if sugerido else "sin cola no hay a quién dar prioridad"), puntajes
        accion = max(puntajes, key=puntajes.get)
        motivo = "DQN: mayor Q" + (" (con predicción T-GCN)" if pred else " (sin predicción aún)")
        if sugerido and accion != sugerido:
            motivo = f"DQN: las colas pesan más que el plan (sugería {sugerido})"
        if anterior in puntajes and accion != anterior and puntajes[accion] - puntajes[anterior] < K.DQN_MARGEN:
            return anterior, f"se mantiene {anterior}: {accion} solo mejora {puntajes[accion] - puntajes[anterior]:.2f} (< {K.DQN_MARGEN})", puntajes
        return accion, motivo, puntajes

    class ControlAdaptativo(PeriodicBehaviour):
        async def run(self):
            ag = self.agent
            nuevas = {}
            for cruce, lecturas in list(ag.lecturas.items()):
                if lecturas:
                    nuevas[cruce] = (sum(x for x, _ in lecturas) / len(lecturas), sum(y for _, y in lecturas) / len(lecturas))
                    ag.lecturas[cruce] = []
            if not nuevas:
                return
            ag.colas_previas = dict(ag.colas)
            ag.colas.update(nuevas)
            for cruce, (a, b) in nuevas.items():
                total = a + b
                estado = ag.q.estado(a, b)
                info = {"A": round(a, 1), "B": round(b, 1), "estado": estado, "modo": ag.modo.get(cruce, "normal"),
                        "sugerido": ag.sugerido.get(cruce), "bloqueado": ag.bloqueado.get(cruce)}
                # la tabla Q aprende de lo que pasó con la decisión anterior (sea de la DQN o suya: Q-learning es off-policy)
                previo = ag.previo.pop(cruce, None)
                if previo:
                    est0, acc0, total0, cambio0 = previo
                    recompensa = (total0 - total) - (K.PENALIZACION_CAMBIO if cambio0 else 0.0)
                    q0, q1 = ag.q.aprender(cruce, est0, acc0, recompensa, estado)
                    info["aprendizaje"] = {"estado": est0, "accion": acc0, "recompensa": round(recompensa, 2),
                                           "q_antes": round(q0, 2), "q_despues": round(q1, 2)}
                if cruce in ag.bloqueado:
                    info["motivo"] = f"modo {ag.bloqueado[cruce]} fijado por el plan del Coordinador"
                    ag.decision[cruce] = info
                    continue
                anterior = ag.modo.get(cruce, "normal")
                if ag.dqn is not None:
                    accion, motivo, puntajes = ag.elegir_dqn(cruce, anterior)
                    info["controlador"] = "DQN"
                else:
                    accion, motivo, puntajes = ag.q.elegir(cruce, estado, ag.sugerido.get(cruce), anterior)
                    info["controlador"] = "tabla Q"
                if ag.prediccion and cruce in ag.prediccion.get("cruces", {}):
                    info["prediccion"] = ag.prediccion["cruces"][cruce]
                cambio = accion != anterior
                if cambio:
                    ag.ordenar(cruce, accion)
                    pred = f"; predicción 36 s A={info['prediccion'][0]:.1f} B={info['prediccion'][1]:.1f}" if info.get("prediccion") else ""
                    log("Semaforico", f"{cruce}: cola A={a:.1f} B={b:.1f} ({estado}{pred}) -> {accion} [{motivo}; "
                                      + ", ".join(f"{k}={v:.2f}" for k, v in puntajes.items()) + "]")
                ag.previo[cruce] = (estado, accion, total, cambio)
                ag.decision[cruce] = {**info, "modo": accion, "motivo": motivo, "cambio": cambio,
                                      "puntajes": {k: round(v, 2) for k, v in puntajes.items()}}
            ag.q.guardar()
            ag.mente()

    class Obedecer(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            ag = self.agent
            accion = msg.get_metadata("action")
            datos = json.loads(msg.body)
            if accion == "activarPlan":          # el plan trae un modo por cruce
                ordenes = datos.get("semaforos", {})
            else:
                cruces = datos.get("ruta") or datos.get("cruces") or ["cruce1"]
                ordenes = {c: ORDEN_ESP32.get(accion) for c in cruces}
            modos = set(ordenes.values())
            ag.objetivo = ("Fluidez" if accion == "restaurarPlanNormal" else
                           "CercoPersecucion" if "cerco" in modos or datos.get("persecucion") else
                           "CorredorEmergencia" if "corredor" in modos else "Fluidez")
            for c, orden in ordenes.items():
                if accion == "restaurarPlanNormal":
                    ag.sugerido.pop(c, None)
                    ag.bloqueado.pop(c, None)
                elif orden in K.MODOS_BLOQUEADOS or datos.get("persecucion"):
                    ag.bloqueado[c] = orden      # retención del solicitado (Caso 2) o modo fijo: no se adapta
                else:
                    ag.sugerido[c] = orden       # sugerencia: el control adaptativo puede priorizar otra cosa
                ag.previo.pop(c, None)            # no mezclar la recompensa de una orden externa con el aprendizaje
                ag.ordenar(c, orden)
                log("Semaforico", f"{accion} en {c} -> '{orden}' (objetivo: {ag.objetivo})")
            if not ordenes:
                log("Semaforico", f"{accion}: sin cambios de semáforos")
            # el plan (desvíos, contraflujo) también se publica para el gemelo digital
            if accion in ("activarPlan", "restaurarPlanNormal") and "plan" in datos and ag.conectado:
                aviso = {**datos, "activo": accion == "activarPlan"}
                ag.mqtt.publish(TEMA_PLAN.format(cruce=datos["cruce"]), json.dumps(aviso, ensure_ascii=False))
            await self.send(responder(msg, "ResultadoAccion", {"accion": accion, "estado": "ok", "cruces": list(ordenes)}))


# =====================================================================
# 7. AGENTE COMUNICADOR  (basado en objetivos): por ahora imprime el aviso
# =====================================================================
class AgenteComunicador(Agent):
    async def setup(self):
        self.enviados = 0
        self.mente(None)
        self.add_behaviour(self.Comunicar(), plantilla("request", "comunicar"))

    def mente(self, ultimo):
        publicar_mente("Comunicador", {
            "tipo": "Basado en objetivos", "rol": "Redacta los avisos con un LLM y los envía por Telegram.",
            "percibe": "request comunicar (del Coordinador)",
            "actua": "mensaje de Telegram al grupo de la Ciudadanía o al de la Policía",
            "codigo": ["agentes/sma_agentes.py · AgenteComunicador", "agentes/comunicador.py (LLM + Telegram)"],
            "reglas": ["SI la alerta es de Seguridad ENTONCES avisar SOLO a la Policía y decir 'posible coincidencia'",
                       "SI no ENTONCES avisar a la Ciudadanía con el plan y su duración",
                       "SI el LLM falla, tarda o cambia los datos ENTONCES usar la plantilla"],
            "parametros": {"llm": comunicador.cargar_env().get("LLM", "ollama"), "max_palabras": K.LLM_MAX_PALABRAS,
                           "timeout_llm_s": K.LLM_TIMEOUT_S},
            "estado": {"avisos_enviados": self.enviados, "ultimo": ultimo},
        })

    class Comunicar(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            datos = json.loads(msg.body)
            datos.setdefault("duracion", SEGUNDOS_PLAN)
            if datos["alerta"].get("tipo") == "Seguridad":
                datos["receptor"] = "Policia"            # regla dura: Seguridad nunca va a la ciudadanía
            texto, origen = await asyncio.to_thread(comunicador.redactar, datos)
            log("Comunicador", f"[para {datos['receptor']}] {texto}   ({origen})")
            envio = await asyncio.to_thread(comunicador.enviar, datos["receptor"], texto)
            log("Comunicador", envio)
            self.agent.enviados += 1
            self.agent.mente({"receptor": datos["receptor"], "texto": texto, "redaccion": origen, "envio": envio})


# =====================================================================
async def main():
    conectar_bitacora()
    agentes = [
        AgenteVigilancia(JID["vigilancia"], CLAVE),
        AgenteClasificador(JID["clasificador"], CLAVE),
        AgenteCoordinador(JID["coordinador"], CLAVE),
        AgenteConsultor(JID["consultor"], CLAVE),
        AgentePredictor(JID["predictor"], CLAVE),
        AgenteSemaforico(JID["semaforico"], CLAVE),
        AgenteComunicador(JID["comunicador"], CLAVE),
    ]
    for ag in agentes:
        await ag.start(auto_register=True)
    print("\n=== Los 7 agentes del SMA están activos. Ctrl+C para detener. ===\n", flush=True)

    if "--simular" in sys.argv:
        await asyncio.sleep(2)
        log("Simulación", "disparando un accidente de prueba en cruce1")
        agentes[0].cola.put_nowait({"tipo": "accidente", "ubicacion": "cruce1"})

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for ag in agentes:
            await ag.stop()


if __name__ == "__main__":
    spade.run(main(), embedded_xmpp_server=True)
