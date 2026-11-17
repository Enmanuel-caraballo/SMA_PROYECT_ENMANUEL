"""
SMA Movilidad y Seguridad CTG - Prepara los datos 3D de Bocagrande desde OpenStreetMap
====================================================================================
Descarga (Overpass API) las vías, edificios, semáforos y cruces peatonales de Bocagrande y genera:

  maqueta_virtual/bocagrande/bocagrande.json   red vial (grafo), edificios, andenes, semáforos (en metros)
  maqueta_virtual/cruces.json                  los cruces semaforizados que usan el simulador y los agentes

Solo hace falta correrlo si se quieren refrescar los datos (el resultado ya está en el repositorio):
  python maqueta_virtual/preparar_bocagrande.py               (descarga de Overpass)
  python maqueta_virtual/preparar_bocagrande.py --osm x.json  (usa un archivo ya descargado)
"""

import argparse
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
SALIDA_MAPA = CARPETA / "bocagrande" / "bocagrande.json"
SALIDA_CRUCES = CARPETA / "cruces.json"

# Caja de Bocagrande (sur, oeste, norte, este) con algo de El Laguito y Castillogrande alrededor
CAJA = (10.3925, -75.5650, 10.4150, -75.5430)
ORIGEN = (10.4035, -75.5550)   # centro aproximado de Bocagrande -> (0, 0) en metros

