# Genera docs/Informe_Final_SMA.pdf (reportlab).  Uso: python docs/generar_informe.py
# Estructura del checklist (tareas 5.1 a 5.8): portada, resumen, contenido, introducción, marco teórico, metodología
# GAIA, desarrollo, resultados y discusión, conclusiones, referencias (APA 7) y anexos. Las cifras se leen de
# docs/resultados/ y modelos/entrenados/ cuando existen, para que el informe no se desactualice.
import json
import math
import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

RAIZ = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
IMG = DOCS / "informe"
RES = DOCS / "resultados"
ENT = RAIZ / "modelos" / "entrenados"
SALIDA = DOCS / "Informe_Final_SMA.pdf"

F = "C:/Windows/Fonts/"
for nombre, archivo in (("Arial", "arial.ttf"), ("Arial-B", "arialbd.ttf"), ("Arial-I", "ariali.ttf"),
                        ("Arial-BI", "arialbi.ttf")):
    pdfmetrics.registerFont(TTFont(nombre, F + archivo))
registerFontFamily("Arial", normal="Arial", bold="Arial-B", italic="Arial-I", boldItalic="Arial-BI")

AZUL = colors.HexColor("#1F3A5F")
AZUL_CLARO = colors.HexColor("#E8EEF5")
GRIS = colors.HexColor("#555555")
ss = getSampleStyleSheet()
N = ParagraphStyle("n", parent=ss["Normal"], fontName="Arial", fontSize=10.5, leading=15, alignment=TA_JUSTIFY,
                   spaceAfter=6)
CELDA = ParagraphStyle("c", parent=N, fontSize=8.4, leading=10.6, alignment=0, spaceAfter=0)
CELDA_B = ParagraphStyle("cb", parent=CELDA, fontName="Arial-B", textColor=colors.white)
H1 = ParagraphStyle("h1", parent=N, fontName="Arial-B", fontSize=15, leading=19, textColor=AZUL, spaceBefore=4,
                    spaceAfter=9, alignment=0)
H2 = ParagraphStyle("h2", parent=N, fontName="Arial-B", fontSize=12, leading=15.5, textColor=AZUL, spaceBefore=9,
                    spaceAfter=4, alignment=0)
H3 = ParagraphStyle("h3", parent=N, fontName="Arial-BI", fontSize=10.5, leading=14, textColor=AZUL, spaceBefore=6,
                    spaceAfter=3, alignment=0)
PIE = ParagraphStyle("pie", parent=N, fontSize=8.8, leading=11, textColor=GRIS, alignment=TA_CENTER, spaceAfter=8)
VIN = ParagraphStyle("v", parent=N, leftIndent=14, bulletIndent=3, spaceAfter=3)
REF = ParagraphStyle("ref", parent=N, fontSize=9.8, leading=13.5, leftIndent=18, firstLineIndent=-18, alignment=0)
CODIGO = ParagraphStyle("cod", parent=N, fontName="Courier", fontSize=8.6, leading=11, alignment=0,
                        backColor=colors.HexColor("#F4F6F9"), borderPadding=4, spaceBefore=2, spaceAfter=6)

historia = []
P = lambda t, s=N: historia.append(Paragraph(t, s))
n_fig = [0]
n_tab = [0]


def vinetas(items):
    for it in items:
        historia.append(Paragraph(it, VIN, bulletText="•"))


def tabla(filas, anchos, titulo=None, repetir=1):
    if titulo:
        n_tab[0] += 1
        historia.append(Paragraph(f"<b>Tabla {n_tab[0]}.</b> <i>{titulo}</i>",
                                  ParagraphStyle("tt", parent=N, fontSize=9.5, alignment=0, spaceAfter=3)))
    datos = [[Paragraph(str(c), CELDA_B if i == 0 else CELDA) for c in f] for i, f in enumerate(filas)]
    t = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=repetir)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, AZUL_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    historia.append(t)
    historia.append(Spacer(1, 8))


def figura(flowable, pie):
    n_fig[0] += 1
    historia.append(KeepTogether([flowable, Spacer(1, 3), Paragraph(f"<b>Figura {n_fig[0]}.</b> {pie}", PIE)]))


def imagen(ruta, ancho_cm, pie, alto_max_cm=None):
    if not Path(ruta).exists():
        P(f"<i>[Falta la imagen {Path(ruta).name}]</i>")
        return
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(str(ruta)).getSize()
    ancho = ancho_cm * cm
    alto = ancho * h / w
    if alto_max_cm and alto > alto_max_cm * cm:
        alto = alto_max_cm * cm
        ancho = alto * w / h
    figura(Image(str(ruta), width=ancho, height=alto), pie)


def leer(ruta):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def coma(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


# ====================================================================== diagramas dibujados
def flecha(d, x1, y1, x2, y2, color=GRIS, ancho=1, punteada=False):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=ancho, strokeDashArray=[3, 2] if punteada else None))
    a = math.atan2(y2 - y1, x2 - x1)
    d.add(Polygon([x2, y2, x2 - 6 * math.cos(a - 0.4), y2 - 6 * math.sin(a - 0.4), x2 - 6 * math.cos(a + 0.4),
                   y2 - 6 * math.sin(a + 0.4)], fillColor=color, strokeColor=color))


def caja(d, x, y, w, h, texto, fondo="#E8EEF5", borde="#1F3A5F", tam=7.6, negrita=False):
    d.add(Rect(x, y, w, h, fillColor=colors.HexColor(fondo), strokeColor=colors.HexColor(borde), rx=4, ry=4))
    lineas = texto.split("\n")
    for i, l in enumerate(lineas):
        d.add(String(x + w / 2, y + h / 2 + (len(lineas) / 2 - i - 0.75) * (tam + 2), l,
                     fontName="Arial-B" if negrita and i == 0 else "Arial", fontSize=tam, textAnchor="middle"))


def diagrama_arquitectura():
    W = 16.5 * cm
    d = Drawing(W, 10.4 * cm)
    y0 = 236
    fuentes = [("ESP32 (cruce1 físico)\nbotón + 2 semáforos", 0), ("Simulador de cruces\nsimulador_ciudad.py", 1),
               ("Gemelo 3D (Three.js)\nsensores de cola", 2), ("Cámara web\nYuNet + SFace", 3)]
    ancho = (W - 25) / 4
    for texto, i in fuentes:
        caja(d, 5 + i * (ancho + 5), y0, ancho, 34, texto)
    caja(d, 40, 176, W - 190, 30, "Broker MQTT Mosquitto :1883  (sma/eventos, sensores, camaras, semaforo, plan)",
         fondo="#FFF4D6", borde="#B07D00", tam=7.4)
    for i in range(4):
        x = 5 + i * (ancho + 5) + ancho / 2
        flecha(d, x, y0, min(max(x, 50), W - 160), 206)
    caja(d, 5, 22, W - 10, 136, "", fondo="#F2F7F2", borde="#2E6B3A")
    d.add(String(W / 2, 146, "sma_agentes.py · 7 agentes SPADE 4.1 · mensajes FIPA-ACL sobre XMPP embebido",
                 fontName="Arial-B", fontSize=8.2, textAnchor="middle"))
    nombres = ["Vigilancia\nreactivo", "Clasificador\nobjetivos", "Coordinador\nutilidad", "Consultor\nmodelos",
               "Predictor\nT-GCN + Yen", "Semafórico\nDQN", "Comunicador\nLLM"]
    a7 = (W - 30) / 7
    for i, n in enumerate(nombres):
        caja(d, 12 + i * a7, 88, a7 - 6, 42, n, fondo="#FFFFFF", borde="#2E6B3A", tam=7.2, negrita=True)
    extras = [("rutas.py\nFrank-Wolfe, Yen", "#7A4FA0"), ("modelos/*.pt\nT-GCN y DQN", "#7A4FA0"),
              ("conocimiento.py\n+ memoria/", "#7A4FA0"), ("Ollama (Llama 3.2)\n+ bot de Telegram", "#B03A2E")]
    a4 = (W - 30) / 4
    for i, (t, b) in enumerate(extras):
        caja(d, 12 + i * a4, 32, a4 - 8, 40, t, fondo="#FFFFFF", borde=b, tam=7.2)
    flecha(d, W / 2, 176, W / 2, 158)
    d.add(String(W / 2 + 4, 164, "MQTT", fontName="Arial-I", fontSize=6.8, fillColor=GRIS))
    caja(d, W - 132, 176, 127, 30, "Panel web FastAPI\n+ WebSocket (navegador)", fondo="#E8EEF5", tam=7)
    flecha(d, W - 132, 191, W - 148, 191)
    return d


