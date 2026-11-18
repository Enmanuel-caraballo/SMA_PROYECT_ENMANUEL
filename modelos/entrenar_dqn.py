"""
SMA Movilidad y Seguridad CTG - ENTRENAMIENTO DEL SEMAFÓRICO NEURONAL (DQN) EN SUMO Y COMPARACIÓN DE CONTROLADORES
==================================================================================================================
1) Entrena la DQN (modelos/dqn.py) en Bocagrande-SUMO con escenarios aleatorios: hora del día con su demanda real
   (perfil SIMM), accidentes reales (DATT) o en el cruce5, y demanda ±. El estado incluye la predicción de la T-GCN.
2) Compara con las MISMAS semillas y escenarios: semáforo fijo, max-pressure (matemático), tabla Q actual y DQN.
   Métricas: demora total (veh·h), viaje medio (s), vehículos que terminan su viaje, cola media.

Uso:
    python modelos/entrenar_dqn.py --episodios 60
    python modelos/entrenar_dqn.py --solo-comparar
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno_sumo as E  # noqa: E402
from dqn import MODOS, AgenteDQN, cargar_politica, vector_estado  # noqa: E402
from predictor_colas import PredictorColas  # noqa: E402

SALIDA = Path(__file__).resolve().parent / "entrenados"
RUTA_DQN = SALIDA / "dqn_semaforico.pt"


def estados(obs, previas, pred):
    p = pred["cruces"] if pred else None
    return {c: vector_estado(c, obs["colas"], previas, obs["modo"][c], p, obs["hora"], E.VECINOS[c]) for c in E.CRUCES}


def entrenar(episodios, duracion):
    torch.manual_seed(1)
    random.seed(1)
    ag = AgenteDQN()
    total_pasos = episodios * (duracion // E.INTERVALO) * 0.6
    historia = []
    for ep in range(episodios):
        rng = random.Random(500 + ep)
        hora, acc, escala = E.escenario_aleatorio(rng)
        env = E.EntornoBocagrande(semilla=500 + ep, hora=hora, accidente=acc, duracion=duracion, escala=escala)
        predictor = PredictorColas()
        obs = env.reset()
        previas = obs["colas"]
        predictor.agregar(obs["colas"], obs["hora"])
        s = estados(obs, previas, predictor.predecir())
        fin, suma, perdidas = False, 0.0, []
        while not fin:
            eps = max(0.05, 1.0 - ag.pasos / max(1, total_pasos))
            acciones = {c: ag.elegir(s[c], eps) for c in E.CRUCES}
            obs2, r, fin, _ = env.step({c: MODOS[a] for c, a in acciones.items()})
            predictor.agregar(obs2["colas"], obs2["hora"])
            s2 = estados(obs2, obs["colas"], predictor.predecir())
            for c in E.CRUCES:
                ag.recordar(s[c], acciones[c], r[c], s2[c], float(fin))
                suma += r[c]
            for _ in range(2):
                l = ag.aprender()
                if l is not None:
                    perdidas.append(l)
            obs, s = obs2, s2
        m = env.metricas()
        env.cerrar()
        historia.append({"episodio": ep + 1, "hora": hora, "accidente": str(acc), "recompensa": round(suma, 1),
                         "demora_veh_h": m["demora_veh_h"], "llegados": m["llegados"], "epsilon": round(eps, 3),
                         "perdida": round(float(np.mean(perdidas)), 4) if perdidas else None})
        print(f"ep {ep + 1}/{episodios} hora {hora} acc {acc} recompensa {suma:8.1f} demora {m['demora_veh_h']} veh·h "
              f"llegados {m['llegados']} ε={eps:.2f} pérdida {historia[-1]['perdida']}", flush=True)
        if (ep + 1) % 10 == 0:
            ag.guardar(RUTA_DQN, {"episodios": ep + 1, "historia": historia, "predictor": predictor.info})
    ag.guardar(RUTA_DQN, {"episodios": episodios, "historia": historia, "predictor": predictor.info})
    return historia


def politica_dqn():
    red, _ = cargar_politica(RUTA_DQN)
    predictor = PredictorColas()
    estado = {"previas": None}

    def decidir(obs):
        predictor.agregar(obs["colas"], obs["hora"])
        s = estados(obs, estado["previas"] or obs["colas"], predictor.predecir())
        estado["previas"] = obs["colas"]
        with torch.no_grad():
            return {c: MODOS[int(red(torch.as_tensor(s[c]).unsqueeze(0)).argmax())] for c in E.CRUCES}
    return decidir


def comparar(duracion):
    escenarios = [(h, acc) for h in (8, 13, 17, 19) for acc in (None, {"cruce": "cruce5", "sentido": "salida"}, "real")]
    controles = {"Semáforo fijo": lambda: E.control_fijo, "Max-pressure (matemático)": lambda: E.control_max_presion,
                 "Tabla Q (actual)": E.control_tabla_q}
    if RUTA_DQN.exists():
        controles["DQN + T-GCN (neuronal)"] = politica_dqn
    filas = []
    for i, (hora, acc) in enumerate(escenarios):
        for nombre, fabrica in controles.items():
            control = fabrica()
            env = E.EntornoBocagrande(semilla=9000 + i, hora=hora, accidente=acc, duracion=duracion, escala=1.1)
            obs = env.reset()
            fin, colas = False, []
            while not fin:
                obs, _, fin, _ = env.step(control(obs))
                colas.append(sum(sum(v) for v in obs["colas"].values()))
            m = env.metricas()
            env.cerrar()
            filas.append({"escenario": i, "hora": hora, "accidente": "ninguno" if not acc else ("real DATT" if acc == "real" else "cruce5 salida"),
                          "controlador": nombre, "demora_veh_h": m["demora_veh_h"], "viaje_medio_s": m["viaje_medio_s"],
                          "llegados": m["llegados"], "cola_media": round(float(np.mean(colas)), 2)})
            print(f"esc {i} hora {hora} {filas[-1]['accidente']:13s} {nombre:28s} demora {m['demora_veh_h']:6.1f} veh·h  "
                  f"viaje {m['viaje_medio_s']} s  llegados {m['llegados']}  cola {filas[-1]['cola_media']}", flush=True)
    import pandas as pd
    d = pd.DataFrame(filas)
    d.to_csv(SALIDA / "comparacion_controladores.csv", index=False, encoding="utf-8")
    resumen = d.groupby("controlador")[["demora_veh_h", "viaje_medio_s", "llegados", "cola_media"]].mean().round(2)
    base = resumen.loc["Semáforo fijo", "demora_veh_h"]
    resumen["mejora_demora_vs_fijo_%"] = ((base - resumen["demora_veh_h"]) / base * 100).round(1)
    print("\n", resumen.to_string())
    (SALIDA / "comparacion_controladores.json").write_text(
        json.dumps({"escenarios": len(escenarios), "duracion_s": duracion, "resumen": resumen.reset_index().to_dict("records")},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(14, 4))
        for k, col in enumerate(["demora_veh_h", "viaje_medio_s", "cola_media"]):
            resumen[col].plot.bar(ax=ax[k], color=["#7c8a99", "#2979ff", "#ffb300", "#00c853"][:len(resumen)])
            ax[k].set_title({"demora_veh_h": "Demora total (veh·h)", "viaje_medio_s": "Viaje medio (s)",
                             "cola_media": "Cola media (veh)"}[col])
            ax[k].tick_params(axis="x", rotation=25)
        fig.suptitle(f"Bocagrande en SUMO · {len(escenarios)} escenarios (horas 8/13/17/19; sin accidente, cruce5, accidente real)")
        fig.tight_layout()
        fig.savefig(SALIDA / "comparacion_controladores.png", dpi=110)
    except Exception as error:
        print(f"(sin gráfica: {error})")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--episodios", type=int, default=60)
    p.add_argument("--duracion", type=int, default=1800)
    p.add_argument("--solo-comparar", action="store_true")
    args = p.parse_args()
    SALIDA.mkdir(parents=True, exist_ok=True)
    if not args.solo_comparar:
        t = time.time()
        entrenar(args.episodios, args.duracion)
        print(f"entrenamiento: {(time.time() - t) / 60:.1f} min")
    comparar(args.duracion)
