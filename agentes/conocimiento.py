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
# Exploración de planes: a veces el Coordinador prueba el plan MENOS USADO en esa situación para medirlo con datos
# reales (si no, un plan nunca probado conserva para siempre su impacto inicial y la comparación es injusta).
# Lo aprendido se guarda por situación: evento @ cruce (/ sentido en el cruce5).
EPSILON_PLANES = 0.25                      # probabilidad de explorar en una alerta
EPSILON_PLANES_MINIMO = 0.05
EPSILON_PLANES_DECAIMIENTO = 0.9           # por cada alerta atendida
NO_EXPLORAR_SOLO_AVISAR_EN = ["Alta", "Critica"]   # con alertas graves no se "prueba" no hacer nada
# Las alertas de Seguridad nunca exploran: siempre el plan de mayor utilidad (cerco + aviso solo a la Policía).

SALIDA_BOCAGRANDE = "cruce5"               # única salida/entrada de Bocagrande hacia el Centro

# ---------------------------------------------------------------------
# Modelo matemático de rutas (agentes/rutas.py): BPR + equilibrio de usuario (Frank-Wolfe) + Yen k-rutas
#   costo de un tramo  t = t0 · (1 + BPR_ALFA · (flujo / capacidad)^BPR_BETA)
#   El Coordinador evalúa cada plan por el TSTT (tiempo total de viaje en la red) que deja el equilibrio.
# ---------------------------------------------------------------------
FUNCION_COSTO = "akcelik"                  # "akcelik" (urbana con semáforos, modela colas) o "bpr"
AKCELIK_T_H = 1.0                          # periodo de análisis (h)
AKCELIK_J = 0.4                            # parámetro de demora: 0.4 = vía urbana interrumpida (Akçelik 1991)
BPR_ALFA = 0.15                            # valores estándar del Bureau of Public Roads (1964)
BPR_BETA = 4.0
FLUJO_SATURACION = 1800                    # veh/h por carril con verde (HCM; Medellín SIMM: máx. limpio ~2200)
CAPACIDAD_RESIDUAL_ACCIDENTE = 0.25        # el carril del accidente deja pasar ~1/4 (se pasa despacio, a 3 m/s)
DEMANDA_PICO_VEH_H = 3000                  # viajes/h en la hora pico (se recalibra: ver VC_PICO_OBJETIVO)
VC_PICO_OBJETIVO = 0.90                    # en hora pico normal la vía más cargada queda al 90 % de su capacidad
REPARTO_DEMANDA = {"salir": 0.30, "entrar_centro": 0.22, "entrar_norte": 0.08, "interna": 0.40}
PENALIZACION_SIN_RUTA_S = 600              # viaje que el plan deja sin ruta interna: rodeo por fuera de Bocagrande (10 min)
FW_TOLERANCIA = 1e-3                       # brecha relativa de equilibrio
FW_MAX_ITER = 120
K_RUTAS = 3                                # rutas alternativas que propone el Coordinador (Yen)
PESO_MODELO = 0.6                          # peso del modelo matemático vs. lo aprendido midiendo en la utilidad
MEJORA_MINIMA = 0.005                      # si ningún plan baja el TSTT más de 0.5 %, el modelo no distingue (beneficio 0)
# demanda que se le pone al modelo según lo que MIDEN los sensores (nivel del Consultor) y el tipo de evento
ESCALA_POR_NIVEL = {"alto": 1.0, "medio": 0.85, "bajo": 0.7, "desconocido": 0.85}
ESCALA_POR_EVENTO = {"congestion": 1.25}   # una congestión es demanda por encima de lo normal

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
    # plan que PROPONE el modelo matemático: modo de cada cruce elegido minimizando el TSTT (rutas.optimizar_semaforos)
    "semaforos_optimos":   {"nombre": "Semáforos optimizados", "impacto": 0.65, "costo": 0.10},
}

# Qué planes se consideran para cada evento (reglas de generación de planes)
PLANES_POR_EVENTO = {
    "accidente":    ["solo_avisar", "desvio_local", "semaforos_optimos"],   # + contraflujo si es en SALIDA_BOCAGRANDE
    "congestion":   ["solo_avisar", "priorizar_principal", "semaforos_optimos"],
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
MARGEN_CAMBIO = 0.3                        # solo cambia de modo si la mejor opción supera a la actual por este margen
                                           # (evita que el semáforo "parpadee" por diferencias que son ruido)
# Con el cruce vacío (estado a0-b0) no hay a quién dar prioridad: queda en "normal" (o en el modo del plan) y no explora
# Regla experta (conocimiento inicial de la tabla Q): por cada cubeta de diferencia entre accesos,
# la prioridad hacia el acceso más cargado vale PESO_DESBALANCE más que el ciclo normal.
PESO_DESBALANCE = 1.0
COSTO_PRIORIDAD_SIN_COLA = 1.2             # con 1 cubeta de diferencia sigue en normal; con 2 o más da prioridad

# =====================================================================
# Semafórico neuronal (DQN entrenada en SUMO: modelos/entrenar_dqn.py) - la tabla Q queda de respaldo
# =====================================================================
USAR_DQN = True                            # si existe modelos/entrenados/dqn_semaforico.pt
DQN_PESO_VECINOS = 0.3                     # la recompensa también cuenta la cola de los 2 cruces vecinos
DQN_BONO_PLAN = 0.3                        # preferencia por el modo que sugiere el plan (en unidades de Q de la red)
DQN_MARGEN = 0.05                          # histéresis: cambiar de modo solo si el Q mejora más que esto
AVISO_CRECIMIENTO_COLA = 4                 # el Predictor anuncia cuando anticipa que una cola sube 4 veh o más
SUMO_FRACCION_DEMANDA = 1.0                # SUMO usa la misma demanda calibrada del modelo de rutas (con el perfil horario real)

# =====================================================================
# Agente Predictor (Caso 2): por ahora regla simple
# =====================================================================
RUTA_HUIDA_POR_DEFECTO = {"cruces": ["cruce2", "cruce1"], "confianza": 0.7}
