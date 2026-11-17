"""
SMA Movilidad y Seguridad CTG - Esqueleto de los 7 agentes (tareas 2.5 y 2.6)
==============================================================================
Recorre el Caso 1 (accidente -> plan de desvío elegido por utilidad) de punta a punta:

  ESP32 / mosquitto_pub --MQTT--> AgenteVigilancia --inform--> AgenteClasificadorAlertas
  --inform--> AgenteCoordinador --query-ref--> AgenteConsultor
  AgenteCoordinador --request--> AgenteSemaforico --MQTT--> ESP32 (LED)
  AgenteCoordinador --request--> AgenteComunicador (por ahora imprime el aviso)
  AgentePredictor queda listo para el Caso 2 (responde rutas de huida).

Requisitos:   pip install spade paho-mqtt
Uso:          python sma_agentes.py               (espera eventos por MQTT)
              python sma_agentes.py --simular     (dispara un accidente de prueba solo)
              python sma_agentes.py --duracion 120 (segundos que dura cada plan; por defecto 300)

Disparar un accidente desde otra terminal (carpeta de Mosquitto):
              mosquitto_pub -h localhost -t sma/eventos/cruce1 -m accidente
              mosquitto_pub -h localhost -t sma/eventos/cruce5 -m "{\"tipo\":\"accidente\",\"sentido\":\"salida\"}"
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from spade.template import Template

# ---------------- Configuración ----------------
DOMINIO = "localhost"          # servidor XMPP embebido de SPADE
CLAVE = "sma123"
BROKER_MQTT = "localhost"      # PC donde corre Mosquitto
PUERTO_MQTT = 1883
TEMA_EVENTOS = "sma/eventos/#"          # lo que publica el ESP32 (o mosquitto_pub)
TEMA_SEMAFORO = "sma/semaforo/{cruce}"  # lo que escucha el ESP32
TEMA_PLAN = "sma/plan/{cruce}"          # plan activo (lo dibuja el gemelo digital 3D)
SEGUNDOS_PLAN = 300                     # cuánto dura cada plan (5 min) antes de restaurar
if "--duracion" in sys.argv:             # p. ej. --duracion 60 (las decisiones siguen siendo inmediatas)
    SEGUNDOS_PLAN = float(sys.argv[sys.argv.index("--duracion") + 1])

# ---------------- Conocimiento vial de Bocagrande ----------------
# La única salida/entrada de Bocagrande hacia el Centro es la Av. San Martín por el cruce5
# (Calle 15, frente a la Base Naval); la Carrera 1 (vía de la playa) sirve de alterna en contraflujo.
SALIDA_BOCAGRANDE = "cruce5"
try:
    _ruta = Path(__file__).resolve().parent.parent / "maqueta_virtual" / "cruces.json"
    NOMBRE_CRUCE = {c["id"]: c["nombre"] for c in json.loads(_ruta.read_text(encoding="utf-8"))["cruces"]}
except (OSError, ValueError, KeyError):
    NOMBRE_CRUCE = {}

PLANES_CONTRAFLUJO = {
    "salida": {
        "clave": "contraflujo_salida", "nombre": "Contraflujo Carrera 1 (salida)",
        "semaforos": {"cruce4": "prioridad_a", "cruce3": "prioridad_b", "cruce5": "prioridad_b"},
        "aviso": ("accidente en la Av. San Martín antes de la Calle 15, sentido salida. Para salir de Bocagrande "
                  "tome la Calle 10 desde la Carrera 3 hacia la Carrera 1: la Carrera 1 queda SOLO de salida. "
                  "El ingreso a Bocagrande es únicamente por la Av. San Martín."),
    },
    "entrada": {
        "clave": "contraflujo_entrada", "nombre": "Contraflujo Carrera 1 (entrada)",
        "semaforos": {"cruce5": "prioridad_a", "cruce3": "prioridad_b"},
        "aviso": ("accidente en la Av. San Martín después de la Calle 15, sentido entrada. El ingreso a "
                  "Bocagrande es SOLO por la Carrera 1; la Av. San Martín queda para salir."),
    },
}
FACTOR_TRAFICO = {"alto": 1.0, "medio": 0.85, "bajo": 0.7}


def nombre_cruce(cruce):
    return f"{cruce} ({NOMBRE_CRUCE[cruce]})" if cruce in NOMBRE_CRUCE else cruce


def generar_planes(alerta, cruce):
    """Planes candidatos para una alerta (clave, nombre, impacto, costo, semáforos, receptor, aviso)."""
    evento = alerta.get("tipo_evento")
    lugar = nombre_cruce(cruce)
    avisar = {"clave": "solo_avisar", "nombre": "Solo avisar", "impacto": 0.3, "costo": 0.0, "semaforos": {},
              "receptor": "Ciudadania", "aviso": f"{evento} en {lugar}; transite con precaución."}
    if alerta.get("tipo") == "Seguridad":
        return [
            {"clave": "cerco", "nombre": "Cerco semafórico", "impacto": 0.9, "costo": 0.5,
             "semaforos": {cruce: "cerco"}, "receptor": "Policia",
             "aviso": f"posible coincidencia ({evento}) en {lugar}; cerco semafórico activo."},
            {**avisar, "receptor": "Policia", "aviso": f"posible coincidencia ({evento}) en {lugar}."},
        ]
    if evento == "accidente":
        planes = [avisar,
                  {"clave": "desvio_local", "nombre": f"Desvío local en {cruce}", "impacto": 0.55, "costo": 0.15,
                   "semaforos": {cruce: "prioridad_a"}, "receptor": "Ciudadania",
                   "aviso": f"accidente en {lugar}; más tiempo de verde para la vía libre, use vías alternas."}]
        if cruce == SALIDA_BOCAGRANDE:
            # el accidente bloquea la única salida (o entrada): el contraflujo por la Carrera 1 rinde mucho más
            sentido = alerta.get("sentido") or "salida"
            planes.append({**PLANES_CONTRAFLUJO[sentido], "impacto": 0.95, "costo": 0.35, "receptor": "Ciudadania"})
        return planes
    if evento == "congestion":
        return [avisar, {"clave": "priorizar_principal", "nombre": f"Verde largo para la vía principal en {cruce}",
                         "impacto": 0.7, "costo": 0.2, "semaforos": {cruce: "prioridad_a"}, "receptor": "Ciudadania",
                         "aviso": f"congestión en {lugar}; se amplió el verde de la vía principal."}]
    if evento == "aglomeracion":
        return [avisar, {"clave": "fase_peatonal", "nombre": f"Fase peatonal extendida en {cruce}",
                         "impacto": 0.7, "costo": 0.25, "semaforos": {cruce: "peatonal"}, "receptor": "Ciudadania",
                         "aviso": f"aglomeración de personas en {lugar}; fase peatonal extendida, conduzca despacio."}]
    return [avisar]

JID = {n: f"{n}@{DOMINIO}" for n in
       ["vigilancia", "clasificador", "coordinador", "consultor",
        "predictor", "semaforico", "comunicador"]}


TEMA_PANEL_LOG = "sma/panel/log"        # bitácora que muestra la maqueta virtual
_mqtt_log = None                        # cliente MQTT solo para publicar la bitácora


def conectar_bitacora():
    """Conecta un cliente MQTT para que cada línea de log() llegue también al panel web."""
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


def mensaje(para, performativa, accion, contenido, hilo=None):
    """Crea un mensaje FIPA-ACL con el contenido en JSON (ver pestaña 'Mensajes').
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


