"""
SMA Movilidad y Seguridad CTG - DATOS REALES: descarga, limpieza y depuración
=============================================================================
Fuentes (datos abiertos, gratuitos):
  1) Accidentes de Tránsito Ocurridos en Cartagena de Indias (Alcaldía de Cartagena / DATT)
     datos.gov.co, conjunto 5z62-bn99  -> eventos reales (dónde y cuándo ocurren en Bocagrande)
  2) Registro de Semaforización de Cartagena (Alcaldía) - datos.gov.co, conjunto mybb-py6y
     -> valida que nuestros 5 cruces son semáforos oficiales
  3) Velocidad e intensidad vehicular en Medellín (SIMM, MEData)
     -> flujo vehicular real por hora para entrenar la red neuronal de predicción y sacar el perfil de demanda

Uso:
    python modelos/datos_reales.py            (descarga lo que falte y limpia)
    python modelos/datos_reales.py --forzar   (vuelve a descargar)

Salidas en modelos/datos/ (los crudos quedan en modelos/datos/crudos/, que Git ignora):
    accidentes_bocagrande.csv, accidentes_resumen.json, semaforos_bocagrande.csv,
    simm_intensidad.csv.gz (matriz hora x estación), simm_estaciones.csv, perfil_demanda.json,
    reporte_limpieza.md
"""

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "modelos" / "datos"
CRUDOS = DATOS / "crudos"
BOCAGRANDE = RAIZ / "maqueta_virtual" / "bocagrande" / "bocagrande.json"

URL_ACCIDENTES = "https://www.datos.gov.co/resource/5z62-bn99.json"
URL_SEMAFOROS = "https://www.datos.gov.co/resource/mybb-py6y.json"
URL_SIMM = ("https://medata.app.medellin.gov.co/sites/default/files/distribution/1-023-25-000296/"
            "velocidad_e_intensidad_vehicular_en_medellin.csv")

# Reglas de limpieza (umbrales con justificación física)
CARTAGENA = {"lat": (10.25, 10.55), "lon": (-75.65, -75.35)}   # caja del distrito urbano
DIST_MAX_VIA = 60          # m: accidente a más de esto de una vía del gemelo -> fuera del sector
DIST_CRUCE = 80            # m: si está a menos de esto de un cruce semaforizado se le asigna ese cruce
INTENSIDAD_MAX = 2200      # veh/h por carril: por encima es físicamente imposible (capacidad ~1800-2000)
VELOCIDAD_MAX = 130        # km/h en vías urbanas de Medellín
HUECO_MAX = 2              # h: huecos más largos no se interpolan
COBERTURA_MIN = 0.80       # fracción mínima de horas válidas para conservar una estación

reporte = []


def nota(texto):
    print(texto)
    reporte.append(texto)


# ---------------------------------------------------------------- descarga
def bajar(url, destino, forzar=False):
    if destino.exists() and not forzar:
        return
    print(f"descargando {url} ...")
    pedido = urllib.request.Request(url, headers={"User-Agent": "sma-bocagrande-academico"})
    with urllib.request.urlopen(pedido, timeout=600) as r:
        destino.write_bytes(r.read())