def diagrama_secuencia(actores, mensajes, alto_paso=21, ancho_cm=16.5):
    """actores: nombres; mensajes: (origen, destino, texto, tipo) con tipo msg | resp | mqtt | ext | propio."""
    W = ancho_cm * cm
    H = 46 + alto_paso * (len(mensajes) + 1)
    d = Drawing(W, H)
    paso = (W - 20) / len(actores)
    xs = [10 + paso * (i + 0.5) for i in range(len(actores))]
    colores = {"msg": colors.black, "resp": GRIS, "mqtt": colors.HexColor("#7A4FA0"),
               "ext": colors.HexColor("#2E6B3A"), "propio": colors.black}
    for x, nombre in zip(xs, actores):
        externo = nombre.startswith("~")
        nombre = nombre.lstrip("~")
        caja(d, x - paso / 2 + 3, H - 30, paso - 6, 26, nombre, fondo="#E7DDF0" if externo else "#FFF2CC",
             borde="#7A4FA0" if externo else "#B07D00", tam=6.6, negrita=True)
        d.add(Line(x, 8, x, H - 30, strokeColor=colors.HexColor("#B8B8B8"), strokeDashArray=[2, 2]))
    y = H - 30 - alto_paso
    for k, (o, de, texto, tipo) in enumerate(mensajes, 1):
        c = colores[tipo]
        if tipo == "propio":
            x = xs[o]
            d.add(Line(x, y + 3, x + 16, y + 3, strokeColor=c))
            d.add(Line(x + 16, y + 3, x + 16, y - 6, strokeColor=c))
            flecha(d, x + 16, y - 6, x + 1, y - 6, color=c)
            d.add(String(x + 20, y - 3, f"{k}. {texto}", fontName="Arial", fontSize=6.4, fillColor=c))
        else:
            flecha(d, xs[o], y - 4, xs[de], y - 4, color=c, punteada=tipo == "resp")
            medio = (xs[o] + xs[de]) / 2
            d.add(String(medio, y, f"{k}. {texto}", fontName="Arial", fontSize=6.4, fillColor=c, textAnchor="middle"))
        y -= alto_paso
    return d


def diagrama_conocidos():
    """Modelo de conocidos de GAIA: quién se comunica con quién."""
    W = 16 * cm
    d = Drawing(W, 7.2 * cm)
    pos = {"Vigilancia": (60, 150), "Clasificador": (200, 150), "Coordinador": (340, 150), "Comunicador": (400, 40),
           "Consultor": (120, 40), "Predictor": (260, 40), "Semafórico": (400, 150)}
    pos["Semafórico"] = (430, 120)
    for nombre, (x, y) in pos.items():
        caja(d, x - 48, y - 15, 96, 30, nombre, fondo="#FFF2CC", borde="#B07D00", tam=8, negrita=True)
    enlaces = [("Vigilancia", "Clasificador"), ("Vigilancia", "Consultor"), ("Clasificador", "Coordinador"),
               ("Coordinador", "Consultor"), ("Coordinador", "Predictor"), ("Coordinador", "Semafórico"),
               ("Coordinador", "Comunicador"), ("Predictor", "Semafórico")]
    for a, b in enlaces:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        ang = math.atan2(y2 - y1, x2 - x1)
        flecha(d, x1 + 50 * math.cos(ang) * 0.95, y1 + 17 * math.sin(ang), x2 - 50 * math.cos(ang) * 0.95,
               y2 - 17 * math.sin(ang), color=AZUL)
    return d


# ====================================================================== datos para las tablas
tg = leer(ENT / "metricas_bocagrande.json") or {}
tg_simm = leer(ENT / "metricas_simm.json") or {}
comp = leer(ENT / "comparacion_controladores.json") or {}
lat = leer(RES / "latencia_agentes.json") or []
com = leer(RES / "comunicador_llm.json") or []

# ====================================================================== PORTADA
historia.append(Spacer(1, 1.6 * cm))
P("FUNDACIÓN UNIVERSITARIA COLOMBO INTERNACIONAL — UNICOLOMBO",
  ParagraphStyle("u", parent=N, fontName="Arial-B", fontSize=12, alignment=TA_CENTER, textColor=AZUL))
P("Programa de Ingeniería de Sistemas · Asignatura Inteligencia Artificial",
  ParagraphStyle("u2", parent=N, fontSize=11, alignment=TA_CENTER))
historia.append(Spacer(1, 2.4 * cm))
P("Sistema Multiagente de Movilidad y Seguridad para Bocagrande, Cartagena de Indias",
  ParagraphStyle("t", parent=H1, fontSize=22, leading=28, alignment=TA_CENTER))
historia.append(Spacer(1, 0.4 * cm))
P("Informe final del proyecto", ParagraphStyle("t2", parent=H2, fontSize=14, alignment=TA_CENTER))
historia.append(Spacer(1, 2.4 * cm))
P("Presentado por:", ParagraphStyle("p0", parent=N, fontName="Arial-B", alignment=TA_CENTER))
for nombre in ("Enmanuel David Caraballo Marín", "Omar Stick Coronel Ramírez", "Mauricio Andrés Vergara Fonseca",
               "Yohan Alexander Maldonado Santana", "Deyker Fernando Gómez Caraballo"):
    P(nombre, ParagraphStyle("p1", parent=N, alignment=TA_CENTER, spaceAfter=1))
historia.append(Spacer(1, 2.6 * cm))
P("Cartagena de Indias, noviembre de 2026", ParagraphStyle("p2", parent=N, alignment=TA_CENTER, textColor=GRIS))
historia.append(PageBreak())

# ====================================================================== RESUMEN
P("Resumen ejecutivo", ParagraphStyle("hr", parent=H1, alignment=0))
P("Cartagena enfrenta trancones y accidentes en vías con semáforos de tiempo fijo, y retos de seguridad por su "
  "alta población y flujo de turistas. Este proyecto diseñó e implementó un <b>Sistema Multiagente (SMA)</b> de "
  "siete agentes que perciben el tráfico, razonan y actúan sobre los semáforos de cinco intersecciones reales del "
  "sector de Bocagrande. El diseño siguió la metodología <b>GAIA</b>; la implementación usa SPADE (mensajes "
  "FIPA-ACL), MQTT, un gemelo digital 3D construido con OpenStreetMap y un cruce físico con ESP32.")
P("Los agentes combinan reglas con modelos aprendidos y matemáticos: una red <b>T-GCN</b> predice las colas, una "
  "<b>Double DQN</b> entrenada en el simulador SUMO controla los semáforos, un modelo de equilibrio de tráfico "
  "(Frank-Wolfe con demora de Akçelik y k rutas de Yen) evalúa los planes de desvío, el reconocimiento facial "
  "(YuNet + SFace) detecta a personas solicitadas y un LLM local redacta avisos que un bot de Telegram envía. Los "
  "datos provienen de fuentes abiertas reales: accidentes del DATT, semáforos oficiales y flujo del SIMM de Medellín.")
P("Con 10 accidentes reales simulados en hora pico, el SMA redujo la demora un 7,5 % y el tiempo de viaje un 9,7 % "
  "frente a semáforos fijos, en los 10 casos. En el caso de una persona solicitada, la retención semafórica le dio a "
  "la Policía entre 4,6 y 9,8 minutos adicionales para interceptarla. El reconocimiento facial acertó el 93,8 % de "
  "las muestras sin ningún falso positivo, y los semáforos reaccionan en menos de un segundo. Se respetó la Ley 1581 "
  "de 2012: solo participaron compañeros con consentimiento y nunca se guardaron imágenes.")
P("<b>Palabras clave:</b> sistemas multiagente, GAIA, control semafórico, aprendizaje por refuerzo profundo, "
  "redes neuronales de grafos, reconocimiento facial, gemelo digital, Cartagena.")
historia.append(PageBreak())

# ====================================================================== CONTENIDO
P("Contenido", ParagraphStyle("hc", parent=H1, alignment=0))
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("toc1", parent=N, fontName="Arial-B", fontSize=10.5, leftIndent=0, spaceAfter=2),
                   ParagraphStyle("toc2", parent=N, fontSize=9.8, leftIndent=16, spaceAfter=0)]
historia.append(toc)
historia.append(PageBreak())

