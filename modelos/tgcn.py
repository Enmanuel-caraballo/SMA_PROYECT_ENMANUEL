"""
SMA Movilidad y Seguridad CTG - RED NEURONAL ESPACIO-TEMPORAL T-GCN (GCN + GRU), PyTorch puro
=============================================================================================
T-GCN: Zhao, L. et al. (2019) "T-GCN: A Temporal Graph Convolutional Network for Traffic Prediction",
IEEE Transactions on Intelligent Transportation Systems 21(9). Código de referencia: github.com/lehaifeng/T-GCN

Idea: el tráfico de un punto depende de sus VECINOS (espacio) y de su HISTORIA (tiempo).
  - Convolución de grafo (GCN, Kipf & Welling 2017): cada nodo mezcla su estado con el de sus vecinos
        GC(X) = Â · X · W,     Â = D^(-1/2) (A + I) D^(-1/2)   (matriz de adyacencia normalizada)
  - Celda GRU con convoluciones de grafo en lugar de capas densas:
        u, r = σ(GC([X_t, H_{t-1}]))          (compuertas de actualización y de reinicio)
        c    = tanh(GC([X_t, r ⊙ H_{t-1}]))   (estado candidato)
        H_t  = u ⊙ H_{t-1} + (1 − u) ⊙ c
  - Al final una capa lineal por nodo predice los próximos HORIZONTE pasos.

Se implementa sin torch-geometric (que da problemas en Windows con Python 3.13): con 5-200 nodos la
matriz Â densa es pequeña y la multiplicación directa es rápida en CPU.
"""

import numpy as np
import torch
from torch import nn


def adyacencia_gaussiana(coords, umbral=0.1, sigma=None):
    """Grafo por cercanía (DCRNN, Li et al. 2018): W_ij = exp(-d_ij² / σ²) si >= umbral."""
    coords = np.asarray(coords, dtype=float)
    d = np.sqrt(((coords[:, None, :] - coords[None, :, :]) ** 2).sum(-1))
    sigma = sigma or d[d > 0].std()
    w = np.exp(-(d / sigma) ** 2)
    w[w < umbral] = 0.0
    return w


def normalizar_adyacencia(a):
    """Â = D^(-1/2) (A + I) D^(-1/2)"""
    a = np.asarray(a, dtype=float) + np.eye(len(a))
    d = a.sum(1)
    dinv = np.diag(1.0 / np.sqrt(d))
    return dinv @ a @ dinv


class ConvGrafo(nn.Module):
    def __init__(self, entrada, salida, a_norm):
        super().__init__()
        self.register_buffer("a", torch.as_tensor(a_norm, dtype=torch.float32))
        self.lineal = nn.Linear(entrada, salida)

    def forward(self, x):                      # x: (lote, nodos, rasgos)
        return self.lineal(torch.einsum("ij,bjf->bif", self.a, x))


class CeldaTGCN(nn.Module):
    def __init__(self, rasgos, oculto, a_norm):
        super().__init__()
        self.oculto = oculto
        self.compuertas = ConvGrafo(rasgos + oculto, 2 * oculto, a_norm)
        self.candidato = ConvGrafo(rasgos + oculto, oculto, a_norm)

    def forward(self, x, h):
        ur = torch.sigmoid(self.compuertas(torch.cat([x, h], -1)))
        u, r = ur.chunk(2, -1)
        c = torch.tanh(self.candidato(torch.cat([x, r * h], -1)))
        return u * h + (1 - u) * c


class TGCN(nn.Module):
    """Entrada (lote, ventana, nodos, rasgos) -> salida (lote, horizonte, nodos)."""

    def __init__(self, a_norm, rasgos, oculto=32, horizonte=3):
        super().__init__()
        self.celda = CeldaTGCN(rasgos, oculto, a_norm)
        self.salida = nn.Linear(oculto, horizonte)
        self.oculto = oculto

    def forward(self, x):
        b, t, n, _ = x.shape
        h = x.new_zeros(b, n, self.oculto)
        for k in range(t):
            h = self.celda(x[:, k], h)
        return self.salida(h).permute(0, 2, 1)


class GRUSinGrafo(nn.Module):
    """Ablación: la misma GRU pero cada nodo solo ve su propia historia (Â = I). Mide cuánto aporta el grafo."""

    def __init__(self, n, rasgos, oculto=32, horizonte=3):
        super().__init__()
        self.red = TGCN(np.eye(n), rasgos, oculto, horizonte)

    def forward(self, x):
        return self.red(x)
