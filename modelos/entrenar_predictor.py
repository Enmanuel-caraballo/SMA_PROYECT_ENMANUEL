"""
SMA Movilidad y Seguridad CTG - ENTRENAMIENTO Y EVALUACIÓN DE LA RED DE PREDICCIÓN (T-GCN)
=========================================================================================
Datos:
  --datos simm        flujo vehicular REAL por hora de 156 estaciones de Medellín (SIMM, julio 2020, limpiado
                      por modelos/datos_reales.py). Predice la intensidad (veh/h) de las próximas 1-3 horas.
  --datos bocagrande  colas por acceso de los 5 cruces de Bocagrande medidas en SUMO con demanda y accidentes reales
                      (modelos/datos/bocagrande_colas.csv.gz, lo genera modelos/entorno_sumo.py --recolectar).
                      Predice la cola de cada acceso en los próximos 3 pasos de 12 s (36 s); es lo que usa el Agente Predictor.

División CRONOLÓGICA 70 / 15 / 15 (entrenamiento / validación / prueba): el modelo nunca ve el futuro.
Se compara contra líneas base: persistencia (mañana = hoy), promedio histórico (misma hora y día) y GRU sin grafo.

Uso:
    python modelos/entrenar_predictor.py --datos simm
    python modelos/entrenar_predictor.py --datos bocagrande
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tgcn import GRUSinGrafo, TGCN, adyacencia_gaussiana, normalizar_adyacencia  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "modelos" / "datos"
SALIDA = RAIZ / "modelos" / "entrenados"

CONFIG = {
    "simm": {"ventana": 12, "horizonte": 3, "paso": "1 h", "unidad": "veh/h", "oculto": 32, "epocas": 120},
    "bocagrande": {"ventana": 15, "horizonte": 3, "paso": "12 s", "unidad": "veh en cola", "oculto": 32, "epocas": 200},
}


def cargar(nombre):
    if nombre == "simm":
        serie = pd.read_csv(DATOS / "simm_intensidad.csv.gz", index_col=0, parse_dates=True)
        meta = pd.read_csv(DATOS / "simm_estaciones.csv")
        coords = meta[["x", "y"]].values
        nombres = list(serie.columns)
        tiempo = serie.index
        return serie.values.astype(np.float32), coords, nombres, tiempo, None
    serie = pd.read_csv(DATOS / "bocagrande_colas.csv.gz")
    geo = json.loads((RAIZ / "maqueta_virtual" / "bocagrande" / "bocagrande.json").read_text(encoding="utf-8"))
    cruces = [c["id"] for c in geo["cruces"]]
    nodos = [f"{c}_{g}" for c in cruces for g in "AB"]                       # 10 nodos: acceso A y B de cada cruce
    pos = {c["id"]: (c["x"], c["z"]) for c in geo["cruces"]}
    coords = np.array([[pos[n[:-2]][0] + (0 if n.endswith("A") else 15), pos[n[:-2]][1]] for n in nodos])
    tiempo = pd.to_datetime(serie["hora_sim"], unit="h", origin="2026-11-16")
    extra = serie[["episodio"]].values
    return serie[nodos].values.astype(np.float32), coords, nodos, pd.DatetimeIndex(tiempo), extra


def rasgos_tiempo(tiempo):
    h = tiempo.hour.values + tiempo.minute.values / 60
    d = tiempo.dayofweek.values
    return np.stack([np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24),
                     np.sin(2 * np.pi * d / 7), np.cos(2 * np.pi * d / 7)], -1).astype(np.float32)


def ventanas(valores, rt, ventana, horizonte, cortes=None):
    """X: (muestras, ventana, nodos, 1 + 4), Y: (muestras, horizonte, nodos). cortes: índices donde empieza un episodio."""
    n = len(valores)
    inicio_valido = np.zeros(n, bool)
    validos = []
    limites = sorted(set([0, n] + list(cortes if cortes is not None else [])))
    for a, b in zip(limites[:-1], limites[1:]):
        validos += list(range(a + ventana, b - horizonte + 1))
    X = np.stack([np.concatenate([valores[i - ventana:i, :, None],
                                  np.repeat(rt[i - ventana:i, None, :], valores.shape[1], 1)], -1) for i in validos])
    Y = np.stack([valores[i:i + horizonte] for i in validos])
    return X, Y, np.array(validos)


def metricas(real, pred, umbral_mape):
    err = pred - real
    mascara = real > umbral_mape
    return {"MAE": float(np.abs(err).mean()), "RMSE": float(np.sqrt((err ** 2).mean())),
            "MAPE_%": float(np.abs(err[mascara] / real[mascara]).mean() * 100) if mascara.any() else None}


def entrenar(modelo, Xtr, Ytr, Xva, Yva, epocas, lr=2e-3, lote=32, paciencia=20):
    opt = torch.optim.Adam(modelo.parameters(), lr=lr, weight_decay=1e-5)
    perdida = torch.nn.MSELoss()
    Xtr, Ytr, Xva, Yva = map(torch.as_tensor, (Xtr, Ytr, Xva, Yva))
    mejor, estado, sin_mejora, historia = np.inf, None, 0, []
    for ep in range(epocas):
        modelo.train()
        orden = torch.randperm(len(Xtr))
        total = 0.0
        for k in range(0, len(Xtr), lote):
            i = orden[k:k + lote]
            opt.zero_grad()
            l = perdida(modelo(Xtr[i]), Ytr[i])
            l.backward()
            torch.nn.utils.clip_grad_norm_(modelo.parameters(), 5.0)
            opt.step()
            total += l.item() * len(i)
        modelo.eval()
        with torch.no_grad():
            val = perdida(modelo(Xva), Yva).item()
        historia.append((total / len(Xtr), val))
        if ep % 10 == 0:
            print(f"    época {ep}: entrenamiento {total / len(Xtr):.4f}  validación {val:.4f}", flush=True)
        if val < mejor - 1e-5:
            mejor, sin_mejora = val, 0
            estado = {k: v.clone() for k, v in modelo.state_dict().items()}
        else:
            sin_mejora += 1
            if sin_mejora >= paciencia:
                break
    modelo.load_state_dict(estado)
    return historia


def main():
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--datos", choices=list(CONFIG), default="simm")
    p.add_argument("--epocas", type=int)
    p.add_argument("--hilos", type=int, default=2, help="hilos de CPU para PyTorch")
    args = p.parse_args()
    cfg = CONFIG[args.datos]
    torch.manual_seed(0)
    np.random.seed(0)
    torch.set_num_threads(max(1, args.hilos))
    SALIDA.mkdir(parents=True, exist_ok=True)

    valores, coords, nombres, tiempo, extra = cargar(args.datos)
    n_t, n_nodos = valores.shape
    corte_tr, corte_va = int(n_t * 0.70), int(n_t * 0.85)
    media = valores[:corte_tr].mean(0)
    desv = valores[:corte_tr].std(0) + 1e-6
    z = (valores - media) / desv
    rt = rasgos_tiempo(tiempo)
    cortes = None
    if extra is not None:                               # no mezclar ventanas de episodios distintos de SUMO
        ep = extra[:, 0]
        cortes = list(np.where(np.diff(ep) != 0)[0] + 1)
    X, Y, idx = ventanas(z, rt, cfg["ventana"], cfg["horizonte"], cortes)
    tr, va, te = idx < corte_tr, (idx >= corte_tr) & (idx < corte_va), idx >= corte_va
    tr &= idx + cfg["horizonte"] <= corte_tr                     # el objetivo tampoco cruza la frontera
    print(f"datos {args.datos}: {n_t} pasos x {n_nodos} nodos; muestras entrenamiento={tr.sum()}, "
          f"validación={va.sum()}, prueba={te.sum()}")

    a = adyacencia_gaussiana(coords)
    a_norm = normalizar_adyacencia(a)
    rasgos = X.shape[-1]
    epocas = args.epocas or cfg["epocas"]
    resultados, modelos = {}, {}
    desnorm = lambda y: y * desv[None, None, :] + media[None, None, :]
    real = desnorm(Y[te])
    umbral = 50 if args.datos == "simm" else 1.0

    # líneas base
    persist = np.repeat(valores[idx[te] - 1][:, None, :], cfg["horizonte"], 1)
    resultados["Persistencia (ingenuo)"] = metricas(real, persist, umbral)
    clave_t = pd.DataFrame({"d": tiempo.dayofweek, "h": tiempo.hour, "m": tiempo.minute // 10 * 10})
    tabla = pd.DataFrame(valores[:corte_tr]).groupby([clave_t.d[:corte_tr].values, clave_t.h[:corte_tr].values]).mean()
    prom = np.zeros_like(real)
    for j, i in enumerate(idx[te]):
        for k in range(cfg["horizonte"]):
            t = i + k
            llave = (clave_t.d.iloc[t], clave_t.h.iloc[t])
            prom[j, k] = tabla.loc[llave].values if llave in tabla.index else valores[:corte_tr].mean(0)
    resultados["Promedio histórico (misma hora y día)"] = metricas(real, prom, umbral)

    for nombre, modelo in [("GRU sin grafo (ablación)", GRUSinGrafo(n_nodos, rasgos, cfg["oculto"], cfg["horizonte"])),
                           ("T-GCN (GCN + GRU)", TGCN(a_norm, rasgos, cfg["oculto"], cfg["horizonte"]))]:
        t0 = time.time()
        historia = entrenar(modelo, X[tr], Y[tr], X[va], Y[va], epocas)
        modelo.eval()
        with torch.no_grad():
            pred = desnorm(modelo(torch.as_tensor(X[te])).numpy())
        pred = np.maximum(pred, 0)
        resultados[nombre] = {**metricas(real, pred, umbral), "epocas": len(historia),
                              "segundos": round(time.time() - t0, 1),
                              "por_horizonte_MAE": [float(np.abs(pred[:, k] - real[:, k]).mean()) for k in range(cfg["horizonte"])]}
        modelos[nombre] = (modelo, pred, historia)
        print(f"  {nombre}: MAE {resultados[nombre]['MAE']:.2f}  RMSE {resultados[nombre]['RMSE']:.2f}  "
              f"({len(historia)} épocas, {time.time() - t0:.0f} s)")

    for k, v in resultados.items():
        print(f"{k:40s} MAE={v['MAE']:8.2f}  RMSE={v['RMSE']:8.2f}  MAPE={v['MAPE_%'] if v['MAPE_%'] is None else round(v['MAPE_%'], 1)}")

    modelo, pred, historia = modelos["T-GCN (GCN + GRU)"]
    base = resultados["Persistencia (ingenuo)"]["MAE"]
    mejora = 100 * (base - resultados["T-GCN (GCN + GRU)"]["MAE"]) / base
    torch.save({"estado": modelo.state_dict(), "a_norm": a_norm, "media": media, "desv": desv, "nodos": nombres,
                "ventana": cfg["ventana"], "horizonte": cfg["horizonte"], "oculto": cfg["oculto"], "rasgos": rasgos,
                "paso": cfg["paso"], "unidad": cfg["unidad"]}, SALIDA / f"tgcn_{args.datos}.pt")
    informe = {"datos": args.datos, "paso": cfg["paso"], "unidad": cfg["unidad"], "ventana": cfg["ventana"],
               "horizonte": cfg["horizonte"], "nodos": n_nodos, "aristas_grafo": int((a > 0).sum() - n_nodos),
               "muestras": {"entrenamiento": int(tr.sum()), "validacion": int(va.sum()), "prueba": int(te.sum())},
               "resultados": resultados, "mejora_vs_persistencia_%": round(mejora, 1),
               "fecha": time.strftime("%Y-%m-%d %H:%M")}
    (SALIDA / f"metricas_{args.datos}.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nT-GCN mejora el MAE {mejora:.1f} % frente a la persistencia. Guardado en {SALIDA}")

    try:   # gráfica: real vs predicción en el conjunto de prueba (el nodo más cargado) + curva de aprendizaje
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        j = int(np.argmax(valores.mean(0)))
        fig, ax = plt.subplots(1, 2, figsize=(13, 4))
        ax[0].plot(real[:, 0, j], label="real", lw=2)
        ax[0].plot(pred[:, 0, j], label="T-GCN", lw=1.5)
        ax[0].plot(persist[:, 0, j], label="persistencia", lw=1, alpha=.6)
        ax[0].set_title(f"Prueba · {nombres[j][:40]} · próximo paso ({cfg['paso']})")
        ax[0].set_ylabel(cfg["unidad"])
        ax[0].legend()
        h = np.array(historia)
        ax[1].plot(h[:, 0], label="entrenamiento")
        ax[1].plot(h[:, 1], label="validación")
        ax[1].set_title("Curva de aprendizaje (MSE normalizado)")
        ax[1].set_xlabel("época")
        ax[1].legend()
        fig.tight_layout()
        fig.savefig(SALIDA / f"prediccion_{args.datos}.png", dpi=110)
    except Exception as error:
        print(f"(sin gráfica: {error})")


if __name__ == "__main__":
    main()