# ====================================================================== 1. INTRODUCCIÓN
P("1. Introducción", H1)
P("1.1 Planteamiento del problema", H2)
P("En Cartagena de Indias conviven dos problemas que afectan a ciudadanos y autoridades. El primero es la "
  "<b>movilidad</b>: los trancones son una de las quejas principales de la ciudad y las soluciones aplicadas "
  "(ampliación de vías secundarias, acompañamiento en puntos críticos, semaforización y nueva flota de buses) no "
  "aprovechan tecnologías que adapten la operación al tráfico real. Los semáforos funcionan con tiempos fijos y "
  "no reaccionan a un accidente. Entre 2020 y 2025 el DATT registró 8.066 siniestros en la ciudad; 204 de ellos "
  "ocurrieron sobre las vías de Bocagrande, un sector turístico con solo dos salidas hacia el resto de la ciudad.")
P("El segundo es la <b>seguridad</b>: la ciudad recibe muchos turistas y, aunque se reforzó la fuerza pública y se "
  "instalaron cámaras capaces de reconocer placas y rasgos, no existe un sistema que integre esa información con "
  "la operación del tráfico. Cuando una cámara detecta a una persona solicitada, la información no se usa para "
  "anticipar su ruta ni para facilitar la intercepción.")
P("1.2 Objetivos", H2)
P("<b>Objetivo general.</b> Diseñar e implementar un sistema multiagente basado en inteligencia artificial que, a "
  "escala del sector de Bocagrande, adapte los semáforos ante accidentes y congestión, y apoye a la Policía cuando "
  "una cámara detecta a una persona solicitada.")
P("<b>Objetivos específicos.</b>")
vinetas([
    "Modelar el SMA con la metodología GAIA: roles, protocolos de interacción, tipos de agente y servicios.",
    "Construir la red vial real de Bocagrande y un gemelo digital 3D, más un cruce físico con ESP32, que compartan "
    "el mismo contrato de comunicación MQTT.",
    "Implementar siete agentes en SPADE que se comuniquen con mensajes FIPA-ACL y que decidan con reglas, modelos "
    "matemáticos de tráfico y redes neuronales entrenadas.",
    "Depurar y usar datos abiertos reales (accidentes del DATT, semáforos oficiales y flujo vehicular del SIMM).",
    "Demostrar dos casos: accidente con plan de desvío (Caso 1) y persona solicitada con retención semafórica (Caso 2).",
    "Medir el sistema con experimentos reproducibles y analizar sus resultados y limitaciones.",
])
P("1.3 Justificación del uso de inteligencia artificial", H2)
P("El problema reúne las características que justifican un enfoque multiagente con IA. Es <b>distribuido</b>: "
  "cada cruce percibe solo su entorno y las decisiones locales afectan a los vecinos. Es <b>dinámico</b>: el tráfico "
  "cambia minuto a minuto y una regla fija no se adapta. Involucra <b>objetivos en conflicto</b>: dar fluidez al "
  "tráfico y, al mismo tiempo, retener a un sospechoso. Y requiere <b>interpretar señales no estructuradas</b>, como "
  "un rostro en una imagen, y comunicarse con personas en lenguaje natural. Repartir estas tareas entre agentes "
  "especializados, con un coordinador basado en utilidad, permite que cada uno use la técnica adecuada: aprendizaje "
  "por refuerzo para los semáforos, redes de grafos para predecir, optimización para las rutas, visión por "
  "computador para los rostros y un modelo de lenguaje para los avisos.")
P("1.4 Alcance y limitaciones", H2)
vinetas([
    "<b>Sector:</b> Bocagrande, con sus 5 intersecciones semaforizadas reales (verificadas con el registro oficial).",
    "<b>Escala:</b> el sistema se demuestra en un gemelo digital y en una maqueta física de un cruce; no controla "
    "semáforos reales de la ciudad.",
    "<b>Datos de flujo:</b> Cartagena no publica aforos abiertos, así que la dinámica del flujo se aprendió con datos "
    "de Medellín (SIMM) y la demanda de Bocagrande se calibró con el modelo.",
    "<b>Reconocimiento facial:</b> solo con compañeros que dieron su consentimiento; es un prototipo académico y no "
    "un sistema de vigilancia real.",
    "<b>Maqueta física:</b> el ESP32 y su código están listos y conectados; a la fecha faltan los LEDs y componentes.",
])
historia.append(PageBreak())

# ====================================================================== 2. MARCO TEÓRICO
P("2. Marco teórico", H1)
P("2.1 Agentes inteligentes", H2)
P("Un agente es una entidad que percibe su entorno mediante sensores y actúa sobre él mediante actuadores para "
  "cumplir sus objetivos (Russell &amp; Norvig, 2021). Según cómo eligen sus acciones se distinguen agentes "
  "<b>reactivos simples</b> (reglas condición-acción), <b>reactivos basados en modelos</b> (mantienen un estado "
  "interno del mundo), <b>basados en objetivos</b> (eligen acciones que conducen a una meta), <b>basados en "
  "utilidad</b> (eligen la acción que maximiza una medida de desempeño) y <b>agentes que aprenden</b>. Los siete "
  "agentes de este proyecto se clasificaron con esta taxonomía (sección 3).")
P("2.2 Sistemas multiagente", H2)
P("Un sistema multiagente está formado por varios agentes autónomos que interactúan para resolver problemas que "
  "superan las capacidades de cada uno (Wooldridge, 2009). Sus ventajas son la distribución del control, la "
  "tolerancia a fallos y la especialización. Sus retos son la coordinación, la comunicación y la resolución de "
  "conflictos entre objetivos. En tráfico, los SMA se usan para el control descentralizado de semáforos: el "
  "sistema SURTRAC de Pittsburgh, donde cada intersección es un agente que coordina con sus vecinas, reportó "
  "reducciones cercanas al 25 % en el tiempo de viaje (Smith et al., 2013).")
P("2.3 Comunicación entre agentes: FIPA-ACL", H2)
P("La Foundation for Intelligent Physical Agents definió el lenguaje de comunicación FIPA-ACL, basado en la teoría "
  "de actos de habla: cada mensaje lleva una <b>performativa</b> (inform, request, query-ref, etc.) que indica la "
  "intención del emisor, además del contenido, la ontología y el protocolo (FIPA, 2002). SPADE implementa FIPA-ACL "
  "sobre el protocolo XMPP (Palanca et al., 2020). En este proyecto el contenido es JSON y la ontología se llama "
  "<i>sma-ctg</i>. Para la comunicación con dispositivos se usa MQTT, un protocolo publicador/suscriptor ligero "
  "para Internet de las cosas (OASIS, 2019).")
P("2.4 Modelado de SMA: MAS-ML y GAIA", H2)
P("MAS-ML extiende UML con conceptos propios de los SMA, como agentes, roles, organizaciones y entornos, para "
  "modelar su estructura y su comportamiento (da Silva &amp; de Lucena, 2004); con esa notación se elaboró el "
  "diagrama de clases del sistema (anexo B). GAIA es una metodología orientada a roles para el análisis y el diseño "
  "de SMA (Wooldridge et al., 2000; Zambonelli et al., 2003). En el <b>análisis</b> produce el modelo de roles (con "
  "permisos y responsabilidades de vivacidad y seguridad) y el modelo de interacciones (protocolos). En el "
  "<b>diseño</b> produce el modelo de agentes, el de servicios y el de conocidos.")
P("2.5 Aprendizaje por refuerzo: Q-learning y DQN", H2)
P("En el aprendizaje por refuerzo un agente aprende una política que maximiza la recompensa acumulada interactuando "
  "con el entorno. Q-learning estima el valor Q(s, a) de cada acción en cada estado con la regla "
  "Q(s,a) ← Q(s,a) + α [r + γ max Q(s',a') − Q(s,a)] (Watkins &amp; Dayan, 1992). Cuando el estado es continuo se "
  "usa una red neuronal para aproximar Q: la Deep Q-Network (Mnih et al., 2015) y su variante <b>Double DQN</b>, "
  "que reduce la sobreestimación de los valores (van Hasselt et al., 2016). Como referencia matemática de control "
  "semafórico se usó max-pressure (Varaiya, 2013).")
P("2.6 Redes neuronales sobre grafos: T-GCN", H2)
P("La red vial es un grafo, por eso la predicción del tráfico se beneficia de redes que combinan convolución sobre "
  "grafos (Kipf &amp; Welling, 2017), que capta la dependencia espacial entre cruces, con redes recurrentes, que "
  "captan la dependencia temporal. La <b>T-GCN</b> une una GCN con una GRU para predecir el tráfico en varios pasos "
  "futuros (Zhao et al., 2019).")
