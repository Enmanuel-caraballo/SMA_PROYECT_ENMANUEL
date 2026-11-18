"""
SMA Movilidad y Seguridad CTG - RED DE BOCAGRANDE PARA SUMO
==========================================================
SUMO (Eclipse SUMO, DLR) es un simulador de tráfico microscópico de código abierto muy usado en investigación de
control semafórico con aprendizaje por refuerzo (p. ej. sumo-rl, RESCO). Aquí se genera la red de Bocagrande con
EXACTAMENTE el mismo grafo dirigido que usan el gemelo 3D y el modelo de rutas (agentes/rutas.py · RedVial):
mismos tramos, carriles por sentido, velocidades, cruces semaforizados (5, un programa por cruce) y tramos de
"reserva" para el contraflujo (cerrados al iniciar).

Uso:  python modelos/sumo_red.py      -> modelos/sumo/bocagrande.net.xml + modelos/sumo/mapa.json
"""

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "agentes"))
from rutas import RedVial  # noqa: E402

CARPETA = RAIZ / "modelos" / "sumo"


def binario(nombre):
    try:
        import sumo
        return str(Path(sumo.SUMO_HOME) / "bin" / f"{nombre}.exe" if os.name == "nt" else Path(sumo.SUMO_HOME) / "bin" / nombre)
    except ImportError:
        return nombre


def generar():
    R = RedVial()
    CARPETA.mkdir(parents=True, exist_ok=True)
    usados = {n for e in R.E for n in (e["desde"], e["hasta"])}
    tl = {n: c for c, d in R.cruces.items() for n in d["nodos"]}
    xy = lambda p: f"{p[0]:.2f},{-p[1]:.2f}"                      # en SUMO el norte es +y; en el gemelo es -z
    nodos = ['<nodes>']
    for n in sorted(usados):
        x, z = R.nodos[n]
        if n in tl:
            nodos.append(f'  <node id="n{n}" x="{x:.2f}" y="{-z:.2f}" type="traffic_light" tl="{tl[n]}"/>')
        else:
            nodos.append(f'  <node id="n{n}" x="{x:.2f}" y="{-z:.2f}" type="priority"/>')
    nodos.append('</nodes>')
    aristas = ['<edges>']
    for e in R.E:
        forma = " ".join(xy(q) for q in e["p"])
        aristas.append(f'  <edge id="e{e["id"]}" from="n{e["desde"]}" to="n{e["hasta"]}" numLanes="{e["carriles"]}" '
                       f'speed="{e["vmax"]:.1f}" shape="{forma}"' + (f' name="{e["nombre"]}"' if e["nombre"] else "") + '/>')
    aristas.append('</edges>')
    (CARPETA / "bocagrande.nod.xml").write_text("\n".join(nodos), encoding="utf-8")
    (CARPETA / "bocagrande.edg.xml").write_text("\n".join(aristas), encoding="utf-8")
    r = subprocess.run([binario("netconvert"), "-n", str(CARPETA / "bocagrande.nod.xml"), "-e", str(CARPETA / "bocagrande.edg.xml"),
                        "-o", str(CARPETA / "bocagrande.net.xml"), "--no-turnarounds", "true",
                        "--offset.disable-normalization", "true", "--tls.default-type", "static",
                        "--junctions.join", "false", "--no-warnings", "true"], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr)
        raise SystemExit("netconvert falló")
    mapa = {
        "aristas": [{"id": f"e{e['id']}", "base": e["base"], "nombre": e["nombre"], "largo": round(e["largo"], 1),
                     "carriles": e["carriles"], "reserva": e["reserva"], "control": e["control"],
                     "desde": e["desde"], "hasta": e["hasta"]} for e in R.E],
        "cruces": {c: {"nodos": d["nodos"], "via_principal": d["via_principal"], "x": d["x"], "z": d["z"]}
                   for c, d in R.cruces.items()},
        "zonas": R.zonas,
        "geo": {k: (v["id"] if isinstance(v, dict) else [x["id"] for x in v if x] if isinstance(v, list) else v)
                for k, v in R.geo.items()},
    }
    (CARPETA / "mapa.json").write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
    print(f"red SUMO: {len(usados)} nodos, {len(R.E)} aristas -> {CARPETA / 'bocagrande.net.xml'}")


if __name__ == "__main__":
    generar()
