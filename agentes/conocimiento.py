"""
SMA Movilidad y Seguridad CTG - BASE DE CONOCIMIENTO
====================================================
Aquí están TODAS las reglas y parámetros que siguen los 7 agentes. No hay reglas escondidas en otro
archivo: si se cambia un número aquí, cambia el comportamiento del SMA.

Lo que los agentes APRENDEN con la experiencia no está aquí sino en agentes/memoria/ (ver aprendizaje.py):
  - q_semaforico.json        tabla Q del Agente Semafórico (qué prioridad conviene según las colas)
  - experiencia_planes.json  impacto real medido de cada plan del Agente Coordinador
Los valores de este archivo son el CONOCIMIENTO INICIAL (lo que sabe un experto en tránsito antes de
que el agente acumule experiencia).
"""

# =====================================================================
# Agente Clasificador (basado en objetivos): tipo de evento -> (TipoAlerta, NivelPrioridad)
# =====================================================================
REGLAS_CLASIFICACION = {
    "accidente":          ("Movilidad", "Alta"),
    "congestion":         ("Movilidad", "Media"),
    "aglomeracion":       ("Mixta", "Media"),
    "placa_reportada":    ("Seguridad", "Alta"),
    "persona_solicitada": ("Seguridad", "Critica"),
}
CLASIFICACION_POR_DEFECTO = ("Mixta", "Baja")

# =====================================================================
# Agente Consultor (reactivo basado en modelos): nivel de tráfico a partir de los sensores de cola
# =====================================================================
UMBRAL_NIVEL = {"medio": 4, "alto": 10}   # vehículos en cola (A + B) en el cruce
CACHE_SEGUNDOS = 5                         # cuánto tiempo reutiliza una respuesta antes de volver a medir
HISTORIAL_SEGUNDOS = 900                   # cuánta historia de colas guarda (para que el Coordinador aprenda)

# =====================================================================
# Agente Coordinador (basado en utilidad)
#   utilidad = impacto x FACTOR_TRAFICO[nivel] - PESO_COSTO x costo
#   impacto: empieza con IMPACTO_INICIAL y se corrige con lo que se mide en cada plan (aprendizaje)
# =====================================================================
SEGUNDOS_PLAN = 300                        # duración de cada plan (5 min); --duracion N lo cambia
FACTOR_TRAFICO = {"alto": 1.0, "medio": 0.85, "bajo": 0.7}
FACTOR_TRAFICO_DESCONOCIDO = 0.85
PESO_COSTO = 0.5
ALFA_PLANES = 0.3                          # cuánto pesa cada nueva experiencia al corregir el impacto
SEGUNDOS_LINEA_BASE = 60                   # colas medidas antes del evento para comparar

SALIDA_BOCAGRANDE = "cruce5"               # única salida/entrada de Bocagrande hacia el Centro

# Catálogo de planes: costo para la movilidad (0..1) e impacto esperado ANTES de tener experiencia
CATALOGO_PLANES = {
    "solo_avisar":         {"nombre": "Solo avisar", "impacto": 0.30, "costo": 0.00},
    "desvio_local":        {"nombre": "Desvío local", "impacto": 0.55, "costo": 0.15},
    "contraflujo_salida":  {"nombre": "Contraflujo Carrera 1 (salida)", "impacto": 0.95, "costo": 0.35,
                            "semaforos": {"cruce4": "prioridad_a", "cruce3": "prioridad_b", "cruce5": "prioridad_b"}},
    "contraflujo_entrada": {"nombre": "Contraflujo Carrera 1 (entrada)", "impacto": 0.90, "costo": 0.35,
                            "semaforos": {"cruce5": "prioridad_a", "cruce3": "prioridad_b"}},
    "priorizar_principal": {"nombre": "Verde largo para la vía principal", "impacto": 0.70, "costo": 0.20},
    "fase_peatonal":       {"nombre": "Fase peatonal extendida", "impacto": 0.70, "costo": 0.25},
    "cerco":               {"nombre": "Cerco semafórico", "impacto": 0.90, "costo": 0.50},
}

# Qué planes se consideran para cada evento (reglas de generación de planes)
PLANES_POR_EVENTO = {
    "accidente":    ["solo_avisar", "desvio_local"],      # + contraflujo si es en SALIDA_BOCAGRANDE
    "congestion":   ["solo_avisar", "priorizar_principal"],
    "aglomeracion": ["solo_avisar", "fase_peatonal"],
    "Seguridad":    ["cerco", "solo_avisar"],              # por TipoAlerta: aviso SOLO a la Policía
}

AVISOS_CONTRAFLUJO = {
    "contraflujo_salida": ("accidente en la Av. San Martín antes de la Calle 15, sentido salida. Para salir de "
                           "Bocagrande tome la Calle 10 desde la Carrera 3 hacia la Carrera 1: la Carrera 1 queda "
                           "SOLO de salida. El ingreso a Bocagrande es únicamente por la Av. San Martín."),
    "contraflujo_entrada": ("accidente en la Av. San Martín después de la Calle 15, sentido entrada. El ingreso a "
                            "Bocagrande es SOLO por la Carrera 1; la Av. San Martín queda para salir."),
}

# =====================================================================
# Agente Semafórico (basado en objetivos + aprendizaje por refuerzo, Q-learning)
#   Percibe: colas por acceso (A = vía principal, B = secundaria) de sma/sensores/<cruce>
#   Estado:  (cubeta de cola A, cubeta de cola B)       Acciones: MODOS_ADAPTATIVOS
#   Recompensa: cuánto bajó la cola total en el intervalo - PENALIZACION_CAMBIO si cambió de modo
# =====================================================================
MODOS_ADAPTATIVOS = ["normal", "prioridad_a", "prioridad_b"]
MODOS_BLOQUEADOS = ["cerco", "peatonal", "corredor"]   # los ordena el Coordinador; el Semafórico no los cambia
INTERVALO_CONTROL = 12                     # cada cuántos segundos decide (≈ medio ciclo semafórico)
CUBETAS_COLA = [0, 3, 7, 12]               # 0 | 1-3 | 4-7 | 8-12 | 13+  vehículos  -> cubetas 0..4
Q_ALFA = 0.2                               # tasa de aprendizaje
Q_GAMMA = 0.6                              # cuánto importa el futuro
EPSILON_INICIAL = 0.15                     # probabilidad de explorar una acción distinta
EPSILON_MINIMO = 0.02
EPSILON_DECAIMIENTO = 0.995                # por cada decisión
PENALIZACION_CAMBIO = 0.5                  # cambiar de modo a cada rato también cuesta
BONO_PLAN = 1.5                            # preferencia por el modo que sugiere el plan del Coordinador
# Regla experta (conocimiento inicial de la tabla Q): por cada cubeta de diferencia entre accesos,
# la prioridad hacia el acceso más cargado vale PESO_DESBALANCE más que el ciclo normal.
PESO_DESBALANCE = 1.0
COSTO_PRIORIDAD_SIN_COLA = 1.2             # con 1 cubeta de diferencia sigue en normal; con 2 o más da prioridad

# =====================================================================
# Agente Predictor (Caso 2): por ahora regla simple
# =====================================================================
RUTA_HUIDA_POR_DEFECTO = {"cruces": ["cruce2", "cruce1"], "confianza": 0.7}