# =====================================================================
# 1. AGENTE VIGILANCIA  (reactivo simple): percibe eventos por MQTT
# =====================================================================
class AgenteVigilancia(Agent):
    async def setup(self):
        self.cola = asyncio.Queue()
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
        self.add_behaviour(self.Percibir())

    class Percibir(CyclicBehaviour):
        async def run(self):
            evento = await self.agent.cola.get()
            evento["ts"] = datetime.now().isoformat(timespec="seconds")
            log("Vigilancia", f"percibí: {evento}")
            await self.send(mensaje("clasificador", "inform", "AlertaNovedad", evento))


# =====================================================================
# 2. AGENTE CLASIFICADOR DE ALERTAS  (basado en objetivos)
# =====================================================================
REGLAS = {  # tipo de evento -> (TipoAlerta, NivelPrioridad)
    "accidente":          ("Movilidad", "Alta"),
    "congestion":         ("Movilidad", "Media"),
    "aglomeracion":       ("Mixta", "Media"),
    "placa_reportada":    ("Seguridad", "Alta"),
    "persona_solicitada": ("Seguridad", "Critica"),
}


class AgenteClasificador(Agent):
    async def setup(self):
        self.contador = 0
        self.add_behaviour(self.Clasificar(), plantilla("inform", "AlertaNovedad"))

    class Clasificar(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            novedad = json.loads(msg.body)
            tipo, prioridad = REGLAS.get(novedad.get("tipo"), ("Mixta", "Baja"))
            self.agent.contador += 1
            alerta = {**novedad, "tipo_evento": novedad.get("tipo"),
                      "id": f"AL-{self.agent.contador:04d}", "tipo": tipo, "prioridad": prioridad}
            log("Clasificador", f"{alerta['id']}: {novedad.get('tipo')} -> {tipo}, prioridad {prioridad}")
            await self.send(mensaje("coordinador", "inform", "Alerta", alerta))


# =====================================================================
# 3. AGENTE COORDINADOR  (basado en utilidad)
# =====================================================================
class AgenteCoordinador(Agent):
    async def setup(self):
        self.add_behaviour(self.Atender(), plantilla("inform", "Alerta"))

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

    class ResponderAlerta(OneShotBehaviour):
        def __init__(self, alerta):
            super().__init__()
            self.alerta = alerta

        async def esperar(self, accion, segundos=10):
            r = await self.receive(timeout=segundos)
            if r and r.get_metadata("action") == accion:
                return json.loads(r.body)
            return None

        async def run(self):
            a = self.alerta
            cruce, hilo = a.get("ubicacion", "cruce1"), a["id"]
            # 1) Consultar estado vial
            await self.send(mensaje("consultor", "query-ref", "consultarEstadoVial", {"sector": cruce}, hilo))
            estado = await self.esperar("EstadoTrafico") or {"nivel": "desconocido"}
            log("Coordinador", f"estado vial en {cruce}: {estado['nivel']}")

            # 2) Generar planes y elegir el de mayor utilidad: impacto x tráfico - 0.5 x costo de movilidad
            factor = FACTOR_TRAFICO.get(estado["nivel"], 0.85)
            planes = generar_planes(a, cruce)
            for p in planes:
                p["utilidad"] = round(p["impacto"] * factor - 0.5 * p["costo"], 2)
            mejor = max(planes, key=lambda p: p["utilidad"])
            log("Coordinador", "utilidades: " + ", ".join(f"{p['nombre']}={p['utilidad']}" for p in planes))
            log("Coordinador", f"plan elegido: {mejor['nombre']}")

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

            # 5) Mantener el plan SEGUNDOS_PLAN para que se vea su efecto y restaurar
            log("Coordinador", f"{hilo}: plan activo {SEGUNDOS_PLAN:g} s ({mejor['nombre']})")
            await asyncio.sleep(SEGUNDOS_PLAN)
            await self.send(mensaje("semaforico", "request", "restaurarPlanNormal",
                                    {**plan, "cruces": sorted(set(mejor["semaforos"]) | {cruce})}, hilo))
            await self.esperar("ResultadoAccion")
            log("Coordinador", f"{hilo} cerrado: semáforos vuelven a plan normal")


# =====================================================================
# 4. AGENTE CONSULTOR  (reactivo basado en modelos): datos + caché
# =====================================================================
class AgenteConsultor(Agent):
    async def setup(self):
        self.cache = {}
        self.add_behaviour(self.Responder(), plantilla("query-ref"))

    class Responder(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            pedido = json.loads(msg.body)
            clave = (msg.get_metadata("action"), pedido.get("sector"))
            if clave in self.agent.cache:
                estado, origen = self.agent.cache[clave], "caché"
            else:
                estado, origen = {"sector": pedido.get("sector"), "nivel": "alto"}, "base de datos (simulada)"
                self.agent.cache[clave] = estado
            log("Consultor", f"estado vial de {pedido.get('sector')} desde {origen}")
            r = msg.make_reply()
            r.set_metadata("performative", "inform")
            r.set_metadata("action", "EstadoTrafico")
            r.body = json.dumps(estado)
            await self.send(r)


# =====================================================================
# 5. AGENTE PREDICTOR  (basado en modelos + utilidad) - listo para el Caso 2
# =====================================================================
class AgentePredictor(Agent):
    async def setup(self):
        self.add_behaviour(self.Predecir(), plantilla("request", "predecirRutaHuida"))

    class Predecir(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            pedido = json.loads(msg.body)
            ruta = {"cruces": ["cruce2", "cruce1"], "confianza": 0.7}  # reglas simples por ahora
            log("Predictor", f"ruta probable desde {pedido.get('ubicacion')}: {ruta['cruces']}")
            r = msg.make_reply()
            r.set_metadata("performative", "inform")
            r.set_metadata("action", "Ruta")
            r.body = json.dumps(ruta)
            await self.send(r)


# =====================================================================
# 6. AGENTE SEMAFÓRICO  (basado en objetivos): manda al ESP32 por MQTT
# =====================================================================
ORDEN_ESP32 = {  # acción del agente -> modo que entiende el ESP32 de la maqueta
    "abrirCorredorEmergencia": "corredor",
    "restaurarPlanNormal": "normal",
    "activarCerco": "cerco",
}


class AgenteSemaforico(Agent):
    async def setup(self):
        self.objetivo = "Fluidez"
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sma-semaforico")
        try:
            self.mqtt.connect(BROKER_MQTT, PUERTO_MQTT)
            self.mqtt.loop_start()
            self.conectado = True
        except OSError:
            self.conectado = False
            log("Semaforico", "AVISO: sin Mosquitto, solo simulo los semáforos")
        self.add_behaviour(self.Obedecer(), plantilla("request"))

    class Obedecer(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=60)
            if not msg:
                return
            accion = msg.get_metadata("action")
            datos = json.loads(msg.body)
            if accion == "activarPlan":          # el plan trae un modo por cruce
                ordenes = datos.get("semaforos", {})
            else:
                cruces = datos.get("ruta") or datos.get("cruces") or ["cruce1"]
                ordenes = {c: ORDEN_ESP32.get(accion) for c in cruces}
            modos = set(ordenes.values())
            self.agent.objetivo = ("CercoPersecucion" if "cerco" in modos else
                                   "CorredorEmergencia" if "corredor" in modos else "Fluidez")
            for c, orden in ordenes.items():
                if orden and self.agent.conectado:
                    self.agent.mqtt.publish(TEMA_SEMAFORO.format(cruce=c), orden)
                log("Semaforico", f"{accion} en {c} -> '{orden}' (objetivo: {self.agent.objetivo})")
            if not ordenes:
                log("Semaforico", f"{accion}: sin cambios de semáforos")
            # el plan (desvíos, contraflujo) también se publica para el gemelo digital
            if accion in ("activarPlan", "restaurarPlanNormal") and "plan" in datos and self.agent.conectado:
                aviso = {**datos, "activo": accion == "activarPlan"}
                self.agent.mqtt.publish(TEMA_PLAN.format(cruce=datos["cruce"]), json.dumps(aviso, ensure_ascii=False))
            r = msg.make_reply()
            r.set_metadata("performative", "inform")
            r.set_metadata("action", "ResultadoAccion")
            r.body = json.dumps({"accion": accion, "estado": "ok", "cruces": list(ordenes)})
            await self.send(r)


# =====================================================================
# 7. AGENTE COMUNICADOR  (basado en objetivos): por ahora imprime el aviso
# =====================================================================
class AgenteComunicador(Agent):
    async def setup(self):
        self.add_behaviour(self.Comunicar(), plantilla("request", "comunicar"))

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