P("2.7 Modelos de asignación de tráfico", H2)
P("Según el primer principio de Wardrop (1952), en equilibrio ningún conductor puede reducir su tiempo de viaje "
  "cambiando de ruta. El tiempo de cada tramo crece con su volumen según una función de demora, como la del Bureau "
  "of Public Roads (1964) o la de Akçelik (1991), que representa mejor las colas en intersecciones. El equilibrio "
  "se calcula con el algoritmo de Frank y Wolfe (1956), y las rutas alternativas con el algoritmo de las k rutas más "
  "cortas de Yen (1971). El reparto de conductores entre rutas se modela con un logit (Dial, 1971). Para la "
  "simulación microscópica se usó SUMO (Lopez et al., 2018).")
P("2.8 Reconocimiento facial", H2)
P("El reconocimiento facial tiene dos etapas. La <b>detección</b> localiza los rostros en la imagen; YuNet es un "
  "detector de milisegundos para CPU (Wu et al., 2023). El <b>reconocimiento</b> transforma el rostro alineado en "
  "un vector (embedding) tal que los rostros de la misma persona quedan cerca; SFace entrena ese espacio con una "
  "pérdida sobre la hiperesfera (Zhong et al., 2021). Dos rostros se comparan con la similitud coseno, y la "
  "decisión depende de un umbral que equilibra los aciertos y los falsos positivos.")
P("2.9 Modelos de lenguaje", H2)
P("Los grandes modelos de lenguaje (LLM) generan texto a partir de instrucciones. Existen modelos abiertos pequeños, "
  "como Llama 3.2 de 3.000 millones de parámetros (Meta AI, 2024), que se pueden ejecutar en un portátil con "
  "herramientas como Ollama, sin enviar datos a servicios externos. Como pueden inventar información, su salida "
  "debe verificarse cuando se usa en avisos con datos operativos.")
P("2.10 Marco legal: Ley 1581 de 2012", H2)
P("La Ley Estatutaria 1581 de 2012 regula el tratamiento de datos personales en Colombia (Congreso de Colombia, "
  "2012), reglamentada por el Decreto 1377 de 2013. Los datos biométricos son <b>datos sensibles</b>: su tratamiento "
  "requiere autorización previa, expresa e informada del titular, quien conserva los derechos de conocer, actualizar, "
  "rectificar y suprimir sus datos. Por eso el registro facial de este proyecto exige consentimiento, guarda solo un "
  "vector (nunca fotos), permite borrar a una persona y descarta los rostros que no coinciden.")
historia.append(PageBreak())

# ====================================================================== 3. METODOLOGÍA
P("3. Metodología", H1)
P("El trabajo siguió un proceso iterativo de cuatro fases semanales: (1) análisis y diseño con GAIA, a partir de los "
  "derechos, deberes y responsabilidades de cada agente definidos en el documento de la problemática; (2) "
  "construcción del entorno (red vial, gemelo 3D, maqueta física y contrato MQTT); (3) implementación de los agentes, "
  "primero con reglas y después con modelos aprendidos; y (4) mediciones. Cada cambio se probó con pruebas de punta "
  "a punta automáticas antes de integrarlo.")
P("3.1 Análisis con GAIA", H2)
P("3.1.1 Modelo de roles", H3)
P("Cada rol se describe con sus permisos (recursos que lee o modifica) y sus responsabilidades. Las de "
  "<b>vivacidad</b> indican lo que el rol hace continuamente (el exponente ω significa «se repite indefinidamente»). "
  "Las de <b>seguridad</b> son invariantes que nunca deben violarse.")
tabla([
    ["Rol", "Permisos", "Vivacidad", "Seguridad"],
    ["Vigilar", "lee sma/eventos y sma/camaras", "(PercibirEvento . EnviarNovedad)<super>ω</super>",
     "No guarda imágenes ni vectores de rostros"],
    ["ClasificarAlertas", "lee la tabla REGLAS", "(RecibirNovedad . Clasificar . EnviarAlerta)<super>ω</super>",
     "Toda novedad recibe tipo y prioridad; ninguna se pierde"],
    ["Coordinar", "lee estado vial, rutas y experiencia; modifica el plan activo",
     "(RecibirAlerta . ConsultarEstado . [PedirRuta] . GenerarPlanes . Elegir . Ordenar . Comunicar . "
     "Restaurar)<super>ω</super>", "Nunca explora en alertas de Seguridad; todo plan termina en SEGUNDOS_PLAN"],
    ["ConsultarTráfico", "lee sma/sensores y el registro de rostros", "(RecibirConsulta . Responder)<super>ω</super>",
     "No almacena los vectores consultados"],
    ["Predecir", "lee colas y grafo vial", "(PredecirColas)<super>ω</super> || (RecibirPedido . CalcularRuta . "
     "Responder)<super>ω</super>", "Sin sesgos: no marca zonas sin datos que lo respalden"],
    ["ControlarSemáforos", "modifica sma/semaforo y sma/plan", "(Percibir . Decidir . Publicar)<super>ω</super>",
     "Respeta los modos bloqueados (cerco, peatonal); todo cambio pasa por amarillo y todo rojo"],
    ["Comunicar", "envía a Telegram", "(RecibirOrden . Redactar . Verificar . Enviar)<super>ω</super>",
     "Una alerta de Seguridad va solo a la Policía; el texto no cambia los datos"],
], [2.6, 3.6, 5.6, 4.7], "Modelo de roles (GAIA)")
P("3.1.2 Modelo de interacciones", H3)
tabla([
    ["Protocolo", "Iniciador → Respondedor", "Performativa", "Entrada / salida"],
    ["AlertaNovedad", "Vigilar → ClasificarAlertas", "inform", "{tipo, ubicacion, ts}"],
    ["ConsultarPersona", "Vigilar → ConsultarTráfico", "query-ref / inform", "{embedding, camara} → {coincide, similitud, persona}"],
    ["Alerta", "ClasificarAlertas → Coordinar", "inform", "{id, tipo, prioridad, ...}"],
    ["EstadoVial", "Coordinar → ConsultarTráfico", "query-ref / inform", "{sector} → {nivel, colas}"],
    ["RutaHuida", "Coordinar → Predecir", "request / inform", "{ubicacion} → {rutas, probabilidad, intercepcion}"],
    ["PrediccionColas", "Predecir → ControlarSemáforos", "inform (cada 12 s)", "{cruces: colas a 36 s}"],
    ["ActivarPlan / Restaurar", "Coordinar → ControlarSemáforos", "request / inform", "{semaforos, persecucion} → {estado}"],
    ["Comunicar", "Coordinar → Comunicar", "request", "{receptor, aviso, plan, duracion}"],
], [3.2, 4.6, 3.0, 5.7], "Modelo de interacciones (protocolos)")
P("3.2 Diseño con GAIA", H2)
P("3.2.1 Modelo de agentes", H3)
P("Cada rol se asignó a un tipo de agente, con una instancia por tipo. El tipo de Russell y Norvig se justificó "
  "en el documento de la problemática y se conservó en la implementación, enriquecido con aprendizaje.")
