"""
SMA Movilidad y Seguridad CTG - Esqueleto de los 7 agentes (tareas 2.5 y 2.6)
==============================================================================
Recorre el Caso 1 (accidente -> corredor de emergencia) de punta a punta:

  ESP32 / mosquitto_pub --MQTT--> AgenteVigilancia --inform--> AgenteClasificadorAlertas
  --inform--> AgenteCoordinador --query-ref--> AgenteConsultor
  AgenteCoordinador --request--> AgenteSemaforico --MQTT--> ESP32 (LED)
  AgenteCoordinador --request--> AgenteComunicador (por ahora imprime el aviso)
  AgentePredictor queda listo para el Caso 2 (responde rutas de huida).

Requisitos:   pip install spade paho-mqtt
Uso:          python sma_agentes.py               (espera eventos por MQTT)
              python sma_agentes.py --simular     (dispara un accidente de prueba solo)

Disparar un accidente desde otra terminal (carpeta de Mosquitto):
              mosquitto_pub -h localhost -t sma/eventos/cruce1 -m accidente
"""

import asyncio
import json
import sys
from datetime import datetime

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
SEGUNDOS_CORREDOR = 30                  # cuánto se mantiene el plan (corredor) antes de restaurar
if "--duracion" in sys.argv:             # p. ej. --duracion 60 (las decisiones siguen siendo inmediatas)
    SEGUNDOS_CORREDOR = float(sys.argv[sys.argv.index("--duracion") + 1])

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


def mensaje(para, performativa, accion, contenido):
    """Crea un mensaje FIPA-ACL con el contenido en JSON (ver pestaña 'Mensajes')."""
    m = Message(to=JID[para])
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
            # Lanza un comportamiento por incidente para poder esperar respuestas
            self.agent.add_behaviour(AgenteCoordinador.ResponderAccidente(alerta),
                                     plantilla("inform"))

    class ResponderAccidente(OneShotBehaviour):
        def __init__(self, alerta):
            super().__init__()
            self.alerta = alerta

        async def esperar(self, accion, segundos=10):
            r = await self.receive(timeout=segundos)
            if r and r.get_metadata("action") == accion:
                return json.loads(r.body)
            return None

        async def run(self):
            cruce = self.alerta.get("ubicacion", "cruce1")
            # 1) Consultar estado vial
            await self.send(mensaje("consultor", "query-ref", "consultarEstadoVial", {"sector": cruce}))
            estado = await self.esperar("EstadoTrafico") or {"nivel": "desconocido"}
            log("Coordinador", f"estado vial en {cruce}: {estado['nivel']}")

            # 2) Generar planes y elegir el de mayor utilidad
            planes = [
                {"nombre": "Corredor de emergencia", "impacto": 0.9, "costo_movilidad": 0.3},
                {"nombre": "Solo avisar a la ciudadanía", "impacto": 0.4, "costo_movilidad": 0.0},
            ]
            for p in planes:
                p["utilidad"] = round(p["impacto"] - 0.5 * p["costo_movilidad"], 2)
            mejor = max(planes, key=lambda p: p["utilidad"])
            log("Coordinador", "utilidades: " + ", ".join(f"{p['nombre']}={p['utilidad']}" for p in planes))
            log("Coordinador", f"plan elegido: {mejor['nombre']}")

            # 3) Ordenar al Semafórico y esperar resultado
            await self.send(mensaje("semaforico", "request", "abrirCorredorEmergencia",
                                    {"ruta": [cruce], "alerta": self.alerta["id"]}))
            resultado = await self.esperar("ResultadoAccion") or {"estado": "sin respuesta"}
            log("Coordinador", f"resultado del Semafórico: {resultado}")

            # 4) Pedir al Comunicador que avise
            await self.send(mensaje("comunicador", "request", "comunicar",
                                    {"receptor": "Ciudadania", "alerta": self.alerta, "resultado": resultado}))

            # 5) Mantener el plan SEGUNDOS_CORREDOR para que se vea su efecto y restaurar
            log("Coordinador", f"{self.alerta['id']}: plan activo {SEGUNDOS_CORREDOR:g} s en {cruce}")
            await asyncio.sleep(SEGUNDOS_CORREDOR)
            await self.send(mensaje("semaforico", "request", "restaurarPlanNormal", {"cruces": [cruce]}))
            await self.esperar("ResultadoAccion")
            log("Coordinador", f"{self.alerta['id']} cerrado: semáforos vuelven a plan normal")


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
            cruces = datos.get("ruta") or datos.get("cruces") or ["cruce1"]
            orden = ORDEN_ESP32.get(accion)
            self.agent.objetivo = {"abrirCorredorEmergencia": "CorredorEmergencia",
                                   "activarCerco": "CercoPersecucion"}.get(accion, "Fluidez")
            for c in cruces:
                if orden and self.agent.conectado:
                    self.agent.mqtt.publish(TEMA_SEMAFORO.format(cruce=c), orden)
                log("Semaforico", f"{accion} en {c} -> ESP32 '{orden}' (objetivo: {self.agent.objetivo})")
            r = msg.make_reply()
            r.set_metadata("performative", "inform")
            r.set_metadata("action", "ResultadoAccion")
            r.body = json.dumps({"accion": accion, "estado": "ok", "cruces": cruces})
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
            evento = str(a.get("tipo_evento") or a.get("tipo", "evento")).replace("_", " ")
            texto = (f"Alerta de {a.get('tipo', '').lower()}: se reporta {evento} en {a.get('ubicacion')}. "
                     f"Se abrió un corredor de emergencia; por favor tome una vía alterna.")
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