SERVIDORES = ["https://overpass-api.de/api/interpreter",
              "https://overpass.private.coffee/api/interpreter",
              "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]

VIAS_CARRO = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
VIAS_PEATON = {"footway", "pedestrian", "path", "steps", "living_street"}
RANGO = {"trunk": 6, "primary": 5, "secondary": 4, "tertiary": 3, "unclassified": 2, "residential": 2, "living_street": 1}
RADIO_CRUCE = 32        # m: semáforos a menos de esta distancia forman un mismo cruce


def consulta():
    s, o, n, e = CAJA
    caja = f"({s},{o},{n},{e})"
    return f"""[out:json][timeout:180];
(
 node["highway"="traffic_signals"]{caja};
 node["crossing"]{caja};
 way["highway"]{caja};
 way["building"]{caja};
);
out body geom;"""


def descargar():
    datos = urllib.parse.urlencode({"data": consulta()}).encode()
    for intento in range(4):
        for srv in SERVIDORES:
            try:
                req = urllib.request.Request(srv, data=datos, headers={"User-Agent": "sma-proyecto-academico/1.0"})
                with urllib.request.urlopen(req, timeout=200) as r:
                    texto = r.read().decode("utf-8")
                if texto.lstrip().startswith("{"):
                    print(f"datos descargados de {srv}")
                    return json.loads(texto)
            except Exception as ex:
                print(f"falló {srv}: {ex}")
            time.sleep(10 * (intento + 1))
    raise SystemExit("No se pudo descargar de Overpass. Intenta más tarde.")


def a_metros(lat, lon):
    x = (lon - ORIGEN[1]) * 111320 * math.cos(math.radians(ORIGEN[0]))
    z = -(lat - ORIGEN[0]) * 110540          # en Three.js el norte es -z
    return round(x, 1), round(z, 1)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def altura_edificio(tags, osm_id):
    if "height" in tags:
        try:
            return float(tags["height"].split()[0])
        except ValueError:
            pass
    if "building:levels" in tags:
        try:
            return float(tags["building:levels"]) * 3.2
        except ValueError:
            pass
    # Sin dato: altura estimada (Bocagrande mezcla casas y torres); estable por id
    h = int(hashlib.md5(str(osm_id).encode()).hexdigest(), 16) % 100
    return 7 + h * 0.35 if h < 75 else 40 + (h - 75) * 2.4


def procesar(osm):
    elementos = osm["elements"]
    nodos_tags = {e["id"]: e for e in elementos if e["type"] == "node"}

    # ---------- Red vial de carros: grafo dirigido partido en intersecciones ----------
    vias = [e for e in elementos if e["type"] == "way" and e.get("tags", {}).get("highway") in VIAS_CARRO
            and e.get("tags", {}).get("access") not in ("private", "no")]
    uso = defaultdict(int)
    for w in vias:
        for nid in w["nodes"]:
            uso[nid] += 1
    coord = {}
    for w in vias:
        for nid, g in zip(w["nodes"], w["geometry"]):
            coord[nid] = a_metros(g["lat"], g["lon"])

    indice = {}          # id OSM -> índice de nodo del grafo
    nodos = []

    def idx(nid):
        if nid not in indice:
            indice[nid] = len(nodos)
            nodos.append(coord[nid])
        return indice[nid]

    aristas = []
    for w in vias:
        t = w["tags"]
        sentido = t.get("oneway", "no")
        unico = sentido in ("yes", "1", "true", "-1") or t.get("junction") == "roundabout" or t["highway"].endswith("_link") and sentido != "no"
        ids = w["nodes"][::-1] if sentido == "-1" else w["nodes"]
        try:
            carriles = int(t.get("lanes", "0").split(";")[0])
        except ValueError:
            carriles = 0
        if carriles <= 0:
            carriles = 2 if t["highway"] in ("primary", "secondary", "trunk") else (1 if unico else 2)
        tramo = [ids[0]]
        for nid in ids[1:]:
            tramo.append(nid)
            if uso[nid] > 1 or nid == ids[-1]:
                pts = [coord[n] for n in tramo]
                largo = sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
                if largo > 0.5:
                    aristas.append({"a": idx(tramo[0]), "b": idx(tramo[-1]), "p": pts, "nombre": t.get("name", ""),
                                    "tipo": t["highway"].replace("_link", ""), "unico": unico,
                                    "carriles": carriles, "largo": round(largo, 1)})
                tramo = [nid]

    # Recortar a la caja: OSM devuelve vías completas que se salen de Bocagrande
    (x0, z1), (x1, z0) = a_metros(CAJA[0], CAJA[1]), a_metros(CAJA[2], CAJA[3])
    dentro = lambda q: x0 - 30 <= q[0] <= x1 + 30 and z0 - 30 <= q[1] <= z1 + 30
    aristas = [a for a in aristas if all(dentro(q) for q in a["p"])]
    usados = sorted({a["a"] for a in aristas} | {a["b"] for a in aristas})
    nuevo = {v: i for i, v in enumerate(usados)}
    nodos = [nodos[v] for v in usados]
    for a in aristas:
        a["a"], a["b"] = nuevo[a["a"]], nuevo[a["b"]]

    # ---------- Semáforos -> cruces ----------
    senales = [n for n in nodos_tags.values() if n.get("tags", {}).get("highway") == "traffic_signals"]
    grupos = []
    for s in senales:
        p = a_metros(s["lat"], s["lon"])
        for g in grupos:
            if dist(g["centro"], p) < RADIO_CRUCE:
                g["pts"].append(p)
                g["latlon"].append((s["lat"], s["lon"]))
                g["centro"] = (sum(q[0] for q in g["pts"]) / len(g["pts"]), sum(q[1] for q in g["pts"]) / len(g["pts"]))
                break
        else:
            grupos.append({"pts": [p], "centro": p, "latlon": [(s["lat"], s["lon"])]})

    cruces = []
    for g in grupos:
        cx, cz = g["centro"]
        # nodos del grafo que pertenecen al cruce
        cerca = [i for i, q in enumerate(nodos) if dist(q, (cx, cz)) < RADIO_CRUCE]
        if not cerca:
            continue
        nombres = defaultdict(int)
        for a in aristas:
            if (a["a"] in cerca or a["b"] in cerca) and a["nombre"]:
                nombres[a["nombre"]] = max(nombres[a["nombre"]], RANGO.get(a["tipo"], 1) * 10 + a["carriles"])
        orden = sorted(nombres, key=lambda n: -nombres[n])
        if len(orden) < 1:
            continue
        lat = sum(q[0] for q in g["latlon"]) / len(g["latlon"])
        lon = sum(q[1] for q in g["latlon"]) / len(g["latlon"])
        cruces.append({"nodos": cerca, "x": round(cx, 1), "z": round(cz, 1), "lat": round(lat, 6), "lon": round(lon, 6),
                       "via_principal": orden[0], "via_secundaria": orden[1] if len(orden) > 1 else "(paso peatonal)",
                       "semaforos": len(g["pts"]), "solo_peatonal": len(orden) < 2})

    # ---------- Peatones: andenes / senderos y cruces peatonales ----------
    senderos = []
    for e in elementos:
        if e["type"] == "way" and e.get("tags", {}).get("highway") in VIAS_PEATON:
            senderos.append([a_metros(g["lat"], g["lon"]) for g in e["geometry"]])
    pasos = [list(a_metros(n["lat"], n["lon"])) + [1 if n["tags"].get("crossing") == "traffic_signals" else 0]
             for n in nodos_tags.values() if "crossing" in n.get("tags", {})]

    # ---------- Edificios ----------
    edificios = []
    for e in elementos:
        if e["type"] == "way" and "building" in e.get("tags", {}) and len(e.get("geometry", [])) >= 4:
            pts = [a_metros(g["lat"], g["lon"]) for g in e["geometry"]][:-1]
            edificios.append({"p": pts, "h": round(altura_edificio(e["tags"], e["id"]), 1),
                              "n": e["tags"].get("name", "")})
    return nodos, aristas, cruces, senderos, pasos, edificios


SAN_MARTIN = {"Avenida San Martín", "Carrera 2"}   # en OSM el tramo sur de la San Martín se llama "Carrera 2"
REFERENCIAS = {"Calle 15": "frente a la Base Naval"}


def numero_calle(c):
    for n in (c["via_principal"], c["via_secundaria"]):
        m = re.match(r"Calle (\d+)", n)
        if m:
            return int(m.group(1))
    return 99


def clave_orden(c):
    return (numero_calle(c), 0 if c["via_principal"] in SAN_MARTIN else 1)


def abreviar(nombre):
    if nombre == "Carrera 2":
        return "Av. San Martín (Cra. 2)"
    return nombre.replace("Avenida ", "Av. ").replace("Carrera ", "Cra. ")


def escribir_cruces(cruces):
    lista = []
    for c in cruces:
        nombre = f"{abreviar(c['via_principal'])} × {abreviar(c['via_secundaria'])}"
        ref = REFERENCIAS.get(c["via_secundaria"])
        lista.append({"id": c["id"], "nombre": nombre + (f" ({ref})" if ref else ""), "barrio": "Bocagrande",
                      "lat": c["lat"], "lon": c["lon"], "via_principal": c["via_principal"],
                      "via_secundaria": c["via_secundaria"], "fisico": c["id"] == "cruce1"})
    datos = {"_fuente": "Semáforos (highway=traffic_signals) de OpenStreetMap en Bocagrande, agrupados por intersección. "
                        "Generado por preparar_bocagrande.py",
             "cruces": lista}
    SALIDA_CRUCES.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--osm", help="archivo JSON de Overpass ya descargado")
    args = p.parse_args()
    osm = json.loads(Path(args.osm).read_text(encoding="utf-8")) if args.osm else descargar()
    nodos, aristas, cruces, senderos, pasos, edificios = procesar(osm)

    # Orden: por número de calle y luego San Martín antes que Carrera 3.
    # cruce1 = Av. San Martín x Calle 6 (punto crítico citado por la DATT) = cruce físico del ESP32.
    cruces.sort(key=clave_orden)
    for i, c in enumerate(cruces, 1):
        c["id"] = f"cruce{i}"
    escribir_cruces(cruces)
    mapa = {"_fuente": "© colaboradores de OpenStreetMap (ODbL). Generado por preparar_bocagrande.py",
            "origen": {"lat": ORIGEN[0], "lon": ORIGEN[1]},
            "nodos": nodos, "aristas": aristas, "senderos": senderos, "pasos": pasos, "edificios": edificios,
            "cruces": [{"id": c["id"], "x": c["x"], "z": c["z"], "nodos": c["nodos"],
                        "via_principal": c["via_principal"]} for c in cruces]}
    SALIDA_MAPA.parent.mkdir(exist_ok=True)
    SALIDA_MAPA.write_text(json.dumps(mapa, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{SALIDA_MAPA.name}: {len(nodos)} nodos, {len(aristas)} tramos, {len(edificios)} edificios, "
          f"{len(senderos)} senderos, {len(pasos)} pasos peatonales, {len(cruces)} cruces semaforizados")
    for c in cruces:
        print(f"  {c['id']}: {c['via_principal']} x {c['via_secundaria']}  ({c['lat']}, {c['lon']}) "
              f"semáforos OSM={c['semaforos']}")
    return cruces


if __name__ == "__main__":
    main()
