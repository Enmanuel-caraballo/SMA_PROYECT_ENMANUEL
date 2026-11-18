"""
SMA Movilidad y Seguridad CTG - PREDICTOR DE COLAS (T-GCN entrenada) para el Agente Predictor y el entrenamiento DQN
====================================================================================================================
Recibe cada 12 s la cola media de cada acceso (A, B) de los 5 cruces y, con la ventana de las últimas lecturas,
predice las colas de los próximos 3 pasos (36 s). Si no hay modelo entrenado o aún no hay historia suficiente,
devuelve None y quien lo usa sigue con la cola actual (modo heurístico).
"""

from collections import deque
from pathlib import Path

import numpy as np
import torch

from tgcn import TGCN

RUTA = Path(__file__).resolve().parent / "entrenados" / "tgcn_bocagrande.pt"
CRUCES = ["cruce1", "cruce2", "cruce3", "cruce4", "cruce5"]


class PredictorColas:
    def __init__(self, ruta=RUTA):
        self.modelo = None
        self.historia = None
        try:
            d = torch.load(ruta, map_location="cpu", weights_only=False)
            self.modelo = TGCN(d["a_norm"], d["rasgos"], d["oculto"], d["horizonte"])
            self.modelo.load_state_dict(d["estado"])
            self.modelo.eval()
            self.nodos, self.ventana, self.horizonte = d["nodos"], d["ventana"], d["horizonte"]
            self.media, self.desv = np.asarray(d["media"]), np.asarray(d["desv"])
            self.historia = deque(maxlen=self.ventana)
            self.info = {"modelo": "T-GCN (GCN + GRU)", "ventana": self.ventana, "horizonte": self.horizonte,
                         "paso": d.get("paso", "12 s"), "nodos": len(self.nodos)}
        except (OSError, RuntimeError, KeyError) as error:
            self.info = {"modelo": "heurístico (sin red entrenada)", "motivo": str(error)[:120]}

    @property
    def disponible(self):
        return self.modelo is not None

    def agregar(self, colas, hora):
        """colas: {cruce: (A, B)} promediadas en el intervalo; hora: hora del día (float)."""
        if self.historia is not None:
            self.historia.append((float(hora), [colas.get(n[:-2], (0.0, 0.0))[0 if n.endswith("A") else 1] for n in self.nodos]))

    def predecir(self):
        """-> {"cruces": {cruce: (A, B) media de los próximos pasos}, "pasos": [[...]], "confianza": None} o None."""
        if not self.disponible or len(self.historia) < self.ventana:
            return None
        horas = np.array([h for h, _ in self.historia])
        v = (np.array([x for _, x in self.historia], dtype=np.float32) - self.media) / self.desv
        rt = np.stack([np.sin(2 * np.pi * horas / 24), np.cos(2 * np.pi * horas / 24),
                       np.zeros_like(horas), np.ones_like(horas)], -1).astype(np.float32)   # día fijo (lunes), como en SUMO
        x = np.concatenate([v[:, :, None], np.repeat(rt[:, None, :], v.shape[1], 1)], -1)[None]
        with torch.no_grad():
            y = self.modelo(torch.as_tensor(x, dtype=torch.float32))[0].numpy()
        y = np.maximum(y * self.desv + self.media, 0)          # (horizonte, nodos)
        idx = {n: i for i, n in enumerate(self.nodos)}
        cruces = {c: (float(y[:, idx[f"{c}_A"]].mean()), float(y[:, idx[f"{c}_B"]].mean())) for c in CRUCES}
        return {"cruces": cruces, "pasos": np.round(y, 2).tolist()}