tabla([
    ["Agente", "Roles", "Tipo (Russell y Norvig)", "Técnica principal"],
    ["AgenteVigilancia", "Vigilar", "Reactivo simple", "Reglas sobre eventos MQTT"],
    ["AgenteClasificadorAlertas", "ClasificarAlertas", "Basado en objetivos", "Tabla de reglas evento → (tipo, prioridad)"],
    ["AgenteCoordinador", "Coordinar", "Basado en utilidad", "Modelo de equilibrio + utilidad + experiencia (ε-greedy)"],
    ["AgenteConsultor", "ConsultarTráfico", "Reactivo basado en modelos", "Modelo del tráfico con caché; similitud coseno"],
    ["AgentePredictor", "Predecir", "Híbrido: modelos + utilidad", "T-GCN; Yen + logit para la huida"],
    ["AgenteSemaforico", "ControlarSemáforos", "Objetivos + aprendizaje", "Double DQN con capa de seguridad; tabla Q de respaldo"],
    ["AgenteComunicador", "Comunicar", "Basado en objetivos", "LLM local con verificación + plantilla"],
], [3.8, 3.0, 3.8, 5.9], "Modelo de agentes")
P("3.2.2 Modelo de servicios y de conocidos", H3)
tabla([
    ["Servicio", "Agente", "Precondición", "Postcondición"],
    ["Clasificar una novedad", "Clasificador", "Llega una AlertaNovedad", "Alerta con tipo y prioridad enviada al Coordinador"],
    ["Elegir un plan de respuesta", "Coordinador", "Alerta recibida y estado vial conocido", "Plan de mayor utilidad activo durante 5 min"],
    ["Comparar un rostro", "Consultor", "Vector de 128 números", "Coincidencia (alias, similitud) o descarte sin guardar"],
    ["Predecir la ruta de huida", "Predictor", "Cruce de la detección", "k rutas con probabilidad, accesos y tiempos"],
    ["Aplicar modos semafóricos", "Semafórico", "Plan o decisión de la DQN", "Modo publicado en cada cruce, fase conservada"],
    ["Avisar", "Comunicador", "Orden del Coordinador", "Mensaje verificado en el grupo correcto de Telegram"],
], [3.8, 2.6, 4.2, 5.9], "Modelo de servicios (extracto)")
figura(diagrama_conocidos(), "Modelo de conocidos: las flechas indican qué agente inicia la comunicación con cuál.")
P("3.3 Fuentes de datos y depuración", H2)
tabla([
    ["Fuente", "Contenido", "Depuración", "Uso"],
    ["Accidentes de tránsito de Cartagena (datos.gov.co, 5z62-bn99)", "8.066 siniestros 2020–2025 con fecha, hora, "
     "gravedad y coordenadas", "573 duplicados por IPAT y 69 sin coordenadas válidas eliminados; 204 ubicados sobre "
     "las vías de Bocagrande (< 60 m)", "Eventos reales (botón «Accidente real») y escenarios de SUMO"],
    ["Registro de semaforización (mybb-py6y)", "90 semáforos oficiales", "Cruce con los semáforos de OSM",
     "Validó los 5 cruces del sector"],
    ["SIMM de Medellín (MEData)", "431.490 registros de flujo por carril y hora", "Valores imposibles, sensores "
     "inconsistentes y atípicos (3×IQR); 156 estaciones con ≥ 80 % de cobertura", "Entrenar y validar la T-GCN; "
     "perfil horario de demanda (pico 17:00)"],
    ["OpenStreetMap (Overpass)", "Red vial, sentidos, carriles, edificios y semáforos",
     "Sentidos de la Av. San Martín corregidos con verificación en campo", "Grafo vial, gemelo 3D y red de SUMO"],
], [3.8, 3.8, 4.8, 4.1], "Fuentes de datos reales")
P("3.4 Modelos usados", H2)
vinetas([
    "<b>Modelo de equilibrio de tráfico</b> (Coordinador): Frank-Wolfe con demora de Akçelik sobre el grafo de "
    "Bocagrande; calcula el tiempo total del sistema (TSTT) de cada plan y propone 3 rutas con Yen.",
    "<b>T-GCN</b> (Predictor): predice la cola de cada acceso a 12, 24 y 36 s con una ventana de 15 pasos de 12 s.",
    "<b>Double DQN</b> (Semafórico): una red compartida por los 5 cruces, entrenada 60 episodios en SUMO, que elige "
    "entre normal, prioridad A y prioridad B con las colas, la predicción y los vecinos.",
    "<b>YuNet + SFace</b> (Vigilancia y Consultor): detección y vector del rostro; umbral de similitud 0,363.",
    "<b>Llama 3.2 3B en Ollama</b> (Comunicador): redacción de avisos con verificación automática de los datos.",
])
historia.append(PageBreak())

# ====================================================================== 4. DESARROLLO
P("4. Desarrollo e implementación", H1)
P("4.1 Arquitectura", H2)
P("Los dispositivos y simuladores publican eventos y mediciones en un broker MQTT. Los siete agentes corren en un "
  "solo proceso de Python con SPADE y conversan entre sí con FIPA-ACL; hacia afuera solo usan MQTT, de modo que no "
  "distinguen un cruce físico de uno simulado. El panel web reenvía todo al navegador por WebSocket.")
figura(diagrama_arquitectura(), "Arquitectura del sistema.")
P("4.2 Herramientas", H2)
tabla([
    ["Categoría", "Herramientas"],
    ["Lenguajes", "Python 3.13, JavaScript, HTML/CSS, C++ (Arduino)"],
    ["Agentes y mensajería", "SPADE 4.1 (FIPA-ACL sobre XMPP), Mosquitto (MQTT), paho-mqtt"],
    ["Web y 3D", "FastAPI, uvicorn, WebSocket, Three.js, Leaflet"],
    ["IA y datos", "PyTorch (T-GCN, DQN), NumPy, pandas, SciPy, NetworkX, OpenCV (YuNet, SFace), Ollama (Llama 3.2)"],
    ["Simulación", "Eclipse SUMO con libsumo/TraCI"],
    ["Hardware", "ESP32 DevKit V1, Arduino IDE, PubSubClient"],
    ["Colaboración", "Git y GitHub, draw.io, Claude Code como asistente de programación"],
], [4.0, 12.5], "Herramientas (todas gratuitas y locales)")
P("4.3 Caso 1: accidente y plan de desvío", H2)
P("Cuando ocurre un accidente (botón del ESP32, panel o accidente real del DATT), la Vigilancia lo reporta, el "
  "Clasificador lo marca como Movilidad de prioridad Alta y el Coordinador consulta el estado vial. Luego genera los "
  "planes del catálogo, los evalúa con el modelo de equilibrio y elige el de mayor utilidad: semáforos optimizados, "
  "desvío local o, si el accidente bloquea la salida del cruce5, el contraflujo de la Carrera 1. El Semafórico aplica "
  "los modos, el gemelo dibuja las rutas sugeridas y el Comunicador avisa a la ciudadanía. A los 5 minutos todo "
  "vuelve a la normalidad y el Coordinador aprende el efecto medido del plan.")
figura(diagrama_secuencia(
    ["~Gemelo / ESP32", "Vigilancia", "Clasificador", "Coordinador", "Consultor", "Semafórico", "Comunicador",
     "~Ciudadanía"],
    [(0, 1, "evento accidente (MQTT)", "mqtt"), (1, 2, "inform AlertaNovedad", "msg"),
     (2, 2, "Movilidad, Alta", "propio"), (2, 3, "inform Alerta", "msg"), (3, 4, "query-ref EstadoVial", "msg"),
     (4, 3, "inform EstadoTrafico", "resp"), (3, 3, "planes + Frank-Wolfe + utilidad", "propio"),
     (3, 5, "request activarPlan", "msg"), (5, 0, "sma/semaforo + sma/plan", "mqtt"),
     (5, 3, "inform ResultadoAccion", "resp"), (3, 6, "request comunicar", "msg"),
     (6, 6, "redactar con LLM y verificar", "propio"), (6, 7, "Telegram", "ext"),
     (3, 5, "request restaurarPlanNormal (5 min)", "msg"), (5, 0, "modo normal", "mqtt")]),
    "Diagrama de secuencia actualizado del Caso 1 (negro: FIPA-ACL; morado: MQTT; verde: salida externa).")
imagen(IMG / "caso1_plan_rutas.jpg", 16.5, "Gemelo digital durante el Caso 1: plan activo «Semáforos optimizados» "
       "por un accidente real del DATT, con las tres rutas sugeridas por el Coordinador.")
P("4.4 Caso 2: persona solicitada y retención semafórica", H2)
P("Una cámara web hace de cámara de un cruce. YuNet detecta el rostro y SFace calcula su vector, que se publica en "
  "MQTT; nunca se envía ni se guarda la imagen. El Consultor compara el vector con los registrados y, si la similitud "
  "supera 0,363, responde «posible coincidencia»; si no, lo descarta. Con la alerta Crítica, el Predictor calcula "
  "las tres rutas de huida más probables hacia la salida, el acceso por el que llegaría a cada cruce y en cuántos "
  "segundos. El Coordinador pone todo rojo (cerco) en los cruces de la ruta más probable y rojo largo para el acceso "
  "del solicitado en las rutas alternas; mientras dura, la DQN no cambia esos modos. El Comunicador avisa solo a la "
  "Policía, con el alias, la ruta probable y el punto de intercepción sugerido.")
figura(diagrama_secuencia(
    ["~Cámara", "Vigilancia", "Consultor", "Clasificador", "Coordinador", "Predictor", "Semafórico", "Comunicador",
     "~Policía"],
    [(0, 1, "vector del rostro (MQTT)", "mqtt"), (1, 2, "query-ref consultarPersona", "msg"),
     (2, 2, "similitud coseno ≥ 0,363", "propio"), (2, 1, "inform Coincidencia", "resp"),
     (1, 3, "inform AlertaNovedad", "msg"), (3, 4, "inform Alerta (Crítica)", "msg"),
     (4, 5, "request predecirRutaHuida", "msg"), (5, 4, "inform Ruta (k rutas)", "resp"),
     (4, 4, "retención en la ruta", "propio"), (4, 6, "request activarPlan (persecución)", "msg"),
     (6, 6, "cerco / rojo largo", "propio"), (4, 7, "request comunicar", "msg"), (7, 8, "Telegram", "ext")]),
    "Diagrama de secuencia actualizado del Caso 2.")
