"""
SMA Movilidad y Seguridad CTG - DQN DEL AGENTE SEMAFÓRICO
=========================================================
Deep Q-Network (Mnih et al. 2015, Nature 518) con Double DQN (van Hasselt et al. 2016):
la tabla Q del Semafórico (25 estados) se reemplaza por una red neuronal que aproxima Q(estado, modo) con un
estado CONTINUO y más rico: colas propias, su tendencia, la PREDICCIÓN de la T-GCN, la cola de los cruces vecinos,
el modo actual y la hora.

    Q(s, a; θ) ≈ recompensa + γ · Q(s', argmax_a' Q(s', a'; θ); θ⁻)      (θ⁻ = red objetivo, copia lenta de θ)

Una sola red compartida por los 5 cruces (parameter sharing, como en MPLight/sumo-rl): cada cruce aporta su
experiencia y la red generaliza. Se entrena SOLO en simulación (SUMO); en los agentes solo hace inferencia.
"""

import math
import random
from collections import deque

import numpy as np
import torch
from torch import nn

MODOS = ["normal", "prioridad_a", "prioridad_b"]
RASGOS = ["cola A", "cola B", "A − B", "Δ cola A", "Δ cola B", "predicción A", "predicción B",
          "cola vecinos", "modo normal", "modo prioridad_a", "modo prioridad_b", "hora (sen)", "hora (cos)"]


def vector_estado(cruce, colas, previas, modo, prediccion, hora, vecinos):
    """Mismo vector en SUMO (entrenamiento) y en el Agente Semafórico (operación).
    colas/previas/prediccion: {cruce: (A, B)}; prediccion puede faltar (se usa la cola actual)."""
    a, b = colas[cruce]
    pa, pb = previas.get(cruce, (a, b)) if previas else (a, b)
    fa, fb = prediccion.get(cruce, (a, b)) if prediccion else (a, b)
    vec = np.mean([sum(colas[o]) for o in vecinos if o in colas]) if vecinos else 0.0
    m = [1.0 if modo == x else 0.0 for x in MODOS]
    return np.array([a / 10, b / 10, (a - b) / 10, (a - pa) / 5, (b - pb) / 5, fa / 10, fb / 10, vec / 10, *m,
                     math.sin(2 * math.pi * hora / 24), math.cos(2 * math.pi * hora / 24)], dtype=np.float32)


class RedQ(nn.Module):
    def __init__(self, entrada=len(RASGOS), oculto=64, salida=len(MODOS)):
        super().__init__()
        self.red = nn.Sequential(nn.Linear(entrada, oculto), nn.ReLU(), nn.Linear(oculto, oculto), nn.ReLU(),
                                 nn.Linear(oculto, salida))

    def forward(self, x):
        return self.red(x)


class AgenteDQN:
    def __init__(self, gamma=0.9, lr=5e-4, memoria=60000, lote=64, sincronizar=500):
        self.q = RedQ()
        self.objetivo = RedQ()
        self.objetivo.load_state_dict(self.q.state_dict())
        self.opt = torch.optim.Adam(self.q.parameters(), lr=lr)
        self.memoria = deque(maxlen=memoria)
        self.gamma, self.lote, self.sincronizar = gamma, lote, sincronizar
        self.pasos = 0

    def valores(self, s):
        with torch.no_grad():
            return self.q(torch.as_tensor(s).unsqueeze(0))[0].numpy()

    def elegir(self, s, epsilon=0.0):
        if random.random() < epsilon:
            return random.randrange(len(MODOS))
        return int(np.argmax(self.valores(s)))

    def recordar(self, s, a, r, s2, fin):
        self.memoria.append((s, a, r, s2, fin))

    def aprender(self):
        if len(self.memoria) < max(self.lote * 10, 1000):
            return None
        lote = random.sample(self.memoria, self.lote)
        s, a, r, s2, fin = map(np.array, zip(*lote))
        s, s2 = torch.as_tensor(s), torch.as_tensor(s2)
        a, r, fin = torch.as_tensor(a), torch.as_tensor(r, dtype=torch.float32), torch.as_tensor(fin, dtype=torch.float32)
        q = self.q(s).gather(1, a.unsqueeze(1)).squeeze(1)
        with torch.no_grad():   # Double DQN: la red en línea elige, la red objetivo evalúa
            mejor = self.q(s2).argmax(1, keepdim=True)
            y = r + self.gamma * (1 - fin) * self.objetivo(s2).gather(1, mejor).squeeze(1)
        perdida = nn.functional.smooth_l1_loss(q, y)
        self.opt.zero_grad()
        perdida.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 10.0)
        self.opt.step()
        self.pasos += 1
        if self.pasos % self.sincronizar == 0:
            self.objetivo.load_state_dict(self.q.state_dict())
        return perdida.item()

    def guardar(self, ruta, extra=None):
        torch.save({"estado": self.q.state_dict(), "rasgos": RASGOS, "modos": MODOS, **(extra or {})}, ruta)


def cargar_politica(ruta):
    """Para el agente: red ya entrenada (solo inferencia). Devuelve (red, metadatos) o (None, None)."""
    try:
        datos = torch.load(ruta, map_location="cpu", weights_only=False)
        red = RedQ()
        red.load_state_dict(datos["estado"])
        red.eval()
        return red, datos
    except (OSError, RuntimeError, KeyError):
        return None, None
