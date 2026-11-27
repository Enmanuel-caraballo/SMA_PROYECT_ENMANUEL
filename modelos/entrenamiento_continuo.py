"""
SMA Movilidad y Seguridad CTG - ENTRENAMIENTO CONTINUO BAJO PRESIÓN (DQN del Semafórico + T-GCN del Predictor)
===============================================================================================================
Corre rondas indefinidamente (hasta detenerlo) con el esquema CAMPEÓN / RETADOR:

  1. Escenarios de estrés en SUMO: hora pico, demanda hasta +60 %, más accidentes (reales del DATT y en la salida
     o la entrada del cruce5).
  2. La DQN sigue entrenando desde los pesos del campeón (retador). Cada pocos pasos un ORÁCULO revisa una decisión:
     guarda el estado de SUMO, prueba las 3 acciones del cruce durante 36 s y mide la cola resultante. Si había una
     acción claramente mejor, la decisión se CORRIGE: se guarda (estado, acción mejor) y la red aprende con una pérdida
     de margen (como DQfD, Hester et al. 2018) además de la pérdida Double DQN.
  3. Evaluación en 8 escenarios FIJOS de estrés que nunca se usan para entrenar (mismas semillas para los dos):
     el retador reemplaza al campeón solo si baja la demora media al menos MEJORA_MINIMA y gana en la mayoría.
     El campeón anterior queda respaldado en modelos/entrenados/historial/.
  4. Cada 2 rondas la T-GCN se ajusta con las colas de los escenarios de estrés y también compite: se promueve si
     baja el MAE en un conjunto de prueba fijo de estrés sin empeorar en los datos originales.

Todo queda en modelos/entrenados/entrenamiento_continuo.jsonl (una línea por ronda).

Uso:
    python modelos/entrenamiento_continuo.py               (corre hasta Ctrl+C / detenerlo)
    python modelos/entrenamiento_continuo.py --rondas 3
    python modelos/entrenamiento_continuo.py --resumen     (tabla y gráfica de la evolución)
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno_sumo as E  # noqa: E402
from dqn import MODOS, AgenteDQN, cargar_politica, vector_estado  # noqa: E402

CARPETA = Path(__file__).resolve().parent
SALIDA = CARPETA / "entrenados"
DATOS = CARPETA / "datos"
HISTORIAL = SALIDA / "historial"
RUTA_DQN = SALIDA / "dqn_semaforico.pt"
RUTA_TGCN = SALIDA / "tgcn_bocagrande.pt"
REGISTRO = SALIDA / "entrenamiento_continuo.jsonl"
ESTRES = DATOS / "bocagrande_colas_estres.csv.gz"
PRUEBA_ESTRES = DATOS / "bocagrande_colas_prueba_estres.csv.gz"

DURACION = 1500                 # s simulados por episodio
EPISODIOS_POR_RONDA = 6
AUDITAR_CADA = 4                # pasos de control (12 s) entre revisiones del oráculo
HORIZONTE_ORACULO = 3           # pasos de 12 s = 36 s
MARGEN_CORRECCION = 0.10        # la acción del oráculo debe ser ≥10 % mejor (y ≥0.5 veh) para corregir
MEJORA_MINIMA = 0.5             # % de demora media para promover al retador
RONDAS_SIN_MEJORA_REINICIO = 3  # tras tantas rondas sin promover, el retador vuelve a partir del campeón

PICOS = [7, 8, 12, 13, 17, 18, 19]
ESCENARIOS_PRUEBA = [           # fijos, nunca usados para entrenar
    (8, None, 1.0), (8, "real", 1.25), (13, "real", 1.0), (17, None, 1.3),
    (17, {"cruce": "cruce5", "sentido": "salida"}, 1.0), (17, "real", 1.45),
    (19, {"cruce": "cruce5", "sentido": "entrada"}, 1.15), (18, None, 1.55),
]


def log(texto):
    print(f"[{time.strftime('%H:%M:%S')}] {texto}", flush=True)


def escenario_estres(rng):
    hora = rng.choice(PICOS) if rng.random() < 0.8 else rng.choice([9, 11, 15, 16, 21])
    acc = rng.choice([None, "real", "real", {"cruce": "cruce5", "sentido": "salida"},
                      {"cruce": "cruce5", "sentido": "entrada"}])
    return hora, acc, rng.uniform(1.0, 1.6)


# ====================================================================== predictor (T-GCN) en SUMO
def nuevo_predictor():
    from predictor_colas import PredictorColas
    return PredictorColas(RUTA_TGCN)


def estados(obs, previas, pred):
    p = pred["cruces"] if pred else None
    return {c: vector_estado(c, obs["colas"], previas, obs["modo"][c], p, obs["hora"], E.VECINOS[c]) for c in E.CRUCES}


def greedy(red, s):
    with torch.no_grad():
        return int(red(torch.as_tensor(s).unsqueeze(0))[0].argmax())


# ====================================================================== oráculo: prueba las 3 acciones con SUMO
ATRIBUTOS = ("t", "fase", "modo", "esperas", "llegados", "salidas", "viajes", "accidente", "colas_prev")


def oraculo(env, acciones, cruce):
    """Costo (cola propia + 0.3 x vecinos, sumado en 36 s) de cada modo del cruce, con los demás cruces fijos.
    Deja la simulación exactamente como estaba."""
    archivo = Path(tempfile.gettempdir()) / f"sma_oraculo_{env.semilla}.xml"
    env.traci.simulation.saveState(str(archivo))
    guardado = {a: copy.deepcopy(getattr(env, a)) for a in ATRIBUTOS}
    costos = []
    for modo in MODOS:
        env.traci.simulation.loadState(str(archivo))
        for a, v in guardado.items():
            setattr(env, a, copy.deepcopy(v))
        for c in E.CRUCES:
            env._luces(c)
        prueba = {**acciones, cruce: modo}
        costo = 0.0
        for _ in range(HORIZONTE_ORACULO):
            obs, _, fin, _ = env.step(prueba)
            q = obs["colas"]
            costo += sum(q[cruce]) + 0.3 * np.mean([sum(q[v]) for v in E.VECINOS[cruce]])
            if fin:
                break
        costos.append(costo)
    env.traci.simulation.loadState(str(archivo))
    for a, v in guardado.items():
        setattr(env, a, v)
    for c in E.CRUCES:
        env._luces(c)
    return costos


# ====================================================================== retador: Double DQN + correcciones
class Retador(AgenteDQN):
    def __init__(self, estado_campeon):
        super().__init__(lr=2e-4, memoria=40000)
        self.q.load_state_dict(estado_campeon)
        self.objetivo.load_state_dict(estado_campeon)
        self.correcciones = deque(maxlen=6000)

    def aprender_correcciones(self, lote=64, peso=1.0, margen=0.8):
        """Pérdida de margen: Q(s, a*) debe superar a las demás acciones por al menos `margen`."""
        if len(self.correcciones) < 32:
            return None
        muestra = random.sample(self.correcciones, min(lote, len(self.correcciones)))
        s, a = map(np.array, zip(*muestra))
        q = self.q(torch.as_tensor(s))
        a = torch.as_tensor(a)
        l = torch.full_like(q, margen)
        l.scatter_(1, a.unsqueeze(1), 0.0)
        perdida = peso * ((q + l).max(1).values - q.gather(1, a.unsqueeze(1)).squeeze(1)).mean()
        self.opt.zero_grad()
        perdida.backward()
        torch.nn.utils.clip_grad_norm_(self.q.parameters(), 10.0)
        self.opt.step()
        return perdida.item()


def episodio_entrenamiento(ag, semilla, filas_estres, ronda, k):
    rng = random.Random(semilla)
    hora, acc, escala = escenario_estres(rng)
    env = E.EntornoBocagrande(semilla=semilla, hora=hora, accidente=acc, duracion=DURACION, escala=escala)
    predictor = nuevo_predictor()
    obs = env.reset()
    predictor.agregar(obs["colas"], obs["hora"])
    s = estados(obs, obs["colas"], predictor.predecir())
    fin, paso, auditadas, corregidas = False, 0, 0, 0
    id_ep = 100000 + ronda * 100 + k
    while not fin:
        acciones_i = {c: ag.elegir(s[c], 0.08) for c in E.CRUCES}
        acciones = {c: MODOS[a] for c, a in acciones_i.items()}
        if paso % AUDITAR_CADA == AUDITAR_CADA - 1:
            cruce = max(E.CRUCES, key=lambda c: sum(obs["colas"][c]))      # el cruce con más presión
            costos = oraculo(env, acciones, cruce)
            auditadas += 1
            mejor = int(np.argmin(costos))
            elegido = acciones_i[cruce]
            if mejor != elegido and costos[elegido] - costos[mejor] > max(0.5, MARGEN_CORRECCION * costos[elegido]):
                ag.correcciones.append((s[cruce], mejor))
                corregidas += 1
        obs2, r, fin, _ = env.step(acciones)
        filas_estres.append({"episodio": id_ep, "hora_sim": obs2["hora"],
                             **{f"{c}_{g}": round(obs2["colas"][c][j], 2) for c in E.CRUCES for j, g in enumerate("AB")}})
        predictor.agregar(obs2["colas"], obs2["hora"])
        s2 = estados(obs2, obs["colas"], predictor.predecir())
        for c in E.CRUCES:
            ag.recordar(s[c], acciones_i[c], r[c], s2[c], float(fin))
        for _ in range(2):
            ag.aprender()
        ag.aprender_correcciones()
        obs, s = obs2, s2
        paso += 1
    m = env.metricas()
    env.cerrar()
    return {"hora": hora, "accidente": str(acc), "escala": round(escala, 2), "demora_veh_h": m["demora_veh_h"],
            "auditadas": auditadas, "corregidas": corregidas}


# ====================================================================== evaluación en escenarios fijos
def evaluar(red, recolectar=None):
    filas = []
    for i, (hora, acc, escala) in enumerate(ESCENARIOS_PRUEBA):
        env = E.EntornoBocagrande(semilla=9300 + i, hora=hora, accidente=acc, duracion=DURACION, escala=escala)
        predictor = nuevo_predictor()
        obs = env.reset()
        predictor.agregar(obs["colas"], obs["hora"])
        previas, fin, colas = obs["colas"], False, []
        while not fin:
            s = estados(obs, previas, predictor.predecir())
            previas = obs["colas"]
            obs, _, fin, _ = env.step({c: MODOS[greedy(red, s[c])] for c in E.CRUCES})
            predictor.agregar(obs["colas"], obs["hora"])
            colas.append(sum(sum(v) for v in obs["colas"].values()))
            if recolectar is not None:
                recolectar.append({"episodio": 900000 + i, "hora_sim": obs["hora"],
                                   **{f"{c}_{g}": round(obs["colas"][c][j], 2) for c in E.CRUCES for j, g in enumerate("AB")}})
        m = env.metricas()
        env.cerrar()
        filas.append({"demora_veh_h": m["demora_veh_h"], "viaje_medio_s": m["viaje_medio_s"], "llegados": m["llegados"],
                      "cola_media": round(float(np.mean(colas)), 2)})
    return filas


def resumen_eval(filas):
    return {k: round(float(np.mean([f[k] for f in filas if f[k] is not None])), 2)
            for k in ("demora_veh_h", "viaje_medio_s", "llegados", "cola_media")}


# ====================================================================== T-GCN: ajuste con datos de estrés
def ajustar_tgcn():
    """Ajuste fino de la T-GCN campeona con datos originales + estrés; se promueve si mejora en la prueba de estrés."""
    import pandas as pd
    from entrenar_predictor import entrenar, rasgos_tiempo, ventanas
    from tgcn import TGCN
    if not ESTRES.exists() or not PRUEBA_ESTRES.exists():
        return None
    d = torch.load(RUTA_TGCN, map_location="cpu", weights_only=False)
    nodos, media, desv = d["nodos"], np.asarray(d["media"]), np.asarray(d["desv"])

    def preparar(df):
        z = (df[nodos].values.astype(np.float32) - media) / desv
        rt = rasgos_tiempo(pd.DatetimeIndex(pd.to_datetime(df["hora_sim"], unit="h", origin="2026-11-16")))
        ep = df["episodio"].values
        cortes = list(np.where(np.diff(ep) != 0)[0] + 1)
        X, Y, _ = ventanas(z, rt, d["ventana"], d["horizonte"], cortes)
        return X.astype(np.float32), Y.astype(np.float32)

    original = pd.read_csv(DATOS / "bocagrande_colas.csv.gz")
    n = len(original)
    orig_tr, orig_te = original.iloc[:int(n * 0.70)], original.iloc[int(n * 0.85):]
    estres = pd.read_csv(ESTRES)
    eps = estres["episodio"].unique()
    corte = max(1, int(len(eps) * 0.8))
    est_tr, est_va = estres[estres["episodio"].isin(eps[:corte])], estres[estres["episodio"].isin(eps[corte:])]
    Xtr = np.concatenate([preparar(orig_tr)[0], preparar(est_tr)[0]])
    Ytr = np.concatenate([preparar(orig_tr)[1], preparar(est_tr)[1]])
    Xva, Yva = preparar(est_va) if len(est_va) else preparar(est_tr)
    Xp, Yp = preparar(pd.read_csv(PRUEBA_ESTRES))
    Xo, Yo = preparar(orig_te)

    def mae(modelo, X, Y):
        with torch.no_grad():
            p = np.maximum(modelo(torch.as_tensor(X)).numpy() * desv + media, 0)
        return float(np.abs(p - (Y * desv + media)).mean())

    campeon = TGCN(d["a_norm"], d["rasgos"], d["oculto"], d["horizonte"])
    campeon.load_state_dict(d["estado"])
    campeon.eval()
    retador = TGCN(d["a_norm"], d["rasgos"], d["oculto"], d["horizonte"])
    retador.load_state_dict(d["estado"])
    entrenar(retador, Xtr, Ytr, Xva, Yva, epocas=40, lr=5e-4, paciencia=8)
    retador.eval()
    res = {"mae_estres_campeon": round(mae(campeon, Xp, Yp), 4), "mae_estres_retador": round(mae(retador, Xp, Yp), 4),
           "mae_original_campeon": round(mae(campeon, Xo, Yo), 4), "mae_original_retador": round(mae(retador, Xo, Yo), 4),
           "muestras_estres": int(len(estres))}
    mejora = res["mae_estres_campeon"] - res["mae_estres_retador"]
    sin_daño = res["mae_original_retador"] <= res["mae_original_campeon"] * 1.01
    res["promovido"] = bool(mejora > 0.01 * res["mae_estres_campeon"] and sin_daño)
    if res["promovido"]:
        HISTORIAL.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUTA_TGCN, HISTORIAL / f"tgcn_bocagrande_{time.strftime('%Y%m%d_%H%M%S')}.pt")
        torch.save({**d, "estado": retador.state_dict(), "ajuste_estres": res,
                    "fecha_ajuste": time.strftime("%Y-%m-%d %H:%M")}, RUTA_TGCN)
    return res


# ====================================================================== bucle principal
def guardar_campeon(red_estado, info):
    HISTORIAL.mkdir(parents=True, exist_ok=True)
    if RUTA_DQN.exists():
        shutil.copy2(RUTA_DQN, HISTORIAL / f"dqn_semaforico_{time.strftime('%Y%m%d_%H%M%S')}.pt")
    datos = torch.load(RUTA_DQN, map_location="cpu", weights_only=False) if RUTA_DQN.exists() else {}
    datos.update({"estado": red_estado, "entrenamiento_continuo": info})
    torch.save(datos, RUTA_DQN)


def bucle(rondas=None):
    import pandas as pd
    torch.set_num_threads(2)
    red, _ = cargar_politica(RUTA_DQN)
    if red is None:
        sys.exit("No hay DQN entrenada: primero python modelos/entrenar_dqn.py")
    log("evaluando al campeón actual en los 8 escenarios fijos de estrés…")
    prueba = []
    eval_campeon = evaluar(red, recolectar=prueba)
    if not PRUEBA_ESTRES.exists():
        pd.DataFrame(prueba).to_csv(PRUEBA_ESTRES, index=False)
    log(f"campeón: {resumen_eval(eval_campeon)}")
    fijo = None
    try:
        fijo = [f["demora_veh_h"] for f in evaluar_fijo()]
        log(f"referencia semáforo fijo: demora media {np.mean(fijo):.1f} veh·h")
    except Exception as error:     # la referencia es informativa
        log(f"(sin referencia de semáforo fijo: {error})")
    estado_campeon = {k: v.clone() for k, v in red.state_dict().items()}
    ag = Retador(estado_campeon)
    previas = [json.loads(l) for l in REGISTRO.read_text(encoding="utf-8").splitlines() if l.strip()] if REGISTRO.exists() else []
    ronda, sin_mejora = (previas[-1]["ronda"] if previas else 0), 0      # la numeración continúa entre reinicios
    tope = None if rondas is None else ronda + rondas
    while tope is None or ronda < tope:
        ronda += 1
        t0 = time.time()
        filas_estres, episodios = [], []
        for k in range(EPISODIOS_POR_RONDA):
            ep = episodio_entrenamiento(ag, 20000 + ronda * 100 + k, filas_estres, ronda, k)
            episodios.append(ep)
            log(f"ronda {ronda} ep {k + 1}/{EPISODIOS_POR_RONDA}: hora {ep['hora']} acc {ep['accidente']} "
                f"demanda ×{ep['escala']} demora {ep['demora_veh_h']} · oráculo corrigió {ep['corregidas']}/{ep['auditadas']}")
        anterior = pd.read_csv(ESTRES) if ESTRES.exists() else None
        nuevas = pd.DataFrame(filas_estres)
        (pd.concat([anterior, nuevas]) if anterior is not None else nuevas).to_csv(ESTRES, index=False)

        ag.q.eval()
        eval_retador = evaluar(ag.q)
        ag.q.train()
        dc = np.array([f["demora_veh_h"] for f in eval_campeon])
        dr = np.array([f["demora_veh_h"] for f in eval_retador])
        mejora = 100 * (dc.mean() - dr.mean()) / dc.mean()
        gana = int((dr < dc).sum())
        promovido = mejora >= MEJORA_MINIMA and gana > len(dc) / 2
        auditadas = sum(e["auditadas"] for e in episodios)
        corregidas = sum(e["corregidas"] for e in episodios)
        fila = {"ronda": ronda, "fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "minutos": round((time.time() - t0) / 60, 1),
                "episodios": episodios, "auditadas": auditadas, "corregidas": corregidas,
                "tasa_correccion_%": round(100 * corregidas / max(1, auditadas), 1),
                "campeon": resumen_eval(eval_campeon), "retador": resumen_eval(eval_retador),
                "mejora_demora_%": round(float(mejora), 2), "gana_escenarios": f"{gana}/{len(dc)}",
                "promovido": bool(promovido),
                "fijo_demora": round(float(np.mean(fijo)), 2) if fijo else None}
        if promovido:
            estado_campeon = {k: v.clone() for k, v in ag.q.state_dict().items()}
            eval_campeon = eval_retador
            guardar_campeon(estado_campeon, {"ronda": ronda, "fecha": fila["fecha"], "evaluacion": fila["retador"]})
            sin_mejora = 0
            log(f"ronda {ronda}: RETADOR PROMOVIDO (demora −{mejora:.2f} %, gana {gana}/{len(dc)}) → nuevo campeón guardado")
        else:
            sin_mejora += 1
            log(f"ronda {ronda}: el campeón se mantiene (retador {mejora:+.2f} %, gana {gana}/{len(dc)})")
            if sin_mejora >= RONDAS_SIN_MEJORA_REINICIO:
                correcciones = ag.correcciones
                ag = Retador(estado_campeon)
                ag.correcciones = correcciones          # lo aprendido del oráculo se conserva
                sin_mejora = 0
                log("el retador vuelve a partir del campeón (conserva las correcciones del oráculo)")
        if ronda % 2 == 0:
            try:
                log("ajustando la T-GCN con los datos de estrés…")
                fila["tgcn"] = ajustar_tgcn()
                log(f"T-GCN: {fila['tgcn']}")
                if fila["tgcn"] and fila["tgcn"].get("promovido"):
                    # la DQN usa la predicción de la T-GCN: el campeón se vuelve a medir con la T-GCN nueva
                    from dqn import RedQ
                    red_c = RedQ()
                    red_c.load_state_dict(estado_campeon)
                    red_c.eval()
                    nueva = evaluar(red_c)
                    fila["campeon_con_tgcn_nueva"] = resumen_eval(nueva)
                    antes = np.mean([f["demora_veh_h"] for f in eval_campeon])
                    despues = np.mean([f["demora_veh_h"] for f in nueva])
                    if despues > antes * 1.005:
                        # verificación de extremo a extremo: lo que importa es el sistema completo, no cada red sola
                        respaldo = sorted(HISTORIAL.glob("tgcn_bocagrande_2*.pt"))[-1]
                        shutil.copy2(RUTA_TGCN, HISTORIAL / f"tgcn_bocagrande_RECHAZADA_{time.strftime('%Y%m%d_%H%M%S')}.pt")
                        shutil.copy2(respaldo, RUTA_TGCN)
                        fila["tgcn"]["revertido"] = f"la DQN campeona empeoró {100 * (despues / antes - 1):+.2f} % de demora"
                        log(f"T-GCN REVERTIDA: con ella la DQN campeona empeora ({antes:.2f} → {despues:.2f} veh·h)")
                    else:
                        eval_campeon = nueva
                        log(f"campeón re-evaluado con la T-GCN nueva: {fila['campeon_con_tgcn_nueva']}")
            except Exception as error:
                fila["tgcn"] = {"error": str(error)[:200]}
                log(f"T-GCN: error en el ajuste ({error})")
        with REGISTRO.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")


def evaluar_fijo():
    filas = []
    for i, (hora, acc, escala) in enumerate(ESCENARIOS_PRUEBA):
        env = E.EntornoBocagrande(semilla=9300 + i, hora=hora, accidente=acc, duracion=DURACION, escala=escala)
        obs, fin = env.reset(), False
        while not fin:
            obs, _, fin, _ = env.step(E.control_fijo(obs))
        filas.append(env.metricas())
        env.cerrar()
    return filas


def resumen():
    if not REGISTRO.exists():
        print("Todavía no hay rondas registradas.")
        return
    filas = [json.loads(l) for l in REGISTRO.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"{'ronda':>5} {'fecha':>19} {'campeón':>9} {'retador':>9} {'mejora':>8} {'gana':>5} {'corrige':>8} promovido  T-GCN")
    for f in filas:
        t = f.get("tgcn") or {}
        tg = (f"MAE {t.get('mae_estres_campeon')}→{t.get('mae_estres_retador')} {'✔' if t.get('promovido') else ''}"
              if "mae_estres_campeon" in t else "")
        print(f"{f['ronda']:>5} {f['fecha']:>19} {f['campeon']['demora_veh_h']:>9} {f['retador']['demora_veh_h']:>9} "
              f"{f['mejora_demora_%']:>7}% {f['gana_escenarios']:>5} {f['tasa_correccion_%']:>7}% "
              f"{'SÍ' if f['promovido'] else 'no':>9}  {tg}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        x = [f["ronda"] for f in filas]
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        ax[0].plot(x, [f["campeon"]["demora_veh_h"] for f in filas], "-o", label="campeón", color="#1f6feb")
        ax[0].plot(x, [f["retador"]["demora_veh_h"] for f in filas], "-o", label="retador", color="#f0b429", alpha=.7)
        if filas[0].get("fijo_demora"):
            ax[0].axhline(filas[0]["fijo_demora"], ls="--", color="#9aa5b1", label="semáforo fijo")
        ax[0].set_title("Demora media en 8 escenarios de estrés (veh·h)"); ax[0].set_xlabel("ronda"); ax[0].legend(frameon=False)
        ax[1].plot(x, [f["tasa_correccion_%"] for f in filas], "-o", color="#d1242f")
        ax[1].set_title("Decisiones que el oráculo tuvo que corregir (%)"); ax[1].set_xlabel("ronda")
        for a in ax:
            a.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(SALIDA / "entrenamiento_continuo.png", dpi=130)
        print(f"gráfica: {SALIDA / 'entrenamiento_continuo.png'}")
    except Exception as error:
        print(f"(sin gráfica: {error})")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Entrenamiento continuo bajo presión (campeón / retador)")
    p.add_argument("--rondas", type=int, default=None)
    p.add_argument("--resumen", action="store_true")
    a = p.parse_args()
    if a.resumen:
        resumen()
    else:
        try:
            bucle(a.rondas)
        except KeyboardInterrupt:
            log("detenido: el último campeón guardado es el que usan los agentes")