imagen(IMG / "caso2_cerco_huida.jpg", 16.5, "Gemelo digital durante el Caso 2: cerco en el cruce3 (todo rojo), rojo "
       "largo en el cruce4 y la ruta probable de huida dibujada en rojo.")
P("4.5 Gemelo digital y panel", H2)
P("El gemelo 3D reconstruye Bocagrande con los datos de OpenStreetMap: 158 nodos, 218 tramos con sus carriles y "
  "sentidos (corregidos con verificación en campo en la Av. San Martín) y los edificios. Carros y peatones simulados "
  "obedecen los semáforos que publican los agentes, y el gemelo mide las colas de cada acceso cada 2 s, que son los "
  "sensores del SMA. Un accidente cierra el tramo y los conductores recalculan su ruta. La página «Mente de los "
  "agentes» muestra en vivo lo que cada agente percibe, sus reglas, sus decisiones y lo que aprende.")
imagen(IMG / "gemelo_general.jpg", 16.5, "Gemelo digital de Bocagrande: flujo del sector, estado de los cinco cruces "
       "y conversación entre agentes en vivo.")
imagen(IMG / "mente_agentes.jpg", 16.5, "Página «Mente de los agentes»: métricas de la T-GCN y comparación de "
       "controladores semafóricos.")
P("4.6 Maqueta física", H2)
P("El cruce1 (Av. San Martín × Calle 6, punto crítico citado por el DATT) se implementó en un ESP32 con dos semáforos "
  "(GPIO 25/26/27 y 32/33/13), un botón de accidente (GPIO 4) y un LED de conexión. El firmware ejecuta exactamente los "
  "mismos ciclos y modos que el simulador, publica su estado y escucha las órdenes del Semafórico por MQTT. El ESP32 "
  "ya se conecta al broker y el botón dispara el Caso 1 de punta a punta; a la fecha faltan los LEDs y componentes "
  "para montar los semáforos físicos.")
P("4.7 Comunicador", H2)
P("Un LLM local redacta cada aviso en español claro con temperatura 0. Antes de enviarlo, el código verifica que "
  "conserve los cruces, las calles, las cifras, el alias y la frase «posible coincidencia». Si no, o si tarda más de "
  "25 s, se envía la plantilla. Hay reglas que no dependen del LLM: una alerta de Seguridad va siempre solo al grupo "
  "de la Policía y a la ciudadanía no se le muestran códigos internos.")
P("4.8 Pruebas", H2)
P("Dos pruebas automáticas de punta a punta levantan los agentes y verifican la cadena completa de cada caso: el "
  "Caso 1 con un accidente y el Caso 2 con un rostro desconocido (que debe descartarse) y una coincidencia (que debe "
  "producir el cerco y un aviso solo a la Policía). Las pruebas no escriben a los grupos reales de Telegram.")
historia.append(PageBreak())

# ====================================================================== 5. RESULTADOS
P("5. Resultados y discusión", H1)
P("Todos los experimentos son reproducibles con <i>python mediciones/medir.py</i> y usan semillas fijas. Se reporta "
  "la media ± el intervalo de confianza del 95 % (t de Student).")
P("5.1 Predicción de colas (T-GCN)", H2)
if tg.get("resultados"):
    filas = [["Modelo", "MAE (veh)", "RMSE (veh)", "MAPE"]]
    for nombre, m in tg["resultados"].items():
        filas.append([nombre, coma(m["MAE"], 2), coma(m["RMSE"], 2), coma(m["MAPE_%"]) + " %"])
    tabla(filas, [7.5, 3, 3, 3], f"Predicción de colas en Bocagrande a 36 s (conjunto de prueba, "
                                 f"{tg['muestras']['prueba']} muestras)")
P("La T-GCN fue el mejor modelo y redujo el error un 14,4 % frente a la persistencia. Con los datos reales del SIMM "
  "(156 estaciones) también mejoró a la persistencia (46,7 %), aunque allí una GRU sin grafo la superó levemente: el "
  "grafo aporta más cuando los nodos están conectados por calles cercanas, como en Bocagrande.")
P("5.2 Control semafórico (DQN)", H2)
if comp.get("resumen"):
    filas = [["Controlador", "Demora (veh·h)", "Viaje medio (s)", "Cola media (veh)", "Mejora en demora"]]
    for r in comp["resumen"]:
        filas.append([r["controlador"], coma(r["demora_veh_h"]), coma(r["viaje_medio_s"]), coma(r["cola_media"]),
                      coma(r["mejora_demora_vs_fijo_%"]) + " %"])
    tabla(filas, [5.3, 2.8, 2.8, 2.8, 2.8], "Controladores en SUMO (12 escenarios: horas 8, 13, 17 y 19; sin "
                                            "accidente, accidente en el cruce5 y accidente real)")
P("La DQN fue el mejor controlador: redujo la cola media un 43 % frente al semáforo fijo y superó al método "
  "matemático max-pressure y a la tabla Q.")
P("5.3 Caso 1 con accidentes reales", H2)
tabla([
    ["Política", "Demora (veh·h)", "Viaje medio (s)", "Reducción de la demora"],
    ["Semáforos fijos", "86,7 ± 18,1", "350,9 ± 11,9", "-"],
    ["Solo el plan del Coordinador", "86,3 ± 18,5", "344,9 ± 12,0", "0,5 % ± 1,0 (6 de 10)"],
    ["Solo la DQN", "80,6 ± 19,3", "316,3 ± 9,4", "7,8 % ± 2,4 (10 de 10)"],
    ["SMA completo (DQN + plan)", "80,9 ± 19,6", "316,6 ± 9,4", "7,5 % ± 2,6 (10 de 10)"],
], [5.2, 3.3, 3.3, 4.7], "Caso 1 en SUMO: 10 accidentes reales del DATT en hora pico (mismo tráfico y accidente "
                          "para todas las políticas)")
imagen(RES / "caso1_demora.png", 15, "Demora total por accidente real y por política.")
P("<b>Discusión.</b> El SMA completo redujo la demora y el tiempo de viaje en todos los accidentes. La mejora "
  "proviene casi por completo del control adaptativo de la DQN: los modos fijos del plan del Coordinador aportan "
  "poco en accidentes de vías secundarias, que son la mayoría en los datos del DATT. El plan sí es decisivo cuando "
  "el accidente bloquea la única salida (cruce5): allí el modelo de equilibrio estima que el contraflujo de la "
  "Carrera 1 baja el tiempo total del sistema de 455 a 282 veh·h (−38 %), mientras que ningún controlador semafórico "
  "logra mejorar. Ese plan no se midió en SUMO porque el simulador no invierte el sentido de los carriles.")
P("5.4 Caso 2: tiempo para la intercepción", H2)
tabla([
    ["Cámara (cruce)", "Huida sin retención (s)", "Con retención (s)", "Tiempo ganado (s)", "Demora extra resto (veh·h)"],
    ["cam1 (cruce1)", "481 ± 206", "921 ± 220", "441 ± 354", "36,2 ± 17,9"],
    ["cam2 (cruce3)", "415 ± 51", "1003 ± 173", "588 ± 171", "39,2 ± 10,5"],
    ["cam3 (cruce5)", "21 ± 12", "298 ± 34", "277 ± 29", "16,4 ± 1,6"],
], [3.0, 3.5, 3.0, 3.0, 4.0], "Caso 2 en SUMO (hora pico, 5 semillas por cámara)")
imagen(RES / "caso2_huida.png", 12.5, "Tiempo que tarda el solicitado en salir de Bocagrande, con y sin retención.")
P("<b>Discusión.</b> La retención multiplica el tiempo que tiene la Policía para llegar al punto de intercepción: "
  "de unos 7 minutos a más de 15 cuando la detección ocurre en el interior del sector. El costo es demora para los "
  "demás conductores durante el cerco, por lo que se limita a 5 minutos y solo a las rutas probables. Es una decisión "
  "con implicaciones éticas: el sistema solo sugiere la medida a partir de una «posible coincidencia», y la "
  "intercepción la decide la autoridad.")
