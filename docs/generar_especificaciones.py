# Genera docs/Especificaciones_Tecnicas_SMA.pdf (reportlab).  Uso: python docs/generar_especificaciones.py
import json, sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image,
                                KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon

RAIZ = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "Especificaciones_Tecnicas_SMA.pdf"
ENT = RAIZ / "modelos" / "entrenados"

F = "C:/Windows/Fonts/"
pdfmetrics.registerFont(TTFont("Arial", F + "arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-B", F + "arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-I", F + "ariali.ttf"))
pdfmetrics.registerFont(TTFont("Arial-BI", F + "arialbi.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("Arial", normal="Arial", bold="Arial-B", italic="Arial-I", boldItalic="Arial-BI")

AZUL = colors.HexColor("#1F3A5F")
AZUL_CLARO = colors.HexColor("#E8EEF5")
GRIS = colors.HexColor("#555555")

ss = getSampleStyleSheet()
N = ParagraphStyle("n", parent=ss["Normal"], fontName="Arial", fontSize=9.5, leading=13.2, alignment=TA_JUSTIFY,
                   spaceAfter=5)
CELDA = ParagraphStyle("c", parent=N, fontSize=8.2, leading=10.4, alignment=0, spaceAfter=0)
CELDA_B = ParagraphStyle("cb", parent=CELDA, fontName="Arial-B", textColor=colors.white)
H1 = ParagraphStyle("h1", parent=N, fontName="Arial-B", fontSize=15, leading=19, textColor=AZUL, spaceBefore=6,
                    spaceAfter=8, alignment=0)
H2 = ParagraphStyle("h2", parent=N, fontName="Arial-B", fontSize=11.5, leading=15, textColor=AZUL, spaceBefore=8,
                    spaceAfter=4, alignment=0)
FORM = ParagraphStyle("f", parent=N, fontName="Arial-I", fontSize=9.5, alignment=TA_CENTER,
                      backColor=colors.HexColor("#F4F6F9"), borderPadding=5, spaceBefore=4, spaceAfter=8)
PIE = ParagraphStyle("pie", parent=N, fontSize=8, textColor=GRIS, alignment=TA_CENTER)
VIN = ParagraphStyle("v", parent=N, leftIndent=12, bulletIndent=2, spaceAfter=2)

historia = []
P = lambda t, s=N: historia.append(Paragraph(t, s))


def vinetas(items):
    for t in items:
        historia.append(Paragraph(t, VIN, bulletText="•"))
    historia.append(Spacer(1, 4))


def tabla(filas, anchos, repetir=1):
    datos = [[Paragraph(str(c), CELDA_B if i == 0 else CELDA) for c in f] for i, f in enumerate(filas)]
    t = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=repetir)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, AZUL_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    historia.append(t)
    historia.append(Spacer(1, 8))


def imagen(ruta, ancho_cm, pie):
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(str(ruta)).getSize()
    im = Image(str(ruta), width=ancho_cm * cm, height=ancho_cm * cm * h / w)
    historia.append(KeepTogether([im, Paragraph(pie, PIE), Spacer(1, 6)]))


def diagrama():
    d = Drawing(17 * cm, 9.2 * cm)

    def caja(x, y, w, h, texto, color="#E8EEF5", borde="#1F3A5F", tam=8):
        d.add(Rect(x, y, w, h, fillColor=colors.HexColor(color), strokeColor=colors.HexColor(borde), rx=4, ry=4))
        lineas = texto.split("\n")
        for i, l in enumerate(lineas):
            d.add(String(x + w / 2, y + h / 2 + (len(lineas) / 2 - i - 0.75) * (tam + 2), l, fontName="Arial",
                         fontSize=tam, textAnchor="middle"))

    def flecha(x1, y1, x2, y2, etiqueta=None):
        d.add(Line(x1, y1, x2, y2, strokeColor=GRIS, strokeWidth=1))
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        p = [x2, y2, x2 - 6 * math.cos(a - 0.4), y2 - 6 * math.sin(a - 0.4), x2 - 6 * math.cos(a + 0.4),
             y2 - 6 * math.sin(a + 0.4)]
        d.add(Polygon(p, fillColor=GRIS, strokeColor=GRIS))
        if etiqueta:
            d.add(String((x1 + x2) / 2 + 3, (y1 + y2) / 2 + 3, etiqueta, fontName="Arial-I", fontSize=6.5,
                         fillColor=GRIS))

    W = 17 * cm
    # fila superior: fuentes
    caja(5, 222, 120, 34, "ESP32 (cruce1 físico)\nPubSubClient, botón GPIO4")
    caja(140, 222, 120, 34, "Simulador de ciudad\nsimulador_ciudad.py")
    caja(275, 222, 115, 34, "Gemelo digital 3D\nThree.js (navegador)")
    caja(405, 222, 77, 34, "Panel FastAPI\n+ WebSocket")
    # broker
    caja(130, 160, 230, 32, "Broker MQTT Mosquitto :1883  (sma/#)", color="#FFF4D6", borde="#B07D00", tam=9)
    for x in (65, 200, 332):
        flecha(x, 222, min(max(x, 140), 350), 192)
    flecha(443, 222, 360, 182, "sensores / eventos")
    # agentes
    caja(5, 20, W - 10, 120, "", color="#F2F7F2", borde="#2E6B3A")
    d.add(String(W / 2, 128, "sma_agentes.py: 7 agentes SPADE 4.1 (FIPA-ACL sobre XMPP embebido)", fontName="Arial-B",
                 fontSize=8.5, textAnchor="middle"))
    nombres = ["Vigilancia\nreactivo", "Clasificador\nobjetivos", "Coordinador\nutilidad + rutas",
               "Consultor\nmodelos", "Predictor\nT-GCN", "Semafórico\nDQN + tabla Q", "Comunicador\nobjetivos"]
    ancho = (W - 30) / 7
    for i, n in enumerate(nombres):
        caja(12 + i * ancho, 72, ancho - 6, 40, n, color="#FFFFFF", borde="#2E6B3A", tam=7.2)
    caja(12, 28, 150, 32, "rutas.py: Akçelik/BPR,\nFrank-Wolfe, Yen", color="#FFFFFF", borde="#7A4FA0", tam=7.2)
    caja(172, 28, 150, 32, "modelos/*.pt (PyTorch)\nT-GCN y Double DQN", color="#FFFFFF", borde="#7A4FA0", tam=7.2)
    caja(332, 28, 150, 32, "conocimiento.py + memoria/\nreglas, Q, experiencia", color="#FFFFFF", borde="#7A4FA0",
         tam=7.2)
    flecha(245, 160, 245, 140, "MQTT")
    return d


# =========================== PORTADA ===========================
historia.append(Spacer(1, 3.2 * cm))
P("Sistema Multiagente de Movilidad y Seguridad", ParagraphStyle("t", parent=H1, fontSize=24, leading=30,
                                                                 alignment=TA_CENTER))
P("Bocagrande, Cartagena de Indias", ParagraphStyle("t2", parent=H2, fontSize=15, alignment=TA_CENTER))
historia.append(Spacer(1, 0.6 * cm))
P("Documento técnico: herramientas, tecnologías, métodos, datos y especificaciones del sistema",
  ParagraphStyle("t3", parent=N, fontSize=12, leading=16, alignment=TA_CENTER))
historia.append(Spacer(1, 2.2 * cm))
P("Proyecto final de Inteligencia Artificial<br/>Ingeniería de Sistemas, UNICOLOMBO",
  ParagraphStyle("t4", parent=N, fontSize=11, leading=15, alignment=TA_CENTER))
historia.append(Spacer(1, 0.5 * cm))
P("Equipo: Enmanuel (líder técnico), Omar, Mauricio, Yohan y Deyker",
  ParagraphStyle("t5", parent=N, fontSize=10.5, alignment=TA_CENTER))
historia.append(Spacer(1, 3 * cm))
P("Noviembre de 2026", ParagraphStyle("t6", parent=N, fontSize=10.5, alignment=TA_CENTER, textColor=GRIS))
historia.append(PageBreak())

# =========================== 1. RESUMEN ===========================
P("1. Resumen", H1)
P("El proyecto es un <b>Sistema Multiagente (SMA)</b> que atiende dos problemas de Cartagena en el sector de "
  "<b>Bocagrande</b>: la movilidad (trancones, accidentes y semáforos de tiempo fijo) y la seguridad (personas o "
  "vehículos solicitados por la ley). Siete agentes de software se comunican con mensajes FIPA-ACL, perciben el "
  "tráfico mediante MQTT y actúan sobre los semáforos de cinco intersecciones reales.")
P("El sistema se demuestra a escala en dos maquetas. La <b>maqueta física</b> es un ESP32 que controla un cruce "
  "con dos semáforos y un botón de accidente. La <b>maqueta virtual</b> es un gemelo digital 3D de Bocagrande, "
  "construido con datos de OpenStreetMap, en el que carros y peatones simulados obedecen los semáforos y reaccionan "
  "a los eventos.")
P("Sobre la base de reglas, la versión actual agrega cinco capas de inteligencia:")
vinetas([
    "<b>Datos reales depurados.</b> Accidentes del DATT de Cartagena, registro oficial de semáforos y flujo "
    "vehicular del SIMM de Medellín.",
    "<b>Modelo matemático de rutas.</b> Asignación de tráfico en equilibrio (Frank-Wolfe con la función de demora "
    "de Akçelik) y k rutas más cortas (Yen). El Coordinador lo usa para elegir planes y proponer rutas.",
    "<b>Redes neuronales entrenadas.</b> Una T-GCN que predice las colas en el Predictor y una Double DQN que decide "
    "los modos semafóricos en el Semafórico, entrenada en el simulador SUMO.",
    "<b>Visión por computador (Caso 2).</b> Reconocimiento facial con OpenCV (YuNet + SFace) para detectar a una "
    "persona solicitada; el Predictor estima su ruta de huida y el Semafórico la retiene para facilitar la "
    "intercepción.",
    "<b>Lenguaje natural.</b> Un LLM local (Ollama, Llama 3.2 3B) redacta los avisos y un bot de Telegram los "
    "envía a la Ciudadanía o a la Policía.",
])
P("Resultados principales", H2)
tabla([
    ["Indicador", "Valor"],
    ["Accidentes reales ubicados sobre la red de Bocagrande", "204 (de 8.066 registros de Cartagena, 2020–2025)"],
    ["Error de la T-GCN al predecir colas a 36 s (prueba)",
     "MAE 1,21 veh frente a 1,42 de la persistencia (mejora del 14,4 %)"],
    ["Demora del DQN frente al semáforo fijo (12 escenarios SUMO)",
     "−4,8 % en demora y −43 % en cola media (14,7 frente a 25,8 veh)"],
    ["Accidente de salida en el cruce5 (hora pico, modelo de rutas)",
     "TSTT de 455 veh·h sin actuar a 282 con el contraflujo de la Carrera 1 (−38 %)"],
    ["Caso 1 con accidentes reales en SUMO (DQN + plan frente a semáforo fijo)",
     "−7,5 % de demora y −9,7 % de viaje medio; mejora en 10 de 10 accidentes"],
    ["Caso 2: tiempo que gana la Policía con la retención semafórica", "+277 a +588 s según la cámara"],
    ["Reconocimiento facial (4 registrados + 1 no registrado)", "93,8 % de aciertos, 0 % de falsos positivos"],
    ["Latencia del evento a los semáforos / al aviso redactado", "0,5 s / ≈ 20 s (incluye el LLM)"],
    ["Código fuente propio", "≈ 8.000 líneas (Python, JavaScript/HTML y C++ de Arduino)"],
], [8.5, 8.5])

# =========================== 2. ARQUITECTURA ===========================
P("2. Arquitectura general", H1)
historia.append(diagrama())
P("Figura 1. Arquitectura: los dispositivos y el gemelo publican eventos y sensores en el broker; los agentes "
  "deciden y publican los modos semafóricos y los planes.", PIE)
P("El flujo de información sigue un ciclo de percepción, decisión y acción:")
vinetas([
    "<b>Percepción.</b> Los eventos (botón del ESP32, panel o simulador) llegan por <i>sma/eventos/&lt;cruce&gt;</i>. "
    "Las colas medidas en el gemelo 3D llegan cada 2 s por <i>sma/sensores/&lt;cruce&gt;</i>.",
    "<b>Decisión.</b> Los agentes intercambian mensajes FIPA-ACL con cuerpo JSON (ontología <i>sma-ctg</i>) sobre un "
    "servidor XMPP embebido en SPADE.",
    "<b>Acción.</b> El Semafórico publica el modo de cada cruce en <i>sma/semaforo/&lt;cruce&gt;</i> y el plan en "
    "<i>sma/plan/&lt;cruce&gt;</i>. El ESP32 y el simulador ejecutan el ciclo y devuelven su estado en "
    "<i>sma/estado/&lt;cruce&gt;</i>.",
    "<b>Explicabilidad.</b> Cada agente publica su estado interno retenido en <i>sma/agentes/&lt;agente&gt;</i> y "
    "su bitácora en <i>sma/panel/log</i>. Ambos se ven en la página <i>/agentes</i> del panel.",
])
historia.append(PageBreak())

# =========================== 3. HERRAMIENTAS ===========================
P("3. Herramientas y tecnologías", H1)
P("Todo el sistema es gratuito y corre en local, en un portátil con Windows 11 y Python 3.13.7. "
  "Las versiones de la tabla son las instaladas en el equipo de desarrollo.")
tabla([
    ["Categoría", "Herramienta (versión)", "Uso en el proyecto"],
    ["Lenguajes", "Python 3.13.7; JavaScript ES2020; HTML/CSS; C++ (Arduino)",
     "Agentes, modelos y servidor; gemelo 3D y panel; firmware del ESP32"],
    ["Sistema multiagente", "SPADE 4.1.4 (con servidor XMPP embebido), greenlet",
     "Agentes, comportamientos (cíclicos, periódicos, de un disparo) y mensajes FIPA-ACL"],
    ["Mensajería IoT", "Mosquitto (broker MQTT, puerto 1883); paho-mqtt 2.x (CallbackAPIVersion 2)",
     "Bus de eventos, sensores, semáforos, planes y estado de los agentes"],
    ["Servidor web", "FastAPI 0.163.1; uvicorn 0.52.4; WebSocket",
     "Panel, puente MQTT↔navegador y API (/api/terminar, /api/accidente_real, /api/modelos…)"],
    ["Visualización 3D", "Three.js 0.160.0 (importmap desde unpkg); Leaflet + OSM (mapa 2D)",
     "Gemelo digital de Bocagrande con tráfico y peatones simulados"],
    ["Datos geográficos", "OpenStreetMap vía API Overpass", "Red vial, sentidos, carriles, edificios y semáforos"],
    ["Ciencia de datos", "NumPy 2.3.5; pandas 2.3.3; SciPy 1.18.1; Matplotlib",
     "Limpieza de datos, Dijkstra sobre matrices dispersas, k-means y gráficas"],
    ["Grafos", "NetworkX 3.7", "k rutas más cortas de Yen (shortest_simple_paths)"],
    ["Aprendizaje profundo", "PyTorch 2.15.0 (CPU)", "T-GCN (predicción de colas) y Double DQN (control semafórico)"],
    ["Visión por computador", "OpenCV 5.1 (FaceDetectorYN = YuNet 2023mar, FaceRecognizerSF = SFace 2021dec)",
     "Caso 2: detección de rostros y vector de 128 números para compararlos"],
    ["Lenguaje natural", "Ollama 0.40 con Llama 3.2 3B (local); API de bots de Telegram",
     "Redacción de los avisos y envío a los grupos de la Ciudadanía y de la Policía"],
    ["Simulación de tráfico", "Eclipse SUMO 1.28.0 (netconvert, libsumo/TraCI)",
     "Red microscópica de Bocagrande para entrenar y comparar controladores"],
    ["Datos abiertos", "API Socrata (SODA) de datos.gov.co; portal MEData de Medellín",
     "Accidentes, registro de semáforos y flujo vehicular reales"],
    ["Hardware", "ESP32 DevKit V1; Arduino IDE; librerías WiFi.h y PubSubClient",
     "Cruce físico (cruce1) con 2 semáforos y botón de accidente"],
    ["Control de versiones", "Git y GitHub (repositorio privado)", "Código, memoria aprendida y modelos entrenados"],
    ["Diseño y documentación", "draw.io, Microsoft Word y Excel",
     "Diagramas de clases y de secuencia, documento del problema y checklist"],
    ["Asistencia de desarrollo", "Claude Code (Anthropic)", "Apoyo en programación, pruebas y documentación"],
], [3.0, 6.2, 7.8])

# =========================== 4. AGENTES ===========================
P("4. Los siete agentes", H1)
P("Cada agente es un <i>spade.agent.Agent</i> con JID <i>nombre@localhost</i>. Su tipo sigue la clasificación de "
  "Russell y Norvig. Todas las reglas y parámetros están centralizados en <i>agentes/conocimiento.py</i>.")
tabla([
    ["Agente", "Tipo", "Percibe", "Decide / hace"],
    ["Vigilancia", "Reactivo simple", "MQTT sma/eventos/#",
     "Convierte cada evento en un mensaje AlertaNovedad al Clasificador."],
    ["Clasificador de alertas", "Basado en objetivos", "AlertaNovedad",
     "Tabla de reglas: accidente → (Movilidad, Alta); congestión → (Movilidad, Media); aglomeración → (Mixta, Media); "
     "placa reportada → (Seguridad, Alta); persona solicitada → (Seguridad, Crítica)."],
    ["Coordinador", "Basado en utilidad + modelo matemático + aprendizaje",
     "Alerta; estado vial del Consultor; modelo de rutas",
     "Genera planes candidatos y evalúa cada uno con Frank-Wolfe (TSTT). Elige el de mayor utilidad, propone 3 rutas "
     "(Yen) y ordena al Semafórico y al Comunicador. Aprende el impacto real de cada plan por situación (ε-greedy)."],
    ["Consultor", "Reactivo basado en modelos", "MQTT sma/sensores/&lt;cruce&gt;",
     "Mantiene el modelo del tráfico (15 min de historia, caché de 5 s). Responde el nivel (bajo/medio/alto) y la cola "
     "media."],
    ["Predictor", "Híbrido: modelos + utilidad (red neuronal)", "Colas de los 5 cruces",
     "La T-GCN predice las colas a 12, 24 y 36 s y envía PrediccionColas al Semafórico cada 12 s. Calcula la ruta de "
     "huida con Yen y una confianza logit. Mide su error en vivo."],
    ["Semafórico", "Objetivos + aprendizaje por refuerzo (DQN y Q-learning)", "Colas, predicción, vecinos, plan",
     "Cada 12 s elige normal, prioridad_a o prioridad_b por cruce con la DQN, protegida por una capa de seguridad. "
     "La tabla Q aprende fuera de política y es el respaldo. Aplica los planes y publica los modos."],
    ["Comunicador", "Basado en objetivos", "Orden del Coordinador",
     "Un LLM local redacta el aviso (Ciudadanía: plan y rutas sugeridas; Policía: alias, ruta de huida e "
     "intercepción) y el bot de Telegram lo envía. Si el LLM cambia datos se usa la plantilla."],
], [2.6, 3.2, 3.4, 7.8])

P("Catálogo de planes del Coordinador", H2)
tabla([
    ["Plan", "Impacto previo", "Costo", "Semáforos / efecto"],
    ["Solo avisar", "0,30", "0,00", "Sin cambios; solo aviso"],
    ["Desvío local", "0,55", "0,15", "Prioridad al acceso no bloqueado"],
    ["Contraflujo Carrera 1 (salida)", "0,95", "0,35",
     "cruce4 prioridad_a, cruce3 y cruce5 prioridad_b; la Carrera 1 queda solo de salida (2 carriles)"],
    ["Contraflujo Carrera 1 (entrada)", "0,90", "0,35", "cruce5 prioridad_a, cruce3 prioridad_b; ingreso solo por la Carrera 1"],
    ["Verde largo vía principal", "0,70", "0,20", "prioridad_a en el cruce congestionado"],
    ["Fase peatonal extendida", "0,70", "0,25", "Todo rojo de 8 s en cada cambio"],
    ["Semáforos optimizados", "0,65", "0,10", "Modos elegidos por el modelo minimizando el TSTT (descenso por coordenadas)"],
    ["Cerco semafórico", "0,90", "0,50", "A y B en rojo; aviso solo a la Policía (Caso 2)"],
], [4.6, 2.2, 1.6, 8.6])
P("Utilidad de un plan:", N)
P("U = (0,6 · beneficio del modelo + 0,4 · impacto aprendido) · factor de tráfico − 0,5 · costo", FORM)
P("El beneficio es la reducción relativa del TSTT frente a no actuar. Si ningún plan mejora más del 0,5 %, el "
  "beneficio es 0. El impacto aprendido se corrige tras cada plan comparando las colas medidas antes y durante el "
  "evento: impacto ← impacto + 0,3 · efecto · margen.")

# =========================== 5. COMUNICACIÓN ===========================
P("5. Comunicación: contrato MQTT y mensajes FIPA-ACL", H1)
tabla([
    ["Tema MQTT", "Publica", "Escucha", "Contenido"],
    ["sma/eventos/&lt;cruce&gt;", "ESP32, simulador, panel", "Vigilancia",
     "accidente o JSON {tipo, sentido, arista, real, alerta}"],
    ["sma/sensores/&lt;cruce&gt;", "Panel (medido en el gemelo)", "Semafórico, Consultor, Predictor",
     "{\"A\": n, \"B\": n} cada 2 s"],
    ["sma/semaforo/&lt;cruce&gt;", "Semafórico", "ESP32, simulador",
     "normal | prioridad_a | prioridad_b | peatonal | cerco | corredor"],
    ["sma/estado/&lt;cruce&gt;", "ESP32, simulador", "Panel", "Retenido {modo, A, B}"],
    ["sma/plan/&lt;cruce&gt;", "Semafórico", "Panel (gemelo 3D)",
     "{plan, nombre, semaforos, alerta, evento, sentido, duracion, activo, rutas}"],
    ["sma/agentes/&lt;agente&gt;", "Cada agente", "Panel /agentes", "Retenido: reglas, parámetros, estado interno"],
    ["sma/panel/log", "Todos los agentes", "Panel", "{ts, agente, texto}"],
], [3.6, 3.4, 3.4, 6.6])
tabla([
    ["Emisor → Receptor", "Performativa", "Acción"],
    ["Vigilancia → Clasificador", "inform", "AlertaNovedad"],
    ["Clasificador → Coordinador", "inform", "Alerta {id, tipo, prioridad, ubicacion…}"],
    ["Coordinador ↔ Consultor", "query-ref / inform", "consultarEstadoVial, consultarColaMedia / EstadoTrafico"],
    ["Coordinador ↔ Predictor", "request / inform", "predecirRutaHuida / Ruta {cruces, confianza}"],
    ["Predictor → Semafórico", "inform", "PrediccionColas {cruces: {c: (A, B)}, pasos}"],
    ["Coordinador → Semafórico", "request", "activarPlan, activarCerco, restaurarPlanNormal"],
    ["Semafórico → Coordinador", "inform", "ResultadoAccion {accion, estado}"],
    ["Coordinador → Comunicador", "request", "comunicar / notificarPolicia {receptor, texto, rutas}"],
], [5.0, 3.2, 8.8])
P("Todos los mensajes llevan la metadata performative, ontology=\"sma-ctg\" y action, con cuerpo JSON. "
  "Los callbacks de paho corren en otro hilo y pasan los datos al bucle asyncio con "
  "<i>loop.call_soon_threadsafe</i>.")
historia.append(PageBreak())

# =========================== 6. DATOS ===========================
P("6. Datos reales y su depuración", H1)
P("El script <i>modelos/datos_reales.py</i> descarga las fuentes (con reintentos), las limpia y genera "
  "<i>modelos/datos/reporte_limpieza.md</i> con las filas que eliminó o corrigió cada regla. "
  "Los archivos crudos no se suben a Git.")
tabla([
    ["Fuente", "Contenido", "Uso"],
    ["Accidentes de tránsito de Cartagena (datos.gov.co, 5z62-bn99, DATT)",
     "8.066 siniestros de 2020 a 2025 con fecha, hora, gravedad, clase, vías y coordenadas",
     "Eventos reales: dónde y cuándo ocurren, botón «Accidente real» y escenarios de SUMO"],
    ["Registro de semaforización de Cartagena (mybb-py6y)", "90 semáforos oficiales",
     "Validación de los 5 cruces del gemelo"],
    ["Velocidad e intensidad vehicular de Medellín (SIMM, MEData)",
     "431.490 registros por carril y hora (553 carriles, 49 corredores)",
     "Entrenar y validar la T-GCN con flujo real; perfil horario de demanda"],
    ["OpenStreetMap (Overpass)", "158 nodos, 219 tramos dirigidos, 924 edificios, 129 senderos, 14 pasos peatonales",
     "Grafo vial del modelo de rutas, de SUMO y del gemelo 3D"],
], [5.0, 6.0, 6.0])
P("Reglas de limpieza aplicadas", H2)
tabla([
    ["Conjunto", "Regla", "Filas afectadas"],
    ["Accidentes", "Duplicados por número de IPAT", "573 eliminadas"],
    ["Accidentes", "Hora guardada como 1899-12-31Thh:mm convertida a hora real", "Todas"],
    ["Accidentes", "Coordenadas nulas o fuera de Cartagena", "69 eliminadas"],
    ["Accidentes", "Ubicación por distancia punto-segmento a la vía del gemelo (&lt; 60 m)",
     "204 ubicados; 18 asignados a un cruce (&lt; 80 m), 8 de ellos en el cruce5"],
    ["Semáforos", "Cruce por nombres de vías (Carrera 2/3 × Calle 6/10/15)",
     "Los 5 cruces son oficiales (códigos 1024, 1038, 1021, 1022 y 1132)"],
    ["SIMM", "Fechas mixtas (texto y serie de Excel con coma decimal)", "357.059 convertidas"],
    ["SIMM", "Dato marcado como falso o no validado (F)", "8.669"],
    ["SIMM", "Intensidad negativa o mayor de 2.200 veh/h por carril", "1.193"],
    ["SIMM", "Velocidad mayor de 130 km/h", "168"],
    ["SIMM", "Velocidad 0 con vehículos pasando", "1.211"],
    ["SIMM", "Atípicos fuera de 3 × IQR por carril y hora", "2.335 marcados como hueco"],
    ["SIMM", "Ventana del mejor mes con cobertura ≥ 80 % (julio de 2020, 744 h)", "156 estaciones conservadas"],
    ["SIMM", "Huecos ≤ 2 h interpolados; los largos con el promedio por hora y día",
     "3.006 interpolados y 3.459 imputados"],
    ["Todos", "División cronológica 70/15/15 (sin mezclar el futuro en el entrenamiento)", "-"],
], [2.4, 9.6, 5.0])
P("<b>Corrección de la red de OSM (21-nov-2026).</b> Al compararla con Google Street View se encontró que OSM traía "
  "la Av. San Martín entre la Calle 10 y la Calle 10A con una calzada de salida paralela, y una incorporación hacia "
  "el Centro en la Y de la Calle 10A. En la realidad ese tramo es una sola calzada de entrada y en la Y solo se cruza "
  "hacia Bocagrande; la salida es por la calzada junto a la Base Naval (desde la Calle 10A) y por la Carrera 1 y la "
  "Calle 15. La corrección está en <i>preparar_bocagrande.py</i> (función <i>corregir_sentidos</i>) y con esa red se "
  "regeneraron SUMO, la ubicación de los accidentes y las dos redes neuronales.")
P("Cartagena no publica aforos vehiculares abiertos. Por eso se usa el SIMM de Medellín para aprender la dinámica "
  "del flujo y su forma horaria normalizada (pico a las 17:00; a las 3:00 la demanda es el 12 % del pico). "
  "Esa forma escala la demanda de Bocagrande. El informe declara esta decisión.")

# =========================== 7. RUTAS ===========================
P("7. Modelo matemático de rutas (agentes/rutas.py)", H1)
P("La clase <i>RedVial</i> construye el mismo grafo dirigido que el gemelo 3D. Replica los tramos de reserva para "
  "el contraflujo, la geografía de Bocagrande (salida por la Av. San Martín, Carrera 1, Calle 10) y los cierres. "
  "Cada plan candidato se convierte en un escenario: carriles, modos semafóricos, salidas habilitadas y la "
  "cierre del tramo accidentado (como en la realidad, el accidente cierra la vía).")
P("Capacidad y tiempo libre", H2)
P("c = carriles × 1.800 veh/h × (g / C);&nbsp;&nbsp;&nbsp;&nbsp; t<sub>0</sub> = L / v<sub>libre</sub> + espera de rojo (Webster)", FORM)
P("La fracción de verde g/C depende del modo del semáforo: normal 8/6, prioridad_a 20/5, prioridad_b 5/20, más "
  "amarillos y todo rojo.")
P("Función de demora de Akçelik (1991), por defecto", H2)
P("t = t<sub>0</sub> + 0,25 · T · [ (z − 1) + √( (z − 1)<super>2</super> + 8 · J · z / (c · T) ) ],   "
  "z = v / c,  T = 1 h,  J = 0,4", FORM)
P("Se eligió Akçelik porque modela las colas en vías urbanas con semáforos. La función BPR "
  "(t = t<sub>0</sub> · [1 + 0,15 · (v/c)<super>4</super>]) quedó como opción, ya que subestimaba en +35 s la "
  "demora del accidente.")
P("Equilibrio de usuario (Wardrop) por Frank-Wolfe", H2)
vinetas([
    "<b>Demanda.</b> Matriz origen-destino entre 8 zonas (k-means sobre los nodos), con este reparto: salir 30 %, "
    "entrar desde el Centro 22 %, entrar por el norte 8 % e interna 40 %. Se calibra por bisección para que la "
    "Av. San Martín de salida quede en v/c = 0,9 en la hora pico.",
    "<b>Iteración.</b> Todo-o-nada con Dijkstra (SciPy, matriz dispersa) y búsqueda lineal por bisección. "
    "Termina con una brecha relativa menor que 10<super>−3</super> o a las 120 iteraciones.",
    "<b>Detalles del grafo.</b> Los tramos paralelos se reducen al más rápido y un sumidero virtual representa "
    "las salidas.",
    "<b>Viajes sin ruta.</b> Si un plan deja un viaje sin ruta interna, se penaliza con 600 s (rodeo por fuera de "
    "Bocagrande).",
    "<b>Indicador.</b> TSTT = Σ v<sub>a</sub> · t<sub>a</sub>(v<sub>a</sub>), el tiempo total del sistema en "
    "veh·h.",
])
P("Rutas propuestas y semáforos optimizados", H2)
vinetas([
    "<b>Yen (k = 3).</b> networkx.shortest_simple_paths sobre los tiempos de equilibrio. Las rutas se publican "
    "con el plan y el gemelo las dibuja.",
    "<b>Semáforos optimizados.</b> Descenso por coordenadas sobre el modo de cada cruce (normal, prioridad_a, "
    "prioridad_b), re-equilibrando y aceptando el cambio solo si baja el TSTT. Con el mismo método se ajustan "
    "los semáforos del contraflujo. Con un accidente de salida en el cruce5 en hora pico (el accidente cierra el tramo), el TSTT es 455,1 veh·h sin actuar, 387,0 con los semáforos optimizados y 281,9 con el contraflujo.",
    "<b>Ruta de huida (Caso 2).</b> Yen hacia la salida de Bocagrande, con confianza logit entre las rutas.",
    "<b>Ejecución.</b> Corre en <i>asyncio.to_thread</i> (2 a 4 s por alerta) para no bloquear a los agentes. "
    "Si falla, el Coordinador vuelve a la utilidad por reglas.",
])
historia.append(PageBreak())

# =========================== 8. REDES ===========================
P("8. Redes neuronales", H1)
P("8.1 T-GCN del Predictor (modelos/tgcn.py)", H2)
P("Red espacio-temporal de Zhao et al. (2019) implementada en PyTorch puro, porque torch-geometric falla en Windows "
  "con Python 3.13. Combina una convolución de grafo, que capta la influencia de los vecinos, con una celda GRU, "
  "que capta la historia.")
P("GC(X) = Â · X · W<sub>vecinos</sub> + X · W<sub>propio</sub>,&nbsp;&nbsp;&nbsp; Â = D<super>−1/2</super> (A + I) "
  "D<super>−1/2</super>", FORM)
P("u, r = σ(GC([X<sub>t</sub>, H<sub>t−1</sub>]));  c = tanh(GC([X<sub>t</sub>, r·H<sub>t−1</sub>]));  "
  "H<sub>t</sub> = u·H<sub>t−1</sub> + (1 − u)·c", FORM)
vinetas([
    "<b>Grafo.</b> Disperso, con los k vecinos más cercanos y peso gaussiano exp(−d²/σ²), donde σ es la mediana "
    "de las distancias (DCRNN). El término W<sub>propio</sub> (estilo GraphSAGE) evita el sobre-suavizado que "
    "tenía el grafo denso.",
    "<b>Ablación.</b> Para medir el aporte del grafo se entrena también una GRU sin grafo (Â = 0).",
    "<b>Entrenamiento.</b> Adam, pérdida MAE, parada temprana con paciencia de 20 sobre validación y datos "
    "normalizados por nodo.",
])
tabla([
    ["Especificación", "Bocagrande (agente)", "Medellín SIMM (validación)"],
    ["Datos", "Colas de SUMO: 8.910 lecturas, 45 episodios", "Intensidad horaria real, 156 estaciones, julio 2020"],
    ["Nodos / k vecinos", "10 accesos (5 cruces × A/B) / 3", "156 estaciones / 4"],
    ["Paso / ventana / horizonte", "12 s / 15 pasos (3 min) / 3 pasos (36 s)", "1 h / 12 h / 3 h"],
    ["Capa oculta", "32", "32"],
    ["Muestras (entrenamiento/validación/prueba)", "5.693 / 1.217 / 1.233", "70 / 15 / 15 % cronológico"],
    ["Archivo", "modelos/entrenados/tgcn_bocagrande.pt", "modelos/entrenados/tgcn_simm.pt"],
], [5.2, 6.0, 5.8])
tabla([
    ["Modelo (conjunto de prueba)", "MAE Bocagrande (veh)", "RMSE", "MAE SIMM (veh/h)", "RMSE"],
    ["Persistencia (ingenuo)", "1,42", "2,21", "306,1", "504,3"],
    ["Promedio histórico (misma hora y día)", "2,14", "3,83", "196,3", "356,1"],
    ["GRU sin grafo (ablación)", "1,24", "1,90", "157,3", "286,0"],
    ["<b>T-GCN (GCN + GRU)</b>", "<b>1,21</b>", "<b>1,87</b>", "163,3", "295,1"],
], [6.0, 3.0, 2.0, 3.2, 2.8])
P("En Bocagrande la T-GCN es el mejor modelo. En Medellín supera ampliamente a la persistencia (−46,7 %) y al "
  "promedio histórico, pero queda 4 % por detrás de la GRU sin grafo. Las estaciones del SIMM están en corredores "
  "distintos y la cercanía geográfica aporta poca información, así que el resultado se reporta tal como salió.")
for nombre, pie in (("prediccion_bocagrande.png", "Figura 2. Colas reales y predichas por la T-GCN en Bocagrande."),
                    ("prediccion_simm.png", "Figura 3. Flujo real y predicho por la T-GCN en estaciones del SIMM.")):
    if (ENT / nombre).exists():
        imagen(ENT / nombre, 15, pie)

P("8.2 Double DQN del Semafórico (modelos/dqn.py)", H2)
P("y = r + γ · Q<sub>θ⁻</sub>(s', argmax<sub>a'</sub> Q<sub>θ</sub>(s', a'))      "
  "(la red en línea elige la acción y la red objetivo la evalúa)", FORM)
tabla([
    ["Elemento", "Especificación"],
    ["Estado (13 rasgos, vector_estado)",
     "Cola A, cola B, A − B, variación de A y B, predicción de la T-GCN para A y B, cola media de los vecinos, modo "
     "actual (one-hot de 3) y hora del día (seno y coseno), normalizados. Es idéntico en SUMO y en el agente."],
    ["Acciones", "normal, prioridad_a, prioridad_b (cada 12 s)"],
    ["Red", "Perceptrón 13 → 64 → 64 → 3 (ReLU), parámetros compartidos por los 5 cruces"],
    ["Recompensa", "−(cola propia + 0,3 · cola de los vecinos) − penalización por cambiar de modo"],
    ["Hiperparámetros", "γ = 0,9; Adam lr = 5·10<super>−4</super>; memoria de 60.000 experiencias; lote 64; "
     "sincronización de la red objetivo cada 500 pasos; ε-greedy decreciente"],
    ["Entrenamiento", "60 episodios en SUMO con hora aleatoria y accidentes reales; archivo dqn_semaforico.pt"],
    ["Capa de seguridad en el agente",
     "Bono de 0,3 al modo sugerido por el plan, histéresis (solo cambia si Q mejora más de 0,05), normal con el cruce "
     "vacío y modos bloqueados (cerco, peatonal, corredor) que la red no puede cambiar"],
    ["Respaldo", "Tabla Q (25 estados por cubetas de cola, α = 0,2, γ = 0,6) que sigue aprendiendo fuera de política. "
     "Si falta PyTorch o el .pt, el agente usa la tabla."],
], [4.2, 12.8])
historia.append(PageBreak())

# =========================== 9. SUMO ===========================
P("9. Simulación microscópica con SUMO", H1)
vinetas([
    "<b>modelos/sumo_red.py.</b> Genera nodos y tramos XML desde el mismo grafo y la red bocagrande.net.xml "
    "(netconvert), con un semáforo por cada uno de los 5 cruces.",
    "<b>modelos/entorno_sumo.py.</b> Controla los semáforos con libsumo (setRedYellowGreenState) usando los mismos "
    "modos y tiempos del ESP32.",
    "<b>Sensor de cola.</b> Es el mismo del gemelo: vehículos con v &lt; 2 m/s a menos de 100 m de la línea de pare.",
    "<b>Demanda.</b> Es la del modelo de rutas, escalada con el perfil horario del SIMM.",
    "<b>Accidentes.</b> Se insertan accidentes reales del DATT como un vehículo detenido en cada carril del tramo "
    "(el accidente cierra la vía); el tramo se marca como intransitable y los demás vehículos recalculan su ruta.",
    "<b>Velocidad.</b> Sin interfaz gráfica, simular una hora toma unos 40 s.",
])
P("Comparación de controladores", H2)
P("Se compararon 4 controladores en los mismos 12 escenarios: horas 8, 13, 17 y 19, combinadas con sin accidente, "
  "accidente en el cruce5 (salida) y un accidente real. Cada escenario dura 30 min y todos usan las mismas semillas.")
filas = [["Controlador", "Demora (veh·h)", "Viaje medio (s)", "Llegados", "Cola media (veh)", "Mejora vs. fijo"]]
try:
    comp = json.loads((ENT / "comparacion_controladores.json").read_text(encoding="utf-8"))
    for r in sorted(comp["resumen"], key=lambda r: r["demora_veh_h"]):
        filas.append([r["controlador"], f'{r["demora_veh_h"]:.2f}'.replace(".", ","),
                      f'{r["viaje_medio_s"]:.1f}'.replace(".", ","), f'{r["llegados"]:.0f}',
                      f'{r["cola_media"]:.2f}'.replace(".", ","), f'{r["mejora_demora_vs_fijo_%"]:.1f} %'.replace(".", ",")])
except Exception:
    pass
tabla(filas, [5.2, 2.4, 2.4, 2.0, 2.5, 2.5])
if (ENT / "comparacion_controladores.png").exists():
    imagen(ENT / "comparacion_controladores.png", 15, "Figura 4. Comparación de controladores en SUMO.")

# =========================== 10. GEMELO ===========================
P("10. Gemelo digital 3D y panel web", H1)
tabla([
    ["Componente", "Especificación"],
    ["Escena", "Three.js 0.160.0. Coordenadas en metros con origen en 10.4035, −75.5550 (el norte es −z). "
     "Incluye 924 edificios extruidos, vías con carriles y sentidos de OSM, 5 cruces y 7 semáforos."],
    ["Tráfico", "Hasta 380 carros en la hora pico, con densidad según la hora y el perfil del SIMM. Los peatones usan "
     "senderos y pasos. Rutas con Dijkstra que respeta los cierres y evita vueltas en U."],
    ["Escala", "Vehículos ×1,5 y peatones ×2, a propósito, para que se vean junto a las torres."],
    ["Eventos", "Accidente (con ambulancia), accidente real en su tramo real, congestión, aglomeración y cerco "
     "(2 patrullas). Cada evento o plan dura 5 min."],
    ["Rutas y accidentes", "Con un plan activo, los carros que entran o salen siguen las 3 rutas del Coordinador, "
     "repartidos con el logit del modelo. Un accidente cierra el tramo: chocados en todos los carriles, cola detrás y "
     "nadie más entra; al retirarlo se reabre y todos recalculan su ruta."],
    ["Contraflujo", "Tramos de reserva en sentido contrario en la Carrera 1 y la Calle 10, préstamo de carril y "
     "reencaminamiento de los carros."],
    ["Sensores", "Cada 2 s, las colas por acceso (v &lt; 2 m/s a menos de 100 m) por WebSocket hacia "
     "sma/sensores/&lt;cruce&gt;."],
    ["Rutas sugeridas", "Dibuja las polilíneas que propone el Coordinador para los planes que no son contraflujo."],
    ["Panel FastAPI", "Puente MQTT↔WebSocket con historial; selector de hora; botones de accidente, accidente real y "
     "terminar contingencia; API /api/datos_reales, /api/accidente_real, /api/modelos y /api/terminar."],
    ["Página /agentes", "Mente de cada agente: reglas, parámetros, tabla Q, controlador activo, predicción frente a "
     "la medición en vivo, última decisión del Coordinador (TSTT, viaje, beneficio, rutas) y métricas de los modelos."],
    ["Mapa 2D", "mapa2d.html con Leaflet y teselas de OpenStreetMap."],
], [3.4, 13.6])

# =========================== 11. HARDWARE ===========================
P("11. Maqueta física (ESP32)", H1)
tabla([
    ["Elemento", "Especificación"],
    ["Placa", "ESP32 DevKit V1 con WiFi y cliente MQTT PubSubClient (id esp32-cruce1)"],
    ["Semáforo A (vía principal)", "Rojo GPIO 25, amarillo GPIO 26, verde GPIO 27"],
    ["Semáforo B (vía secundaria)", "Rojo GPIO 32, amarillo GPIO 33, verde GPIO 13"],
    ["Botón de accidente", "GPIO 4 a GND (INPUT_PULLUP); publica sma/eventos/cruce1"],
    ["Indicador", "LED azul GPIO 2 encendido cuando está conectado a MQTT"],
    ["Cruce", "cruce1 = Av. San Martín × Calle 6 (punto crítico citado por el DATT)"],
], [5.0, 12.0])
tabla([
    ["Modo", "Fases y tiempos"],
    ["normal", "A verde 8 s → A amarillo 2 s → todo rojo 1 s → B verde 6 s → B amarillo 2 s → todo rojo 1 s (20 s)"],
    ["prioridad_a / prioridad_b", "Mismas 6 fases con A 20 s / B 5 s, o A 5 s / B 20 s"],
    ["peatonal", "Todo rojo de 8 s en cada cambio"],
    ["corredor", "A verde fijo, B rojo"],
    ["cerco", "A y B en rojo"],
], [4.2, 12.8])
P("El simulador de ciudad (<i>simulador_ciudad.py</i>) implementa exactamente el mismo contrato MQTT y los mismos "
  "ciclos, de modo que los agentes no distinguen un cruce real de uno virtual. Entre modos cíclicos se conserva la "
  "fase y solo cambian los tiempos.")

# =========================== 12. CASO 2 ===========================
P("12. Caso 2: persona solicitada y retención semafórica", H1)
P("Una webcam hace de cámara de un cruce (cam1 = cruce1, cam2 = cruce3, cam3 = cruce5). El programa "
  "<i>vision/camara.py</i> detecta los rostros con <b>YuNet</b> y calcula con <b>SFace</b> un vector de 128 números "
  "por rostro, que publica en <i>sma/camaras/&lt;cámara&gt;</i>. No se guardan imágenes.")
vinetas([
    "<b>Vigilancia → Consultor.</b> El Consultor compara el vector con el registro por similitud coseno. Si supera "
    "0,363 (umbral recomendado por OpenCV para SFace) responde «posible coincidencia» con el alias; si no, el "
    "vector se descarta sin guardarse.",
    "<b>Predictor.</b> Calcula las 3 rutas de huida más rápidas hacia la salida de Bocagrande (Yen) y la "
    "probabilidad de cada una con un modelo logit sobre su tiempo (Dial, 1971). Para cada ruta da los cruces, el "
    "acceso (A/B) por el que llegaría y los segundos hasta cada uno.",
    "<b>Coordinador.</b> Arma la retención: <b>cerco</b> (todo rojo) en cada cruce de la ruta más probable y rojo "
    "largo solo para el acceso del solicitado en las rutas alternas con probabilidad ≥ 0,15. El plan va marcado "
    "como persecución y la DQN no cambia esos modos.",
    "<b>Comunicador.</b> Avisa solo a la Policía con el alias, la ruta probable, el punto de intercepción sugerido "
    "(el último cruce de la ruta) y el tiempo estimado de llegada.",
])
P("Registro con consentimiento: <i>camara.py --registrar ALIAS --consentimiento</i> toma 15 muestras y guarda solo "
  "su vector promedio en <i>vision/registro/</i>, carpeta que no se sube a Git. <i>--borrar ALIAS</i> lo elimina.")

# =========================== 13. COMUNICADOR ===========================
P("13. Comunicador: LLM local y Telegram", H1)
vinetas([
    "<b>Redacción.</b> Ollama sirve Llama 3.2 3B en el portátil (gratis y sin enviar datos fuera) con temperatura 0. "
    "Las instrucciones exigen español claro, máximo 60 palabras y no inventar datos.",
    "<b>Control del texto.</b> El código verifica que el LLM conserve los cruces, los números de calles y cifras, "
    "el alias y «posible coincidencia». Si falla, tarda más de 25 s o cambia datos, se envía la plantilla.",
    "<b>Reglas que no dependen del LLM.</b> Una alerta de Seguridad va siempre solo al grupo de la Policía. A la "
    "Ciudadanía no se le muestran los códigos internos de los cruces.",
    "<b>Envío.</b> Bot de Telegram con el token y los grupos en <i>.env</i> (no se versiona). Las pruebas "
    "automáticas no escriben a los grupos reales (SMA_SIN_TELEGRAM).",
])

# =========================== 14. PARÁMETROS ===========================
P("14. Parámetros principales (agentes/conocimiento.py)", H1)
tabla([
    ["Parámetro", "Valor", "Significado"],
    ["UMBRAL_NIVEL", "medio 4 / alto 10", "Vehículos en cola (A + B) para clasificar el estado vial"],
    ["SEGUNDOS_PLAN", "300", "Duración de cada plan (5 min)"],
    ["EPSILON_PLANES", "0,25 → 0,05 (×0,9)", "Exploración del Coordinador; nunca en alertas de Seguridad"],
    ["ALFA_PLANES", "0,3", "Peso de cada experiencia nueva al corregir el impacto"],
    ["FUNCION_COSTO", "akcelik (T = 1 h, J = 0,4)", "Función de demora del modelo de rutas"],
    ["FLUJO_SATURACION", "1.800 veh/h/carril", "Capacidad con verde (HCM)"],
    ["VC_PICO_OBJETIVO", "0,90", "Calibración de la demanda en la hora pico"],
    ["FW_TOLERANCIA / FW_MAX_ITER", "10<super>−3</super> / 120", "Convergencia de Frank-Wolfe"],
    ["K_RUTAS", "3", "Rutas alternativas que propone el Coordinador"],
    ["DISPERSION_RUTAS_MIN", "0,5 min", "Reparto logit de los conductores entre las rutas (Dial 1971)"],
    ["CAPACIDAD_RESIDUAL_ACCIDENTE", "0 (cierre)", "El accidente cierra el tramo en el modelo, el 3D y SUMO"],
    ["PESO_MODELO", "0,6", "Peso del modelo frente a lo aprendido en la utilidad"],
    ["INTERVALO_CONTROL", "12 s", "Periodo de decisión del Semafórico"],
    ["Q_ALFA / Q_GAMMA / ε", "0,2 / 0,6 / 0,15 → 0,02", "Q-learning de respaldo"],
    ["MARGEN_CAMBIO / DQN_MARGEN", "0,3 / 0,05", "Histéresis de la tabla Q y de la DQN"],
    ["DQN_PESO_VECINOS / DQN_BONO_PLAN", "0,3 / 0,3", "Recompensa cooperativa y preferencia por el plan"],
    ["UMBRAL_SIMILITUD_ROSTRO", "0,363", "Similitud coseno mínima para «posible coincidencia» (SFace)"],
    ["PROB_MINIMA_RUTA_HUIDA", "0,15", "Rutas alternas de huida que también se vigilan"],
    ["SEGUNDOS_ENTRE_ALERTAS_PERSONA", "300", "No repetir la alerta de la misma persona durante el cerco"],
    ["LLM_MAX_PALABRAS / LLM_TIMEOUT_S", "60 / 25 s", "Longitud y tiempo máximo de la redacción del LLM"],
    ["AVISO_CRECIMIENTO_COLA", "4 veh", "El Predictor avisa cuando anticipa ese crecimiento"],
], [5.4, 4.2, 7.4])

# =========================== 13. EJECUCIÓN ===========================
P("15. Ejecución, pruebas y estructura", H1)
tabla([
    ["Acción", "Comando"],
    ["Instalar dependencias", "pip install -r requirements.txt   (PyTorch CPU: --index-url download.pytorch.org/whl/cpu)"],
    ["Agentes", "python agentes/sma_agentes.py   [--simular] [--duracion N] [--olvidar]"],
    ["Simulador de cruces", "python maqueta_virtual/simulador_ciudad.py --incluir-cruce1"],
    ["Panel y gemelo 3D", "python maqueta_virtual/panel/servidor_panel.py → http://localhost:8000 y /agentes"],
    ["Datos reales", "python modelos/datos_reales.py"],
    ["Red SUMO y colas", "python modelos/sumo_red.py ; python modelos/entorno_sumo.py"],
    ["Entrenar predictor", "python modelos/entrenar_predictor.py --datos bocagrande | simm"],
    ["Entrenar y comparar DQN", "python modelos/entrenar_dqn.py"],
    ["Prueba de punta a punta", "python pruebas/prueba_punta_a_punta.py ; python pruebas/prueba_caso2.py"],
    ["Cámara (Caso 2)", "python vision/camara.py --registrar ALIAS --consentimiento | --camara cam2 | --medir"],
    ["Comunicador", "python agentes/comunicador.py --buscar-chats | --probar"],
    ["Mediciones", "python mediciones/medir.py todo | latencia | informe"],
], [4.2, 12.8])
P("La prueba de punta a punta levanta el broker y los agentes y dispara un accidente real (IPAT 1338287) y un "
  "accidente en el cruce5. Luego verifica toda la cadena: Vigilancia, Clasificador, Coordinador con el modelo y las "
  "rutas, Semafórico con la DQN y Comunicador. Funciona en Windows (UTF-8, lectura sin bloqueo y "
  "CREATE_NEW_PROCESS_GROUP).")
tabla([
    ["Carpeta / archivo", "Contenido", "Líneas"],
    ["agentes/", "sma_agentes.py, conocimiento.py, aprendizaje.py, rutas.py, comunicador.py, memoria/*.json", "≈ 2.400"],
    ["modelos/", "datos_reales, tgcn, dqn, predictor_colas, sumo_red, entorno_sumo, entrenar_*; datos/, sumo/, entrenados/",
     "≈ 1.480"],
    ["maqueta_virtual/", "preparar_bocagrande.py, simulador_ciudad.py, cruces.json, bocagrande.json, panel/", "≈ 2.880"],
    ["esp32/", "esp32_mqtt_prueba.ino, esp32_cruce.ino", "≈ 310"],
    ["vision/", "camara.py (YuNet + SFace; modelos/ y registro/ ignorados por Git)", "≈ 390"],
    ["mediciones/", "medir.py: experimentos y generación de docs/resultados/", "≈ 650"],
    ["pruebas/", "prueba_punta_a_punta.py, prueba_caso2.py", "≈ 300"],
    ["docs/", "Diagramas de clases (v4) y de secuencia, documento del problema (v3), checklist, este documento", "-"],
], [3.4, 11.4, 2.2])

# =========================== 16. RESULTADOS ===========================
RES = RAIZ / "docs" / "resultados"
historia.append(PageBreak())
P("16. Resultados de las mediciones", H1)
P("Se generan con <i>python mediciones/medir.py</i> (semillas fijas, media ± intervalo de confianza del 95 %). El "
  "detalle está en <i>docs/resultados/resultados.md</i>.")
P("16.1 Caso 1: accidentes reales en SUMO", H2)
P("Hora pico (17 h), 30 min simulados y 10 accidentes reales del DATT sorteados con su frecuencia. Todas las "
  "políticas ven el mismo tráfico y el mismo accidente.")
tabla([
    ["Política", "Demora (veh·h)", "Viaje medio (s)", "Reducción de la demora frente al fijo"],
    ["Semáforos fijos", "86,7 ± 18,1", "350,9 ± 11,9", "-"],
    ["Solo el plan del Coordinador", "86,3 ± 18,5", "344,9 ± 12,0", "0,5 % ± 1,0 (6 de 10)"],
    ["Solo la DQN", "80,6 ± 19,3", "316,3 ± 9,4", "7,8 % ± 2,4 (10 de 10)"],
    ["SMA completo (DQN + plan)", "80,9 ± 19,6", "316,6 ± 9,4", "7,5 % ± 2,6 (10 de 10)"],
], [5.0, 3.2, 3.2, 5.6])
P("La mejora viene del control adaptativo (DQN). En estos accidentes, que caen en vías secundarias, el plan "
  "fijo del Coordinador aporta poco. Su valor está en el accidente que cierra la salida del cruce5, donde el "
  "contraflujo reduce el TSTT del modelo de 455 a 282 veh·h; SUMO no invierte carriles, así que ese plan no se "
  "midió allí.")
if (RES / "caso1_demora.png").exists():
    imagen(RES / "caso1_demora.png", 15, "Figura. Demora total por accidente real y política.")
P("16.2 Caso 2: retención del solicitado", H2)
tabla([
    ["Cámara (cruce)", "Huida sin retención (s)", "Con retención (s)", "Tiempo ganado (s)", "Demora extra resto (veh·h)"],
    ["cam1 (cruce1)", "481 ± 206", "921 ± 220", "441 ± 354", "36,2 ± 17,9"],
    ["cam2 (cruce3)", "415 ± 51", "1003 ± 173", "588 ± 171", "39,2 ± 10,5"],
    ["cam3 (cruce5)", "21 ± 12", "298 ± 34", "277 ± 29", "16,4 ± 1,6"],
], [3.2, 3.6, 3.0, 3.0, 4.2])
P("La retención le da a la Policía varios minutos para llegar al punto de intercepción. A cambio, los demás "
  "conductores acumulan demora durante el cerco; por eso dura 5 min y solo se aplica en las rutas probables.")
if (RES / "caso2_huida.png").exists():
    imagen(RES / "caso2_huida.png", 12.5, "Figura. Tiempo de huida del solicitado con y sin retención.")
P("16.3 Reconocimiento facial, latencia y Comunicador", H2)
tabla([
    ["Medición", "Resultado"],
    ["Rostros: registrados reconocidos (117 muestras, 4 personas)", "93,8 %"],
    ["Rostros: confundidos con otro registrado", "0 %"],
    ["Rostros: no registrado tomado como solicitado (falso positivo)", "0 % con 0,363 (10 % con 0,25)"],
    ["Latencia evento → semáforos", "0,5 ± 0,2 s (Caso 1); < 0,1 s (Caso 2)"],
    ["Latencia evento → aviso redactado", "21,3 ± 2,6 s (Caso 1); 19,5 ± 2,5 s (Caso 2)"],
    ["Avisos a la Ciudadanía redactados por el LLM", "10 de 10 (≈ 11 s cada uno)"],
    ["Avisos a la Policía redactados por el LLM", "0 de 3: el LLM omite cifras y sale la plantilla exacta"],
], [9.5, 7.5])
if (RES / "rostros_similitudes.png").exists():
    imagen(RES / "rostros_similitudes.png", 12.5,
           "Figura. Similitud de cada muestra con la persona correcta y con las demás; la línea es el umbral.")

# =========================== 17. ÉTICA ===========================
P("17. Consideraciones éticas, limitaciones y trabajo pendiente", H1)
vinetas([
    "<b>Caso 2 y datos personales (Ley 1581 de 2012).</b> Las «personas solicitadas» son compañeros que dieron su "
    "consentimiento (el registro lo exige). Nunca se guardan fotos: del registro solo queda un vector y los rostros "
    "que no coinciden se descartan. La alerta dice «posible coincidencia» y solo se notifica a la Policía; en las "
    "mediciones solo se guardaron similitudes y los nombres se publican como P1, P2… El registro no se sube a Git.",
    "<b>Uso del LLM.</b> El LLM corre en local, así que ningún dato sale del portátil. El código impide que cambie "
    "datos o que un aviso de Seguridad llegue a la ciudadanía.",
    "<b>Credenciales.</b> Los tokens y claves (Telegram, LLM) van en .env, que no se versiona. El WiFi está en los "
    ".ino por decisión del equipo, porque el repositorio es privado.",
    "<b>Datos de flujo.</b> Cartagena no publica aforos, así que la dinámica del flujo viene de Medellín y la "
    "demanda de Bocagrande se calibra con el modelo. Las métricas de colas se entrenaron en simulación (SUMO), no "
    "con sensores físicos.",
    "<b>Resultados honestos.</b> En el SIMM la T-GCN no supera a la GRU sin grafo; la ganancia del DQN es moderada "
    "en demora (−4,8 %) y alta en cola (−43 %). Con la salida del cruce5 cerrada por un accidente ningún controlador semafórico mejora (±1,6 %): ese caso lo resuelve el plan de contraflujo del Coordinador (TSTT de 455 a 282 veh·h).",
    "<b>Limitaciones de las mediciones.</b> La muestra de rostros es pequeña (prototipo) y las condiciones de luz y "
    "pose se mezclaron en cada toma. El plan fijo del Coordinador casi no mejora la demora frente a la DQN en "
    "accidentes de vías secundarias. El LLM de 3B casi nunca logra redactar el aviso de la Policía sin perder datos.",
    "<b>Siguientes pasos.</b> Montar los LEDs de la maqueta física, ampliar la muestra de rostros por condición, medir "
    "el contraflujo en SUMO invirtiendo carriles y redactar el informe final.",
])

# =========================== 15. REFERENCIAS ===========================
P("18. Referencias", H1)
for r in [
    "Akçelik, R. (1991). Travel time functions for transport planning purposes. Australian Road Research 21(3).",
    "Bureau of Public Roads (1964). Traffic Assignment Manual. U.S. Department of Commerce.",
    "Frank, M. y Wolfe, P. (1956). An algorithm for quadratic programming. Naval Research Logistics Quarterly 3.",
    "Wardrop, J. G. (1952). Some theoretical aspects of road traffic research. Proc. Institution of Civil Engineers.",
    "Yen, J. Y. (1971). Finding the k shortest loopless paths in a network. Management Science 17(11).",
    "Webster, F. V. (1958). Traffic Signal Settings. Road Research Technical Paper 39.",
    "Zhao, L. et al. (2019). T-GCN: A Temporal Graph Convolutional Network for Traffic Prediction. IEEE T-ITS 21(9).",
    "Kipf, T. y Welling, M. (2017). Semi-supervised classification with graph convolutional networks. ICLR.",
    "Hamilton, W. et al. (2017). Inductive representation learning on large graphs (GraphSAGE). NeurIPS.",
    "Li, Y. et al. (2018). Diffusion Convolutional Recurrent Neural Network (DCRNN). ICLR.",
    "van Hasselt, H., Guez, A. y Silver, D. (2016). Deep reinforcement learning with Double Q-learning. AAAI.",
    "Varaiya, P. (2013). Max pressure control of a network of signalized intersections. Transportation Research C 36.",
    "Russell, S. y Norvig, P. (2021). Artificial Intelligence: A Modern Approach, 4.ª ed. Pearson.",
    "Mnih, V. et al. (2015). Human-level control through deep reinforcement learning. Nature 518.",
    "Lopez, P. A. et al. (2018). Microscopic Traffic Simulation using SUMO. IEEE ITSC.",
    "Wu, W., Peng, H. y Yu, S. (2023). YuNet: A Tiny Millisecond-level Face Detector. Machine Intelligence Research 20.",
    "Zhong, Y. et al. (2021). SFace: Sigmoid-Constrained Hypersphere Loss for Robust Face Recognition. IEEE TIP 30.",
    "Dial, R. B. (1971). A probabilistic multipath traffic assignment model. Transportation Research 5(2).",
    "Meta AI (2024). Llama 3.2: lightweight open models (1B y 3B). Ejecutado con Ollama.",
    "Datos abiertos: datos.gov.co (5z62-bn99, mybb-py6y); MEData Medellín (SIMM); OpenStreetMap contributors (ODbL).",
]:
    historia.append(Paragraph(r, VIN, bulletText="•"))


def pie_pagina(c, doc):
    if doc.page == 1:
        return
    c.saveState()
    c.setFont("Arial", 7.5)
    c.setFillColor(GRIS)
    c.drawString(2 * cm, 1.2 * cm, "SMA Movilidad y Seguridad, Bocagrande · Especificaciones técnicas")
    c.drawRightString(letter[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
    c.setStrokeColor(AZUL_CLARO)
    c.line(2 * cm, letter[1] - 1.4 * cm, letter[0] - 2 * cm, letter[1] - 1.4 * cm)
    c.restoreState()


doc = SimpleDocTemplate(str(SALIDA), pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.9 * cm,
                        bottomMargin=1.9 * cm, title="Especificaciones técnicas del SMA de Bocagrande",
                        author="Equipo SMA UNICOLOMBO", subject="Herramientas, tecnologías, métodos y datos")
doc.build(historia, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
print("PDF generado:", SALIDA)
