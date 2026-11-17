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
from aprendizaje import ExperienciaPlanes, QSemaforo

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
        self.contador = {}
        loop = asyncio.get_running_loop()

        def al_llegar(cliente, userdata, msg):
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
            self.mqtt.loop_start()
            log("Vigilancia", f"escuchando eventos en MQTT '{TEMA_EVENTOS}'")
        except OSError:
            log("Vigilancia", "AVISO: no pude conectarme a Mosquitto (usa --simular para probar sin él)")
        self.mente()
        self.add_behaviour(self.Percibir())

    def mente(self, ultimo=None):
        publicar_mente("Vigilancia", {
            "tipo": "Reactivo simple", "rol": "Percibe los eventos de la calle y los reenvía al Clasificador.",
            "percibe": "sma/eventos/<cruce> (botón del ESP32, panel, mosquitto_pub)",
            "actua": "inform AlertaNovedad -> Clasificador; inform TerminarAlerta -> Coordinador",
            "codigo": ["agentes/sma_agentes.py · AgenteVigilancia"],
            "reglas": ["SI llega un evento ENTONCES enviarlo al Clasificador (sin razonar: reflejo)",
                       "SI el evento es fin_contingencia ENTONCES pedir al Coordinador cerrar esa alerta"],
            "parametros": {"tema": TEMA_EVENTOS},
            "estado": {"eventos_percibidos": self.contador, "ultimo": ultimo},
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
    if evento == "accidente" and cruce == K.SALIDA_BOCAGRANDE:
        # el accidente bloquea la única salida (o entrada) de Bocagrande: se considera el contraflujo
        claves.append("contraflujo_entrada" if alerta.get("sentido") == "entrada" else "contraflujo_salida")
    planes = []
    for clave in claves:
        c = K.CATALOGO_PLANES[clave]
        p = {"clave": clave, "nombre": c["nombre"], "impacto": experiencia.impacto(clave), "costo": c["costo"],
             "veces": experiencia.veces(clave), "receptor": "Policia" if seguridad else "Ciudadania"}
        if clave == "solo_avisar":
            p.update(semaforos={}, aviso=(f"posible coincidencia ({evento}) en {lugar}." if seguridad
                                          else f"{evento} en {lugar}; transite con precaución."))
        elif clave == "cerco":
            p.update(semaforos={cruce: "cerco"}, aviso=f"posible coincidencia ({evento}) en {lugar}; cerco semafórico activo.")
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
        planes.append(p)
    return planes


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
        catalogo = [{"clave": k, "nombre": c["nombre"], "costo": c["costo"], "impacto_inicial": c["impacto"],
                     "impacto_aprendido": self.experiencia.impacto(k), "veces": self.experiencia.veces(k)}
                    for k, c in K.CATALOGO_PLANES.items()]
        publicar_mente("Coordinador", {
            "tipo": "Basado en utilidad (aprende el impacto real de cada plan)",
            "rol": "Genera planes para cada alerta, elige el de mayor utilidad, los mantiene y los cierra.",
            "percibe": "inform Alerta; estado vial y colas medias (Consultor); TerminarAlerta (operador)",
            "actua": "request activarPlan / restaurarPlanNormal -> Semafórico; request comunicar -> Comunicador",
            "codigo": ["agentes/sma_agentes.py · AgenteCoordinador, generar_planes",
                       "agentes/aprendizaje.py · ExperienciaPlanes", "agentes/conocimiento.py · Coordinador"],
            "reglas": ["utilidad = impacto × factor de tráfico − " + f"{K.PESO_COSTO} × costo",
                       f"SI accidente en {K.SALIDA_BOCAGRANDE} (única salida de Bocagrande) ENTONCES considerar contraflujo de la Carrera 1",
                       "SI alerta de Seguridad ENTONCES cerco y aviso SOLO a la Policía",
                       "Al cerrar un plan mide las colas de TODO el sector: efecto = (cola antes − cola durante) / máx(cola antes, 3)",
                       f"SI efecto > 0 ENTONCES impacto sube: + {K.ALFA_PLANES}·efecto·(1 − impacto); "
                       f"SI efecto < 0 ENTONCES baja: + {K.ALFA_PLANES}·efecto·impacto"],
            "parametros": {"duracion_plan_s": SEGUNDOS_PLAN, "factor_trafico": K.FACTOR_TRAFICO, "peso_costo": K.PESO_COSTO,
                           "alfa_aprendizaje": K.ALFA_PLANES, "linea_base_s": K.SEGUNDOS_LINEA_BASE},
            "estado": {"catalogo": catalogo, "ultima_decision": self.ultima, "activas": list(self.activas.values()),
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

            # 2) Generar planes y elegir el de mayor utilidad (impacto aprendido con la experiencia)
            factor = K.FACTOR_TRAFICO.get(estado["nivel"], K.FACTOR_TRAFICO_DESCONOCIDO)
            planes = generar_planes(a, cruce, ag.experiencia)
            for p in planes:
                p["utilidad"] = round(p["impacto"] * factor - K.PESO_COSTO * p["costo"], 2)
            mejor = max(planes, key=lambda p: p["utilidad"])
            log("Coordinador", "utilidades: " + ", ".join(
                f"{p['nombre']}={p['utilidad']}" + (f" (aprendido en {p['veces']} usos)" if p["veces"] else "")
                for p in planes))
            log("Coordinador", f"plan elegido: {mejor['nombre']}")
            ag.ultima = {"alerta": hilo, "cruce": cruce, "nivel": estado["nivel"], "factor": factor, "elegido": mejor["clave"],
                         "planes": [{k: p[k] for k in ("clave", "nombre", "impacto", "costo", "utilidad", "veces")} for p in planes]}

            # línea base para aprender: cola media de todo el sector justo antes de actuar
            cruces_plan = sorted(set(mejor["semaforos"]) | {cruce})
            antes = await self.cola_media([], K.SEGUNDOS_LINEA_BASE, hilo)

            # 3) Ordenar al Semafórico y esperar resultado
            sentido = {"contraflujo_salida": "salida", "contraflujo_entrada": "entrada"}.get(mejor["clave"])
            plan = {"plan": mejor["clave"], "nombre": mejor["nombre"], "semaforos": mejor["semaforos"],
                    "alerta": hilo, "cruce": cruce, "evento": a.get("tipo_evento"),
                    "sentido": a.get("sentido") or sentido, "duracion": SEGUNDOS_PLAN}
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
                viejo, nuevo, efecto = ag.experiencia.actualizar(mejor["clave"], antes["media"], durante["media"])
                log("Coordinador", f"{hilo}: aprendí del plan '{mejor['nombre']}': cola {antes['media']:.1f} -> "
                                   f"{durante['media']:.1f} (efecto {efecto:+.2f}); impacto {viejo:.2f} -> {nuevo:.2f}")
                ag.aprendido.append({"alerta": hilo, "plan": mejor["nombre"], "antes": round(antes["media"], 1),
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
                           "SI no hay lecturas en 15 s ENTONCES nivel desconocido"],
                "parametros": {"umbral_nivel": K.UMBRAL_NIVEL, "cache_s": K.CACHE_SEGUNDOS, "historial_s": K.HISTORIAL_SEGUNDOS},
                "estado": {"niveles": niveles},
            })

    class Responder(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            ag, accion = self.agent, msg.get_metadata("action")
            pedido = json.loads(msg.body)
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
    async def setup(self):
        publicar_mente("Predictor", {
            "tipo": "Híbrido: basado en modelos + utilidad", "rol": "Predice la ruta de huida (Caso 2).",
            "percibe": "request predecirRutaHuida", "actua": "inform Ruta",
            "codigo": ["agentes/sma_agentes.py · AgentePredictor", "agentes/conocimiento.py · RUTA_HUIDA_POR_DEFECTO"],
            "reglas": ["Por ahora regla fija (pendiente Caso 2): ruta " + " → ".join(K.RUTA_HUIDA_POR_DEFECTO["cruces"])],
            "parametros": K.RUTA_HUIDA_POR_DEFECTO, "estado": {},
        })
        self.add_behaviour(self.Predecir(), plantilla("request", "predecirRutaHuida"))

    class Predecir(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            pedido = json.loads(msg.body)
            ruta = dict(K.RUTA_HUIDA_POR_DEFECTO)
            log("Predictor", f"ruta probable desde {pedido.get('ubicacion')}: {ruta['cruces']}")
            await self.send(responder(msg, "Ruta", ruta))


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
        log("Semaforico", f"control adaptativo cada {K.INTERVALO_CONTROL} s; experiencia previa: {self.q.pasos} decisiones, epsilon={self.q.epsilon:.3f}")
        self.add_behaviour(self.Obedecer(), plantilla("request"))
        self.add_behaviour(self.ControlAdaptativo(period=K.INTERVALO_CONTROL))

    def ordenar(self, cruce, modo):
        if modo and self.conectado:
            self.mqtt.publish(TEMA_SEMAFORO.format(cruce=cruce), modo)
        self.modo[cruce] = modo

    def mente(self):
        recientes = self.q.recompensas[-50:]
        publicar_mente("Semaforico", {
            "tipo": "Basado en objetivos + aprendizaje por refuerzo (Q-learning)",
            "rol": "Obedece los planes del Coordinador y, por su cuenta, da prioridad al acceso con más cola.",
            "percibe": "sma/sensores/<cruce> (cola en A y B); request activarPlan / restaurarPlanNormal",
            "actua": "sma/semaforo/<cruce> = normal | prioridad_a | prioridad_b | peatonal | cerco",
            "codigo": ["agentes/sma_agentes.py · AgenteSemaforico.ControlAdaptativo",
                       "agentes/aprendizaje.py · QSemaforo", "agentes/conocimiento.py · Semafórico"],
            "reglas": [f"Cada {K.INTERVALO_CONTROL} s: estado = (cubeta cola A, cubeta cola B), cubetas {K.CUBETAS_COLA}",
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
                       "recompensas": recientes, "cruces": self.decision, "q": self.q.q, "visitas": self.q.visitas},
        })

    class ControlAdaptativo(PeriodicBehaviour):
        async def run(self):
            ag = self.agent
            for cruce, lecturas in list(ag.lecturas.items()):
                if not lecturas:
                    continue
                a = sum(x for x, _ in lecturas) / len(lecturas)
                b = sum(y for _, y in lecturas) / len(lecturas)
                ag.lecturas[cruce] = []
                total = a + b
                estado = ag.q.estado(a, b)
                info = {"A": round(a, 1), "B": round(b, 1), "estado": estado, "modo": ag.modo.get(cruce, "normal"),
                        "sugerido": ag.sugerido.get(cruce), "bloqueado": ag.bloqueado.get(cruce)}
                # aprender de la decisión anterior en este cruce
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
                accion, motivo, puntajes = ag.q.elegir(cruce, estado, ag.sugerido.get(cruce), anterior)
                cambio = accion != anterior
                if cambio:
                    ag.ordenar(cruce, accion)
                    log("Semaforico", f"{cruce}: cola A={a:.1f} B={b:.1f} ({estado}) -> {accion} [{motivo}; "
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
            ag.objetivo = ("CercoPersecucion" if "cerco" in modos else
                           "CorredorEmergencia" if "corredor" in modos else "Fluidez")
            for c, orden in ordenes.items():
                if accion == "restaurarPlanNormal":
                    ag.sugerido.pop(c, None)
                    ag.bloqueado.pop(c, None)
                elif orden in K.MODOS_BLOQUEADOS:
                    ag.bloqueado[c] = orden
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
            "tipo": "Basado en objetivos", "rol": "Redacta y envía los avisos (luego Telegram + LLM).",
            "percibe": "request comunicar (del Coordinador)", "actua": "aviso a la Ciudadanía o a la Policía",
            "codigo": ["agentes/sma_agentes.py · AgenteComunicador"],
            "reglas": ["SI la alerta es de Seguridad ENTONCES avisar SOLO a la Policía y decir 'posible coincidencia'",
                       "SI no ENTONCES avisar a la Ciudadanía con el plan y su duración"],
            "parametros": {}, "estado": {"avisos_enviados": self.enviados, "ultimo": ultimo},
        })

    class Comunicar(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            datos = json.loads(msg.body)
            a = datos["alerta"]
            minutos = round(datos.get("duracion", SEGUNDOS_PLAN) / 60, 1)
            texto = (f"Alerta de {a.get('tipo', '').lower()}: {datos.get('aviso', '')} "
                     f"Plan: {datos.get('plan', '-')} (~{minutos:g} min).")
            log("Comunicador", f"[para {datos['receptor']}] {texto}   (luego: Telegram + LLM)")
            self.agent.enviados += 1
            self.agent.mente({"receptor": datos["receptor"], "texto": texto})


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