def bajar_soda(url, destino, forzar=False, pagina=50000):
    """API SODA de datos.gov.co, paginada."""
    if destino.exists() and not forzar:
        return
    filas, desde = [], 0
    while True:
        pedido = urllib.request.Request(f"{url}?$limit={pagina}&$offset={desde}&$order=:id",
                                        headers={"User-Agent": "sma-bocagrande-academico"})
        for intento in range(5):          # datos.gov.co a veces corta la conexión: reintentar
            try:
                with urllib.request.urlopen(pedido, timeout=300) as r:
                    lote = json.loads(r.read().decode("utf-8"))
                break
            except OSError as error:
                print(f"  reintento {intento + 1} ({error})")
                time.sleep(3 * (intento + 1))
        else:
            raise RuntimeError(f"no se pudo descargar {url}")
        filas += lote
        if len(lote) < pagina:
            break
        desde += pagina
    destino.write_text(json.dumps(filas, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- geometría del gemelo
def a_metros(lat, lon, origen):
    x = (lon - origen["lon"]) * 111320 * math.cos(math.radians(origen["lat"]))
    z = -(lat - origen["lat"]) * 110540
    return x, z


def dist_punto_segmento(px, pz, ax, az, bx, bz):
    dx, dz = bx - ax, bz - az
    l2 = dx * dx + dz * dz
    t = 0 if l2 == 0 else max(0, min(1, ((px - ax) * dx + (pz - az) * dz) / l2))
    return math.hypot(px - (ax + t * dx), pz - (az + t * dz))


# ---------------------------------------------------------------- 1) accidentes
def limpiar_accidentes(crudo):
    geo = json.loads(BOCAGRANDE.read_text(encoding="utf-8"))
    origen = geo["origen"]
    d = pd.DataFrame(json.loads(crudo.read_text(encoding="utf-8")))
    nota(f"\n## Accidentes de tránsito de Cartagena (datos.gov.co 5z62-bn99)\n\n- Filas descargadas: {len(d)}")

    n = len(d)
    d = d.drop_duplicates(subset="ipat")
    nota(f"- Duplicados por número de IPAT eliminados: {n - len(d)}")

    # fecha + hora: la hora viene como 1899-12-31Thh:mm (fecha "cero" de Excel)
    fecha = pd.to_datetime(d["fecha"], errors="coerce")
    hora = pd.to_datetime(d["hora"], errors="coerce")
    d["momento"] = fecha.dt.normalize() + pd.to_timedelta(hora.dt.hour.fillna(-1), unit="h") \
        + pd.to_timedelta(hora.dt.minute.fillna(0), unit="m")
    malas = fecha.isna() | hora.isna()
    nota(f"- Sin fecha u hora válida (descartadas): {int(malas.sum())}")
    d = d[~malas].copy()

    d["lat"] = pd.to_numeric(d.get("latitud"), errors="coerce")
    d["lon"] = pd.to_numeric(d.get("longitud"), errors="coerce")
    fuera = d.lat.isna() | d.lon.isna() | ~d.lat.between(*CARTAGENA["lat"]) | ~d.lon.between(*CARTAGENA["lon"])
    nota(f"- Coordenadas nulas o fuera de Cartagena (descartadas): {int(fuera.sum())}")
    d = d[~fuera].copy()

    for c in ["barrio", "lugar", "via1", "via2", "gravedad", "clase_accidente"]:
        d[c] = d[c].fillna("").astype(str).str.strip().str.upper().replace({"0": ""})
    for c in ["muertos", "heridos"]:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype(int)

    # filtrar Bocagrande por COORDENADAS: distancia a las vías del gemelo (no por la etiqueta de barrio)
    xs, zs = zip(*[a_metros(la, lo, origen) for la, lo in zip(d.lat, d.lon)])
    d["x"], d["z"] = np.round(xs, 1), np.round(zs, 1)
    caja = np.array(geo["nodos"])
    xmin, zmin = caja.min(axis=0) - DIST_MAX_VIA
    xmax, zmax = caja.max(axis=0) + DIST_MAX_VIA
    cerca = d.x.between(xmin, xmax) & d.z.between(zmin, zmax)
    etiqueta = (d.barrio == "BOCAGRANDE").sum()
    d = d[cerca].copy()

    segmentos = []
    for i, a in enumerate(geo["aristas"]):
        for p, q in zip(a["p"][:-1], a["p"][1:]):
            segmentos.append((i, a["nombre"], p[0], p[1], q[0], q[1]))
    via, nombre_via, distancia = [], [], []
    for px, pz in zip(d.x, d.z):
        mejor = min(segmentos, key=lambda s: dist_punto_segmento(px, pz, *s[2:]))
        via.append(mejor[0])
        nombre_via.append(mejor[1])
        distancia.append(dist_punto_segmento(px, pz, *mejor[2:]))
    d["arista"], d["via_osm"], d["dist_via"] = via, nombre_via, np.round(distancia, 1)
    lejos = d.dist_via > DIST_MAX_VIA
    d = d[~lejos].copy()

    cruces = geo["cruces"]
    def cruce_cercano(px, pz):
        c = min(cruces, key=lambda c: math.hypot(px - c["x"], pz - c["z"]))
        return c["id"] if math.hypot(px - c["x"], pz - c["z"]) <= DIST_CRUCE else ""
    d["cruce"] = [cruce_cercano(px, pz) for px, pz in zip(d.x, d.z)]
    nota(f"- Con etiqueta barrio=BOCAGRANDE: {etiqueta}; ubicados por coordenadas sobre las vías del gemelo "
         f"(< {DIST_MAX_VIA} m): **{len(d)}** (descartados por lejanía a la vía: {int(lejos.sum())})")
    nota(f"- Asignados a un cruce semaforizado (< {DIST_CRUCE} m): "
         + ", ".join(f"{c}={int((d.cruce == c).sum())}" for c in sorted(set(d.cruce) - {''}))
         + f"; en tramos entre cruces: {int((d.cruce == '').sum())}")

    d["hora_dia"] = d.momento.dt.hour
    d["dia_semana"] = d.momento.dt.dayofweek
    d["anio"] = d.momento.dt.year
    columnas = ["ipat", "momento", "anio", "hora_dia", "dia_semana", "gravedad", "clase_accidente", "choque_con",
                "barrio", "via1", "via2", "via_osm", "arista", "cruce", "lat", "lon", "x", "z", "dist_via",
                "muertos", "heridos"]
    d = d[columnas].sort_values("momento")
    d.to_csv(DATOS / "accidentes_bocagrande.csv", index=False, encoding="utf-8")

    # probabilidades empíricas: por hora del día, por día de la semana y por lugar (para generar eventos realistas)
    por_hora = d.hora_dia.value_counts().reindex(range(24), fill_value=0)
    por_dia = d.dia_semana.value_counts().reindex(range(7), fill_value=0)
    resumen = {
        "fuente": "datos.gov.co 5z62-bn99 (Alcaldía de Cartagena), limpiado por modelos/datos_reales.py",
        "total": int(len(d)),
        "periodo": [str(d.momento.min().date()), str(d.momento.max().date())],
        "por_anio": {int(k): int(v) for k, v in d.anio.value_counts().sort_index().items()},
        "prob_hora": [round(v / max(1, por_hora.sum()), 4) for v in por_hora],
        "prob_dia_semana": [round(v / max(1, por_dia.sum()), 4) for v in por_dia],
        "por_cruce": {c or "tramo": int(v) for c, v in d.cruce.value_counts().items()},
        "por_arista": {int(k): int(v) for k, v in d.arista.value_counts().items()},
        "gravedad": {k: int(v) for k, v in d.gravedad.value_counts().items()},
        "clase": {k: int(v) for k, v in d.clase_accidente.value_counts().items()},
    }
    (DATOS / "accidentes_resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    hora_pico = int(por_hora.idxmax())
    nota(f"- Periodo {resumen['periodo'][0]} a {resumen['periodo'][1]}; hora con más accidentes: {hora_pico}:00; "
         f"gravedad: {resumen['gravedad']}")
    return d


# ---------------------------------------------------------------- 2) semáforos oficiales
def validar_semaforos(crudo):
    d = pd.DataFrame(json.loads(crudo.read_text(encoding="utf-8")))
    nota(f"\n## Registro de semaforización de Cartagena (datos.gov.co mybb-py6y)\n\n- Semáforos registrados: {len(d)}")
    nuestros = {"cruce1": ("CARRERA 2", "CALLE 6"), "cruce2": ("CARRERA 3", "CALLE 6"),
                "cruce3": ("CARRERA 2", "CALLE 10"), "cruce4": ("CARRERA 3", "CALLE 10"),
                "cruce5": ("CARRERA 2", "CALLE 15")}
    ubic = d["ubicaci_n"].fillna("").str.upper().str.replace(r"\s+", " ", regex=True)
    filas = []
    for cruce, (cra, calle) in nuestros.items():
        m = d[ubic.str.contains(rf"\b{cra}\b") & ubic.str.contains(rf"\b{calle}\b")]
        oficial = m.iloc[0] if len(m) else None
        filas.append({"cruce": cruce, "carrera": cra, "calle": calle,
                      "codigo_oficial": oficial["codigo_externo"] if oficial is not None else "",
                      "nombre_oficial": oficial["cruces"] if oficial is not None else "",
                      "instalado": str(oficial["fecha_de_instalaci_n"])[:10] if oficial is not None else ""})
        nota(f"- {cruce} ({cra} x {calle}): " + (f"OFICIAL código {oficial['codigo_externo']} '{oficial['cruces']}'"
                                                 if oficial is not None else "no aparece en el registro"))
    pd.DataFrame(filas).to_csv(DATOS / "semaforos_bocagrande.csv", index=False, encoding="utf-8")


# ---------------------------------------------------------------- 3) flujo vehicular SIMM Medellín
def fecha_simm(texto):
    """Dos formatos mezclados: 'd/m/aaaa h:mm' y número de serie de Excel con coma decimal ('44049,70833')."""
    s = str(texto).strip()
    if "/" in s:
        return pd.to_datetime(s, format="%d/%m/%Y %H:%M", errors="coerce")
    try:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(float(s.replace(",", ".")), unit="D")
    except ValueError:
        return pd.NaT


def limpiar_simm(crudo):
    d = pd.read_csv(crudo, low_memory=False)
    nota(f"\n## Velocidad e intensidad vehicular de Medellín (SIMM)\n\n- Filas descargadas: {len(d)} "
         f"({d.carril.nunique()} carriles, {d.corredor.nunique()} corredores)")

    serie = d.fecha_trafico.astype(str).str.contains(",") | ~d.fecha_trafico.astype(str).str.contains("/")
    d["ts"] = d.fecha_trafico.map(fecha_simm).dt.round("h")
    nota(f"- Fechas en formato de serie de Excel convertidas: {int(serie.sum())}; ilegibles descartadas: "
         f"{int(d.ts.isna().sum())}")
    d = d[d.ts.notna()].copy()

    reglas = [
        ("marcadas como dato falso/no validado (identificador F)", d.identificador_f_v.astype(str).str.upper() != "V"),
        (f"intensidad negativa o > {INTENSIDAD_MAX} veh/h por carril", ~d.intensidad.between(0, INTENSIDAD_MAX)),
        (f"velocidad > {VELOCIDAD_MAX} km/h", d.velocidad_km_h > VELOCIDAD_MAX),
        ("velocidad 0 con vehículos pasando (sensor inconsistente)", (d.velocidad_km_h == 0) & (d.intensidad > 0)),
        ("ocupación fuera de 0-100 %", ~d.ocupacion.between(0, 100)),
        # las cámaras de fotodetección (FT) cuentan pero no clasifican: categorías en 0 NO es error;
        # sí lo es que las categorías sumen MÁS que el total
        ("categorías que suman más que la intensidad",
         d[["categoria_1", "categoria_2", "categoria_3"]].sum(axis=1) > d.intensidad + 2),
    ]
    for texto, mala in reglas:
        nota(f"- {texto}: {int(mala.sum())}")
        d = d[~mala.reindex(d.index, fill_value=False)].copy()

    n = len(d)
    d = d.groupby(["carril", "ts"], as_index=False).agg(
        intensidad=("intensidad", "mean"), velocidad=("velocidad_km_h", "mean"), ocupacion=("ocupacion", "mean"),
        corredor=("corredor", "first"), sentido=("sentido", "first"), operacion=("operacion", "first"),
        longitud=("longitud", "first"), latitud=("latitud", "first"))
    nota(f"- Registros duplicados carril-hora promediados: {n - len(d)}")

    # atípicos por carril y hora del día (IQR 3x): se vuelven hueco y luego se interpolan si el hueco es corto
    d["h"] = d.ts.dt.hour
    g = d.groupby(["carril", "h"]).intensidad
    q1, q3 = g.transform(lambda s: s.quantile(0.25)), g.transform(lambda s: s.quantile(0.75))
    atipico = (d.intensidad > q3 + 3 * (q3 - q1)) | (d.intensidad < q1 - 3 * (q3 - q1))
    nota(f"- Atípicos por carril y hora del día (fuera de 3 x IQR) marcados como hueco: {int(atipico.sum())}")
    d.loc[atipico, "intensidad"] = np.nan

    # estación = punto de medición (mismas coordenadas y sentido): se suman sus carriles
    d["estacion"] = d.corredor.astype(str) + " | " + d.sentido.astype(str) + " | " + d.longitud.astype(str)
    carriles_por_est = d.groupby("estacion").carril.nunique()
    est = d.groupby(["estacion", "ts"]).agg(intensidad=("intensidad", "sum"), n=("carril", "nunique"),
                                            hueco=("intensidad", lambda s: s.isna().any()))
    # si falta algún carril (o tiene un atípico) esa hora de la estación es hueco: sumar solo una parte subestima
    completo = (est.n == carriles_por_est.reindex(est.index.get_level_values(0)).values) & ~est.hueco
    est.loc[~completo, "intensidad"] = np.nan
    matriz = est.intensidad.unstack(0)
    rango = pd.date_range(matriz.index.min(), matriz.index.max(), freq="h")
    matriz = matriz.reindex(rango)
    nota(f"- Estaciones (puntos de medición, carriles sumados): {matriz.shape[1]}; periodo "
         f"{rango[0]} a {rango[-1]} ({len(rango)} horas)")
    # la mayoría de carriles solo reporta UN mes: se toma la ventana mensual con más estaciones completas
    meses = matriz.index.to_period("M")
    utiles = {m: int((matriz[meses == m].notna().mean() >= COBERTURA_MIN).sum()) for m in meses.unique()}
    mejor = max(utiles, key=utiles.get)
    matriz = matriz[meses == mejor]
    nota(f"- Estaciones con >= {int(COBERTURA_MIN * 100)} % de cobertura por mes: "
         + ", ".join(f"{m}={v}" for m, v in utiles.items()) + f" -> se usa **{mejor}** ({len(matriz)} horas)")

    cobertura = matriz.notna().mean()
    matriz = matriz.loc[:, cobertura >= COBERTURA_MIN]
    nota(f"- Estaciones con < {int(COBERTURA_MIN * 100)} % de horas válidas descartadas: "
         f"{int((cobertura < COBERTURA_MIN).sum())}; quedan **{matriz.shape[1]}**")
    huecos = int(matriz.isna().sum().sum())
    matriz = matriz.interpolate(limit=HUECO_MAX, limit_direction="both")
    restantes = int(matriz.isna().sum().sum())
    nota(f"- Huecos: {huecos} celdas; interpolados (≤ {HUECO_MAX} h): {huecos - restantes}; "
         f"los largos se llenan con el promedio de esa estación a esa hora y día de la semana: {restantes}")
    if restantes:
        clave = [matriz.index.dayofweek, matriz.index.hour]
        promedio = matriz.groupby(clave).transform("mean")
        matriz = matriz.fillna(promedio).fillna(matriz.mean())
    matriz = matriz.round(1)
    matriz.index.name = "ts"
    matriz.to_csv(DATOS / "simm_intensidad.csv.gz", encoding="utf-8")

    # metadatos de estaciones (coordenadas planas en metros, MAGNA-SIRGAS Medellín) para el grafo de la T-GCN
    meta = d.drop_duplicates("estacion").set_index("estacion").loc[matriz.columns]
    meta = pd.DataFrame({
        "estacion": matriz.columns,
        "corredor": meta.corredor.values, "sentido": meta.sentido.values, "operacion": meta.operacion.values,
        "x": pd.to_numeric(meta.longitud.astype(str).str.replace(",", "."), errors="coerce").values,
        "y": pd.to_numeric(meta.latitud.astype(str).str.replace(",", "."), errors="coerce").values,
        "carriles": carriles_por_est.loc[matriz.columns].values,
    })
    meta.to_csv(DATOS / "simm_estaciones.csv", index=False, encoding="utf-8")

    # perfil de demanda: forma horaria normalizada (pico = 1), día hábil y fin de semana
    total = matriz.sum(axis=1)
    habil = total[total.index.dayofweek < 5].groupby(total[total.index.dayofweek < 5].index.hour).mean()
    finde = total[total.index.dayofweek >= 5].groupby(total[total.index.dayofweek >= 5].index.hour).mean()
    perfil = {
        "fuente": "SIMM Medellín (MEData), intensidad total horaria limpiada; forma normalizada al pico = 1",
        "habil": [round(v / habil.max(), 3) for v in habil.reindex(range(24)).fillna(0)],
        "fin_de_semana": [round(v / finde.max(), 3) for v in finde.reindex(range(24)).fillna(0)],
        "hora_pico_habil": int(habil.idxmax()),
    }
    (DATOS / "perfil_demanda.json").write_text(json.dumps(perfil, ensure_ascii=False, indent=1), encoding="utf-8")
    nota(f"- Perfil de demanda: hora pico en día hábil = {perfil['hora_pico_habil']}:00; "
         f"madrugada (3:00) = {perfil['habil'][3]:.2f} del pico")
    return matriz


def main():
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--forzar", action="store_true", help="vuelve a descargar los datos")
    args = p.parse_args()
    CRUDOS.mkdir(parents=True, exist_ok=True)

    bajar_soda(URL_ACCIDENTES, CRUDOS / "accidentes_cartagena.json", args.forzar)
    bajar_soda(URL_SEMAFOROS, CRUDOS / "semaforos_cartagena.json", args.forzar)
    bajar(URL_SIMM, CRUDOS / "simm_medellin.csv", args.forzar)

    reporte.append("# Reporte de limpieza y depuración de datos reales\n\n"
                   "Generado por `python modelos/datos_reales.py`. Cada regla indica cuántas filas eliminó o corrigió.")
    limpiar_accidentes(CRUDOS / "accidentes_cartagena.json")
    validar_semaforos(CRUDOS / "semaforos_cartagena.json")
    limpiar_simm(CRUDOS / "simm_medellin.csv")
    (DATOS / "reporte_limpieza.md").write_text("\n".join(reporte) + "\n", encoding="utf-8")
    print(f"\nlisto: {DATOS / 'reporte_limpieza.md'}")


if __name__ == "__main__":
    main()