P("5.5 Reconocimiento facial", H2)
tabla([
    ["Métrica (umbral 0,363)", "Resultado"],
    ["Registrados reconocidos correctamente (97 muestras de 4 personas)", "93,8 %"],
    ["Registrados confundidos con otro registrado", "0 %"],
    ["No registrado tomado como solicitado (20 muestras)", "0 % (10 % con umbral 0,25)"],
    ["Similitud media con la persona correcta / con otra persona", "0,69 / 0,16"],
], [11, 5.5], "Reconocimiento facial con compañeros reales (solo se guardaron similitudes)")
imagen(RES / "rostros_similitudes.png", 12.5, "Distribución de similitudes: con la persona correcta y con las demás; "
       "la línea marca el umbral del Consultor.")
P("<b>Discusión.</b> Nunca hubo un falso positivo ni una confusión entre personas, que son los errores graves "
  "porque llevarían a la Policía hacia un inocente. El 6,2 % restante son rostros no reconocidos en cuadros con mala "
  "pose o poca luz; como la cámara envía un rostro por segundo, una persona frente a ella termina siendo reconocida. "
  "El umbral elegido está en el punto adecuado: bajarlo a 0,25 produce falsos positivos y subirlo a 0,45 reduce los "
  "aciertos sin ninguna ganancia. La muestra es pequeña y corresponde a un prototipo.")
P("5.6 Tiempo de respuesta y Comunicador", H2)
filas = [["Medición", "Resultado"]]
for caso, nombre in (("caso1", "Caso 1"), ("caso2", "Caso 2")):
    xs = [f for f in lat if f["caso"] == caso and f.get("evento_a_semaforos_s") is not None]
    if xs:
        a = sum(f["evento_a_semaforos_s"] for f in xs) / len(xs)
        b = sum(f["evento_a_aviso_s"] for f in xs) / len(xs)
        filas.append([f"{nombre}: evento → semáforos / → aviso redactado ({len(xs)} eventos)",
                      f"{coma(a, 2)} s / {coma(b)} s"])
for r, nombre in (("Ciudadania", "Ciudadanía"), ("Policia", "Policía")):
    xs = [f for f in com if f["receptor"] == r]
    if xs:
        n = sum(f["origen"] == "LLM" for f in xs)
        filas.append([f"Avisos a la {nombre} redactados por el LLM", f"{n} de {len(xs)}"])
tabla(filas, [11, 5.5], "Latencia de la cadena de agentes y redacción del LLM")
P("<b>Discusión.</b> Los semáforos reaccionan en menos de un segundo, porque los siete agentes corren en el mismo "
  "equipo. El aviso tarda unos 20 s, casi todo por la redacción del LLM en CPU. El LLM redactó todos los avisos a la "
  "ciudadanía, pero ninguno de los de la Policía: esos avisos tienen muchas cifras y el modelo de 3B tiende a "
  "resumirlas, así que la verificación los rechaza y se envía la plantilla exacta, que es lo deseable en un aviso "
  "operativo.")
historia.append(PageBreak())

# ====================================================================== 6. CONCLUSIONES
P("6. Conclusiones", H1)
vinetas([
    "Se diseñó con GAIA e implementó en SPADE un SMA de siete agentes que funciona de punta a punta en un gemelo "
    "digital de Bocagrande y en un cruce físico, con los dos casos de demostración operando.",
    "Combinar técnicas dentro de cada agente fue más efectivo que una sola técnica: la DQN aporta la adaptación "
    "continua de los semáforos (−7,5 % de demora con accidentes reales) y el modelo de equilibrio aporta el plan "
    "estructural cuando se bloquea la única salida (−38 % del tiempo total con el contraflujo).",
    "La coordinación entre agentes crea capacidades que ninguno tiene por separado: la cámara, el Predictor y el "
    "Semafórico juntos le dan a la Policía entre 4,6 y 9,8 minutos adicionales para interceptar a una persona solicitada.",
    "Los datos abiertos reales permitieron entrenar y validar los modelos, aunque la falta de aforos de Cartagena "
    "obliga a calibrar la demanda con datos de otra ciudad.",
    "La ética se incorporó en el diseño y no al final: consentimiento, ausencia de imágenes guardadas, descarte de "
    "rostros que no coinciden, avisos solo a la Policía y verificación de lo que redacta el LLM.",
])
P("6.1 Lecciones aprendidas", H2)
vinetas([
    "Los datos geográficos abiertos tienen errores: los sentidos de la Av. San Martín en OpenStreetMap no coincidían "
    "con la realidad y hubo que corregirlos con conocimiento del sector.",
    "Medir con honestidad cambia las conclusiones: el plan del Coordinador parecía la pieza clave y las mediciones "
    "mostraron que la mejora diaria viene de la DQN.",
    "Un mismo contrato de mensajes para dispositivos reales y simulados permitió desarrollar sin depender del hardware.",
    "Las pruebas automáticas de punta a punta evitaron regresiones en un sistema con muchas piezas concurrentes.",
])
P("6.2 Trabajo futuro", H2)
vinetas([
    "Montar los semáforos físicos de la maqueta y medir el Caso 1 con el ESP32.",
    "Evaluar el contraflujo en SUMO invirtiendo carriles y entrenar la DQN con más escenarios de cierre.",
    "Ampliar la muestra de rostros con tomas separadas por condición de luz, distancia y pose.",
    "Conseguir aforos reales de Cartagena (DATT) para calibrar la demanda local.",
    "Escalar a otros sectores y coordinar varios SMA de barrio.",
])
historia.append(PageBreak())

# ====================================================================== REFERENCIAS (APA 7)
P("Referencias", H1)
for r in [
    "Akçelik, R. (1991). Travel time functions for transport planning purposes: Davidson's function, its time-dependent "
    "form and an alternative travel time function. <i>Australian Road Research, 21</i>(3), 49–59.",
    "Bureau of Public Roads. (1964). <i>Traffic assignment manual</i>. U.S. Department of Commerce.",
    "Congreso de Colombia. (2012, 17 de octubre). <i>Ley Estatutaria 1581 de 2012, por la cual se dictan disposiciones "
    "generales para la protección de datos personales</i>. Diario Oficial 48.587.",
    "da Silva, V. T., &amp; de Lucena, C. J. P. (2004). From a conceptual framework for agents and objects to a "
    "multi-agent system modeling language. <i>Autonomous Agents and Multi-Agent Systems, 9</i>(1–2), 145–189.",
    "Dial, R. B. (1971). A probabilistic multipath traffic assignment model which obviates path enumeration. "
    "<i>Transportation Research, 5</i>(2), 83–111.",
    "Foundation for Intelligent Physical Agents. (2002). <i>FIPA ACL message structure specification</i> (SC00061G).",
    "Frank, M., &amp; Wolfe, P. (1956). An algorithm for quadratic programming. <i>Naval Research Logistics Quarterly, "
    "3</i>(1–2), 95–110.",
    "Kipf, T. N., &amp; Welling, M. (2017). Semi-supervised classification with graph convolutional networks. En "
    "<i>International Conference on Learning Representations</i>.",
    "Lopez, P. A., Behrisch, M., Bieker-Walz, L., Erdmann, J., Flötteröd, Y.-P., Hilbrich, R., Lücken, L., Rummel, J., "
    "Wagner, P., &amp; Wießner, E. (2018). Microscopic traffic simulation using SUMO. En <i>21st International "
    "Conference on Intelligent Transportation Systems</i> (pp. 2575–2582). IEEE.",
    "Meta AI. (2024). <i>Llama 3.2: Lightweight models for edge and mobile devices</i> [Modelo de lenguaje].",
    "Mnih, V., Kavukcuoglu, K., Silver, D., Rusu, A. A., Veness, J., Bellemare, M. G., et al. (2015). Human-level "
    "control through deep reinforcement learning. <i>Nature, 518</i>(7540), 529–533.",
    "OASIS. (2019). <i>MQTT version 5.0</i>. OASIS Standard.",
    "Palanca, J., Terrasa, A., Julian, V., &amp; Carrascosa, C. (2020). SPADE 3: Supporting the new generation of "
    "multi-agent systems. <i>IEEE Access, 8</i>, 182537–182549.",
    "Russell, S., &amp; Norvig, P. (2021). <i>Artificial intelligence: A modern approach</i> (4.ª ed.). Pearson.",
    "Smith, S. F., Barlow, G. J., Xie, X.-F., &amp; Rubinstein, Z. B. (2013). SURTRAC: Scalable urban traffic control. "
    "En <i>Transportation Research Board 92nd Annual Meeting</i>.",
    "van Hasselt, H., Guez, A., &amp; Silver, D. (2016). Deep reinforcement learning with double Q-learning. En "
    "<i>Proceedings of the AAAI Conference on Artificial Intelligence, 30</i>(1).",
    "Varaiya, P. (2013). Max pressure control of a network of signalized intersections. <i>Transportation Research "
    "Part C, 36</i>, 177–195.",
    "Wardrop, J. G. (1952). Some theoretical aspects of road traffic research. <i>Proceedings of the Institution of "
    "Civil Engineers, 1</i>(3), 325–362.",
    "Watkins, C. J. C. H., &amp; Dayan, P. (1992). Q-learning. <i>Machine Learning, 8</i>(3–4), 279–292.",
    "Wooldridge, M. (2009). <i>An introduction to multiagent systems</i> (2.ª ed.). Wiley.",
    "Wooldridge, M., Jennings, N. R., &amp; Kinny, D. (2000). The Gaia methodology for agent-oriented analysis and "
    "design. <i>Autonomous Agents and Multi-Agent Systems, 3</i>(3), 285–312.",
    "Wu, W., Peng, H., &amp; Yu, S. (2023). YuNet: A tiny millisecond-level face detector. <i>Machine Intelligence "
    "Research, 20</i>(5), 656–665.",
    "Yen, J. Y. (1971). Finding the k shortest loopless paths in a network. <i>Management Science, 17</i>(11), 712–716.",
    "Zambonelli, F., Jennings, N. R., &amp; Wooldridge, M. (2003). Developing multiagent systems: The Gaia methodology. "
    "<i>ACM Transactions on Software Engineering and Methodology, 12</i>(3), 317–370.",
    "Zhao, L., Song, Y., Zhang, C., Liu, Y., Wang, P., Lin, T., Deng, M., &amp; Li, H. (2020). T-GCN: A temporal graph "
    "convolutional network for traffic prediction. <i>IEEE Transactions on Intelligent Transportation Systems, "
    "21</i>(9), 3848–3858.",
    "Zhong, Y., Deng, W., Hu, J., Zhao, D., Li, X., &amp; Wen, D. (2021). SFace: Sigmoid-constrained hypersphere loss "
    "for robust face recognition. <i>IEEE Transactions on Image Processing, 30</i>, 2587–2598.",
    "Datos abiertos: Alcaldía de Cartagena. (2026). <i>Accidentes de tránsito</i> [Conjunto de datos 5z62-bn99] y "
    "<i>Registro de semaforización</i> [mybb-py6y]. datos.gov.co. Alcaldía de Medellín. (2020). <i>Velocidad e "
    "intensidad vehicular</i> [SIMM, MEData]. OpenStreetMap contributors. (2026). <i>OpenStreetMap</i> [ODbL].",
]:
    historia.append(Paragraph(r, REF))
