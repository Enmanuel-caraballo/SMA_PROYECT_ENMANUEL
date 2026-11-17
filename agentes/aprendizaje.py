"""
SMA Movilidad y Seguridad CTG - APRENDIZAJE DE LOS AGENTES
==========================================================
1) QSemaforo: aprendizaje por refuerzo (Q-learning tabular) del Agente Semafórico.
   Para cada cruce aprende qué modo de semáforo conviene según las colas que ve en cada acceso.
     estado  s = (cubeta cola A, cubeta cola B)          p. ej. "a1-b4" = poca cola en A, mucha en B
     acción  a = normal | prioridad_a | prioridad_b
     recompensa r = (cola total antes - cola total después) - penalización si cambió de modo
     Q(s,a) <- Q(s,a) + alfa * (r + gamma * max_a' Q(s',a') - Q(s,a))
   La tabla empieza con la regla experta (conocimiento.PESO_DESBALANCE) y se corrige con la experiencia.

2) ExperienciaPlanes: el Agente Coordinador corrige el "impacto" de cada plan con lo que mide
   en TODO el sector (suma de colas de los 5 cruces): ¿Bocagrande fluyó mejor o peor con el plan?
     efecto  = (cola antes - cola durante) / max(cola antes, 3)          (-1..1)
     si efecto > 0:  impacto <- impacto + ALFA_PLANES * efecto * (1 - impacto)     (sube hacia 1)
     si efecto < 0:  impacto <- impacto + ALFA_PLANES * efecto * impacto           (baja hacia 0)

Las dos memorias se guardan en agentes/memoria/*.json: el agente recuerda lo aprendido entre ejecuciones.
"""

import json
import random
from pathlib import Path

import conocimiento as K

CARPETA_MEMORIA = Path(__file__).resolve().parent / "memoria"


def _leer(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _escribir(ruta, datos):
    CARPETA_MEMORIA.mkdir(exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def cubeta(cola):
    """0 | 1-3 | 4-7 | 8-12 | 13+  ->  0..4 (con CUBETAS_COLA = [0, 3, 7, 12])."""
    return sum(1 for limite in K.CUBETAS_COLA if cola > limite)


class QSemaforo:
    def __init__(self, olvidar=False):
        self.ruta = CARPETA_MEMORIA / "q_semaforico.json"
        datos = {} if olvidar else _leer(self.ruta)
        self.q = datos.get("q", {})                  # cruce -> estado -> acción -> valor
        self.visitas = datos.get("visitas", {})      # cruce -> estado -> acción -> veces
        self.epsilon = datos.get("epsilon", K.EPSILON_INICIAL)
        self.pasos = datos.get("pasos", 0)
        self.recompensas = datos.get("recompensas", [])[-200:]

    @staticmethod
    def estado(cola_a, cola_b):
        return f"a{cubeta(cola_a)}-b{cubeta(cola_b)}"

    @staticmethod
    def valor_experto(estado, accion):
        """Conocimiento inicial: dar prioridad al acceso con más cola."""
        ca, cb = int(estado[1]), int(estado[4])
        if accion == "normal":
            return 0.0
        dif = (ca - cb) if accion == "prioridad_a" else (cb - ca)
        return K.PESO_DESBALANCE * dif - K.COSTO_PRIORIDAD_SIN_COLA

    def valores(self, cruce, estado):
        fila = self.q.setdefault(cruce, {}).setdefault(estado, {})
        for a in K.MODOS_ADAPTATIVOS:
            fila.setdefault(a, round(self.valor_experto(estado, a), 3))
        return fila

    def elegir(self, cruce, estado, sugerido=None):
        """Devuelve (acción, motivo, puntajes). sugerido = modo que pide el plan del Coordinador."""
        fila = self.valores(cruce, estado)
        puntajes = {a: v + (K.BONO_PLAN if a == sugerido else 0.0) for a, v in fila.items()}
        if random.random() < self.epsilon:
            accion = random.choice(K.MODOS_ADAPTATIVOS)
            return accion, f"explora (epsilon={self.epsilon:.3f})", puntajes
        accion = max(puntajes, key=puntajes.get)
        motivo = "mejor valor Q" + (" + bono del plan" if sugerido and accion == sugerido else "")
        if sugerido and accion != sugerido:
            motivo = f"las colas pesan más que el plan (sugería {sugerido})"
        return accion, motivo, puntajes

    def aprender(self, cruce, estado, accion, recompensa, estado_sig):
        fila = self.valores(cruce, estado)
        futuro = max(self.valores(cruce, estado_sig).values())
        antes = fila[accion]
        fila[accion] = round(antes + K.Q_ALFA * (recompensa + K.Q_GAMMA * futuro - antes), 4)
        v = self.visitas.setdefault(cruce, {}).setdefault(estado, {})
        v[accion] = v.get(accion, 0) + 1
        self.pasos += 1
        self.epsilon = max(K.EPSILON_MINIMO, self.epsilon * K.EPSILON_DECAIMIENTO)
        self.recompensas.append(round(recompensa, 2))
        self.recompensas = self.recompensas[-200:]
        return antes, fila[accion]

    def guardar(self):
        _escribir(self.ruta, {"q": self.q, "visitas": self.visitas, "epsilon": self.epsilon,
                              "pasos": self.pasos, "recompensas": self.recompensas})


class ExperienciaPlanes:
    def __init__(self, olvidar=False):
        self.ruta = CARPETA_MEMORIA / "experiencia_planes.json"
        self.datos = {} if olvidar else _leer(self.ruta)    # clave -> {"impacto", "n", "historial": [...]}

    def impacto(self, clave):
        e = self.datos.get(clave)
        return e["impacto"] if e else K.CATALOGO_PLANES[clave]["impacto"]

    def veces(self, clave):
        return self.datos.get(clave, {}).get("n", 0)

    def actualizar(self, clave, cola_antes, cola_durante):
        efecto = max(-1.0, min(1.0, (cola_antes - cola_durante) / max(cola_antes, 3.0)))
        antes = self.impacto(clave)
        margen = (1 - antes) if efecto > 0 else antes
        nuevo = round(min(0.99, max(0.05, antes + K.ALFA_PLANES * efecto * margen)), 3)
        e = self.datos.setdefault(clave, {"impacto": antes, "n": 0, "historial": []})
        e["impacto"], e["n"] = nuevo, e["n"] + 1
        e["historial"] = (e["historial"] + [{"antes": round(cola_antes, 1), "durante": round(cola_durante, 1),
                                              "efecto": round(efecto, 2), "impacto": nuevo}])[-20:]
        _escribir(self.ruta, self.datos)
        return antes, nuevo, efecto