historia.append(PageBreak())

# ====================================================================== ANEXOS
P("Anexos", H1)
P("Anexo A. Código fuente", H2)
P("El código completo está en el repositorio privado https://github.com/Enmanuel-caraballo/SMA_PROYECT_ENMANUEL "
  "(aprox. 8.000 líneas). Estructura principal:")
tabla([
    ["Carpeta / archivo", "Contenido"],
    ["agentes/", "sma_agentes.py (los 7 agentes), conocimiento.py (reglas y parámetros), aprendizaje.py, rutas.py "
                 "(modelo de equilibrio), comunicador.py (LLM + Telegram), memoria/ (lo aprendido)"],
    ["modelos/", "Datos reales y su limpieza, T-GCN, DQN, red y entorno de SUMO, modelos entrenados (.pt)"],
    ["vision/", "camara.py: cámara con YuNet + SFace, registro con consentimiento y medición"],
    ["maqueta_virtual/", "Red de Bocagrande desde OSM, simulador de cruces, panel FastAPI y gemelo 3D"],
    ["esp32/", "Firmware del cruce físico"],
    ["pruebas/, mediciones/", "Pruebas de punta a punta de los dos casos y experimentos del capítulo 5"],
    ["docs/", "Diagramas, documento de la problemática, especificaciones técnicas, resultados y este informe"],
], [4.0, 12.5])
P("Anexo B. Diagramas de diseño", H2)
P("El diagrama de clases (MAS-ML, versión 4) y los diagramas de secuencia originales corresponden al diseño inicial. "
  "En ese diseño el Caso 1 abría un «corredor de emergencia»; el 16 de noviembre se cambió por planes de desvío y "
  "contraflujo porque en Cartagena no existen corredores de emergencia. Los diagramas actualizados están en la "
  "sección 4.")
imagen(IMG / "diagrama_clases_sma_v4_1.png", 16.5, "Diagrama de clases del SMA (MAS-ML, v4).", alto_max_cm=10)
imagen(IMG / "diagramas_secuencia_sma_1.png", 15.5, "Diagrama de secuencia original del Caso 1 (diseño inicial).")
imagen(IMG / "diagramas_secuencia_sma_2.png", 15.5, "Diagrama de secuencia original del Caso 2 (diseño inicial).")
P("Anexo C. Manual para correr la demo", H2)
for linea in [
    "1. Instalar: pip install -r requirements.txt ; instalar Mosquitto y Ollama (ollama pull llama3.2:3b)",
    "2. Copiar .env.ejemplo como .env y poner el token del bot de Telegram",
    "3. Terminal 1: python maqueta_virtual/simulador_ciudad.py --incluir-cruce1",
    "4. Terminal 2: python agentes/sma_agentes.py",
    "5. Terminal 3: python maqueta_virtual/panel/servidor_panel.py  -> abrir http://localhost:8000",
    "6. Registrar a un compañero: python vision/camara.py --registrar \"Alias\" --consentimiento",
    "7. Cámara del cruce3: python vision/camara.py --camara cam2",
    "8. Pruebas: python pruebas/prueba_punta_a_punta.py ; python pruebas/prueba_caso2.py",
    "9. Mediciones: python mediciones/medir.py todo",
]:
    historia.append(Paragraph(linea, CODIGO))
P("Anexo D. Guion de la demostración", H2)
vinetas([
    "<b>Preparación (antes de empezar):</b> broker, simulador, agentes, panel y Ollama abiertos; pestaña del gemelo "
    "visible; grupos de Telegram abiertos en un celular; un compañero registrado con consentimiento.",
    "<b>1. Presentación (1 min):</b> gemelo de Bocagrande, cinco cruces y página «Mente de los agentes».",
    "<b>2. Caso 1 (3 min):</b> pulsar el botón del ESP32 o «Accidente real». Mostrar la conversación de los agentes, "
    "el plan elegido por utilidad, las rutas sugeridas en el 3D y el aviso en el grupo de la Ciudadanía. Terminar la "
    "contingencia.",
    "<b>3. Caso 2 (3 min):</b> el compañero registrado se para frente a la cámara del cruce3. Mostrar la «posible "
    "coincidencia», la ruta probable de huida, el cerco y el aviso con el alias en el grupo de la Policía. Mostrar "
    "también que un rostro no registrado se descarta.",
    "<b>4. Resultados (2 min):</b> tablas del capítulo 5.",
    "<b>Plan B:</b> si falla la red, usar el video grabado de la demo y los botones del panel, que funcionan sin "
    "cámara ni ESP32.",
])


# ====================================================================== construcción con tabla de contenido
class Documento(SimpleDocTemplate):
    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and f.style.name in ("h1", "h2"):
            texto = f.getPlainText()
            nivel = 0 if f.style.name == "h1" else 1
            clave = f"t{id(f)}"
            self.canv.bookmarkPage(clave)
            self.notify("TOCEntry", (nivel, texto, self.page, clave))


def pie_pagina(c, doc):
    if doc.page == 1:
        return
    c.saveState()
    c.setFont("Arial", 8)
    c.setFillColor(GRIS)
    c.drawString(2.5 * cm, 1.3 * cm, "SMA de Movilidad y Seguridad · Bocagrande · Informe final")
    c.drawRightString(letter[0] - 2.5 * cm, 1.3 * cm, f"{doc.page}")
    c.restoreState()


doc = Documento(str(SALIDA), pagesize=letter, leftMargin=2.5 * cm, rightMargin=2.5 * cm, topMargin=2.3 * cm,
                bottomMargin=2.2 * cm, title="Informe final: Sistema Multiagente de Movilidad y Seguridad para Bocagrande",
                author="Caraballo, Coronel, Vergara, Maldonado y Gómez", subject="Proyecto final de Inteligencia Artificial")
doc.multiBuild(historia, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
print("PDF generado:", SALIDA)
