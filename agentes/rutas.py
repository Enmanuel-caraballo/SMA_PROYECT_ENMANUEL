"""
SMA Movilidad y Seguridad CTG - MODELO MATEMÁTICO DE RUTAS Y PLANES (lo usa el Agente Coordinador)
=================================================================================================
La red vial de Bocagrande (OpenStreetMap, maqueta_virtual/bocagrande/bocagrande.json) es un GRAFO DIRIGIDO:
nodos = intersecciones, aristas = tramos de vía con largo, carriles y velocidad. Se construye igual que en el
gemelo 3D (bocagrande3d.js · construirGrafo) para que el modelo evalúe exactamente los planes que el gemelo ejecuta.

1) Costo de cada tramo (dos funciones estándar, se elige en conocimiento.FUNCION_COSTO):
     BPR (Bureau of Public Roads, 1964)   t(x) = t0 · (1 + α·(x / c)^β)        α = 0.15, β = 4
     Akçelik (1991, vías urbanas con semáforos; modela la cola cuando se supera la capacidad):
                                          t(x) = t0 + 0.25·T·[(z − 1) + √((z − 1)² + 8·J·z / (c·T))],  z = x / c
     BPR casi no castiga pasar de la capacidad (con un carril bloqueado da +35 s); Akçelik sí crece con la cola.
   t0 = largo / velocidad libre (+ espera media en rojo si el tramo llega a un semáforo)
   c  = capacidad = carriles · flujo de saturación · (verde / ciclo)      (los modos del semáforo cambian c)
2) Asignación de tráfico por EQUILIBRIO DE USUARIO (1er principio de Wardrop): cada conductor toma la ruta más
   rápida dadas las demás; se resuelve con el algoritmo de FRANK-WOLFE (Beckmann 1956, LeBlanc 1975):
       repetir: tiempos t(x) -> todo-o-nada por caminos mínimos (Dijkstra) -> y;  x <- x + λ·(y − x)
       (λ por bisección sobre la derivada de la función objetivo de Beckmann) hasta brecha relativa < tolerancia
3) Medida de desempeño: TSTT = Σ x·t(x) = tiempo total que pasa la gente en la red (veh·h por hora).
   El Coordinador compara el TSTT de cada plan (accidente = capacidad reducida; contraflujo = tramos invertidos).
4) Rutas propuestas: YEN k-caminos más cortos (networkx.shortest_simple_paths) con los tiempos del equilibrio.
5) Semáforos optimizados: descenso por coordenadas sobre el modo de cada cruce (normal/prioridad_a/prioridad_b)
   minimizando el TSTT -> el Coordinador puede PROPONER un plan que no está en el catálogo.
"""

import json
import math
from pathlib import Path

import networkx as nx
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import conocimiento as K

RAIZ = Path(__file__).resolve().parents[1]
RUTA_MAPA = RAIZ / "maqueta_virtual" / "bocagrande" / "bocagrande.json"
RUTA_PERFIL = RAIZ / "modelos" / "datos" / "perfil_demanda.json"

VEL_MAX = {"trunk": 14, "primary": 13, "secondary": 12, "tertiary": 10, "unclassified": 8, "residential": 8,
           "living_street": 5}                                   # m/s, igual que el gemelo 3D
TIEMPOS_MODO = {"normal": (8, 6), "prioridad_a": (20, 5), "prioridad_b": (5, 20),   # verde A, verde B (s)
                "peatonal": (8, 6), "corredor": (60, 0), "cerco": (0, 0)}
PERDIDO_CICLO = 6                                                 # 2 amarillos + 2 todo rojo (s)
PEATONAL_EXTRA = 14                                               # todo rojo de 8 s en cada cambio (8+8-2)


def verde_ciclo(modo, grupo):
    ga, gb = TIEMPOS_MODO.get(modo, TIEMPOS_MODO["normal"])
    ciclo = ga + gb + PERDIDO_CICLO + (PEATONAL_EXTRA if modo == "peatonal" else 0)
    if modo == "cerco":
        return 0.0, 60.0
    return (ga if grupo == "A" else gb) / ciclo, ciclo


class RedVial:
    def __init__(self, ruta=RUTA_MAPA):
        mapa = json.loads(Path(ruta).read_text(encoding="utf-8"))
        self.mapa = mapa
        self.nodos = np.array(mapa["nodos"], dtype=float)
        self.cruces = {c["id"]: {**c, "nodosSet": set(c["nodos"])} for c in mapa["cruces"]}
        self.base_contraflujo()
        self.construir()
        self.geografia()
        self.zonas = self.elegir_zonas()
        try:
            self.perfil = json.loads(RUTA_PERFIL.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.perfil = None
        self.demanda_pico = None              # se calibra con la capacidad (calibrar())

    # ------------------------------------------------------------ grafo (= construirGrafo del gemelo)
    def base_contraflujo(self):
        aristas, c3 = self.mapa["aristas"], self.cruces["cruce3"]
        self.c1 = {i for i, a in enumerate(aristas) if a["nombre"] == "Carrera 1" and all(q[1] < c3["z"] for q in a["p"])}
        self.nodos_c1 = {n for i in self.c1 for n in (aristas[i]["a"], aristas[i]["b"])}
        self.calle10 = next((i for i, a in enumerate(aristas) if a["nombre"] == "Calle 10" and
                             ((a["a"] in c3["nodosSet"] and a["b"] in self.nodos_c1) or
                              (a["b"] in c3["nodosSet"] and a["a"] in self.nodos_c1))), -1)

    def construir(self):
        aristas = self.mapa["aristas"]
        invertibles = {i for i in self.c1 if aristas[i]["unico"]} | ({self.calle10} if self.calle10 >= 0 else set())
        self.E = []                                    # aristas dirigidas
        for i, a in enumerate(aristas):
            sentidos = [(a["a"], a["b"], a["p"], False)]
            if not a["unico"]:
                sentidos.append((a["b"], a["a"], a["p"][::-1], False))
            elif i in invertibles:
                sentidos.append((a["b"], a["a"], a["p"][::-1], True))
            carriles = max(2 if a["largo"] < 45 else 1, a["carriles"]) if a["unico"] else max(1, a["carriles"] // 2)
            propias = []
            for desde, hasta, p, reserva in sentidos:
                largo = sum(math.dist(p[k], p[k + 1]) for k in range(len(p) - 1))
                e = {"id": len(self.E), "base": i, "desde": desde, "hasta": hasta, "p": p, "largo": largo,
                     "carriles": carriles, "unico": a["unico"], "nombre": a["nombre"],
                     "vmax": VEL_MAX.get(a["tipo"], 8), "reserva": reserva, "opuesta": None, "control": None}
                self.E.append(e)
                propias.append(e)
            if len(propias) == 2 and not propias[1]["reserva"]:
                propias[0]["opuesta"], propias[1]["opuesta"] = propias[1]["id"], propias[0]["id"]
        self.salientes, entrantes = {}, {}
        for e in self.E:
            self.salientes.setdefault(e["desde"], []).append(e)
            entrantes.setdefault(e["hasta"], []).append(e)
        self.entradas = [e for e in self.E if not e["reserva"] and e["largo"] > 25 and
                         not [x for x in entrantes.get(e["desde"], []) if x["base"] != e["base"]]]
        for c in self.cruces.values():
            for e in self.E:
                if not e["reserva"] and e["hasta"] in c["nodosSet"] and e["desde"] not in c["nodosSet"]:
                    e["control"] = (c["id"], "A" if e["nombre"] and e["nombre"] == c["via_principal"] else "B")

    def geografia(self):
        """Tramos clave del contraflujo (= prepararGeografia del gemelo)."""
        c3, c4, c5 = self.cruces["cruce3"], self.cruces["cruce4"], self.cruces["cruce5"]
        al_norte = lambda e: e["p"][-1][1] < e["p"][0][1]
        tramos_c1 = [e for e in self.E if e["base"] in self.c1]
        g = {}
        g["c1Norte"] = [e for e in tramos_c1 if al_norte(e)]
        g["c1Sur"] = [e for e in tramos_c1 if not al_norte(e)]
        g["nodoNorte"] = min(self.nodos_c1, key=lambda n: self.nodos[n][1])
        g["nodoSalida"] = next(n for n in c5["nodos"] if not any(not e["reserva"] for e in self.salientes.get(n, [])))
        g["entradaNorte"] = next((e for e in self.E if not e["reserva"] and e["desde"] == g["nodoNorte"]), None)
        ramales = [e for e in self.E if not e["reserva"] and e["desde"] in self.nodos_c1 and e["base"] not in self.c1
                   and "Calle 15" in (e["nombre"] or "")]
        g["ramal"] = next((e for e in ramales if g["entradaNorte"] and e["desde"] == g["entradaNorte"]["hasta"]), None)
        g["otrosRamales"] = [e for e in ramales if e is not g["ramal"]]
        g["calle10"] = [e for e in self.E if e["base"] == self.calle10]
        g["cierreC4"] = [e for e in self.E if not e["reserva"] and e["desde"] in c4["nodosSet"]
                         and e["hasta"] not in c4["nodosSet"] and e["hasta"] not in c3["nodosSet"]]
        g["cierreC3"] = [e for e in self.E if not e["reserva"] and e["desde"] in c3["nodosSet"]
                         and e["nombre"] == c3["via_principal"] and al_norte(e)]
        sm = [e for e in self.E if not e["reserva"] and e["nombre"] == c5["via_principal"]]
        g["accSalida"] = max((e for e in sm if e["hasta"] in c5["nodosSet"] and e["desde"] not in c5["nodosSet"]),
                             key=lambda e: e["largo"], default=None)
        g["accEntrada"] = max((e for e in sm if e["desde"] in c5["nodosSet"] and e["hasta"] not in c5["nodosSet"]),
                              key=lambda e: e["largo"], default=None)
        # entrada desde el Centro por la Av. San Martín (llega a Bocagrande por el cruce5)
        g["entradaCentro"] = next((e for e in self.entradas if e["nombre"] == c5["via_principal"]), None) \
            or g["accEntrada"]
        self.geo = g

    def elegir_zonas(self, n=8):
        """Zonas de origen/destino interiores: k-medias sobre los nodos, cada zona en el nodo más conectado."""
        grado = {k: len(v) for k, v in self.salientes.items()}
        candidatos = [k for k in grado if grado[k] >= 2]
        pts = self.nodos[candidatos]
        rng = np.random.default_rng(7)
        centros = pts[rng.choice(len(pts), n, replace=False)]
        for _ in range(30):
            etiqueta = np.argmin(((pts[:, None, :] - centros[None]) ** 2).sum(-1), axis=1)
            centros = np.array([pts[etiqueta == k].mean(0) if (etiqueta == k).any() else centros[k] for k in range(n)])
        zonas = []
        for c in centros:
            nodo = min(candidatos, key=lambda k: (np.hypot(*(self.nodos[k] - c)) - 15 * grado[k]))
            if nodo not in zonas:
                zonas.append(nodo)
        return zonas

    # ------------------------------------------------------------ escenario (accidente + plan)
    def escenario(self, plan=None, semaforos=None, accidente=None):
        """Devuelve (habil, carriles, modos, salidas, (origen_norte, origen_centro), bloqueados) para un plan.
        plan: clave del catálogo; semaforos: {cruce: modo}; accidente: {"cruce", "sentido"} o {"arista_base": i}."""
        g = self.geo
        habil = np.array([not e["reserva"] for e in self.E])
        carriles = np.array([float(e["carriles"]) for e in self.E])
        cerrar, abrir, prestar = [], [], []
        salidas = {g["nodoSalida"]}
        if plan == "contraflujo_salida":
            cerrar += [e for e in g["c1Sur"] if not e["reserva"]] + g["otrosRamales"] + g["cierreC4"] + g["cierreC3"] \
                + [e for e in g["calle10"] if not e["reserva"]]
            abrir += [e for e in g["c1Norte"] if e["reserva"]] + [e for e in g["calle10"] if e["reserva"]]
            prestar += [e for e in g["c1Norte"] if not e["reserva"] and not e["unico"]]
            salidas = {g["nodoNorte"]}
        elif plan == "contraflujo_entrada":
            cerrar += [e for e in g["c1Norte"] if not e["reserva"]] + [g["ramal"]] + g["otrosRamales"]
            prestar += [e for e in g["c1Sur"] if not e["reserva"] and not e["unico"]]
        for e in cerrar:
            if e:
                habil[e["id"]] = False
        for e in abrir:
            habil[e["id"]] = True
        for e in prestar:                      # carril prestado: el otro sentido está cerrado/vacío
            carriles[e["id"]] += 1
        # accidente: el tramo queda CERRADO (CAPACIDAD_RESIDUAL_ACCIDENTE = 0) o con una capacidad residual
        bloqueados = []
        if accidente:
            if accidente.get("arista_base") is not None:
                bloqueados = [e for e in self.E if e["base"] == accidente["arista_base"] and not e["reserva"]]
            elif accidente.get("cruce") == "cruce5":
                bloqueados = [g["accEntrada"] if accidente.get("sentido") == "entrada" else g["accSalida"]]
            elif accidente.get("cruce") in self.cruces:
                acc = [e for e in self.E if e["control"] and e["control"][0] == accidente["cruce"]]
                elegido = next((e for e in acc if e["control"][1] == "B" and e["largo"] > 30), None) \
                    or next((e for e in acc if e["largo"] > 30), None) or (acc[0] if acc else None)
                bloqueados = [elegido] if elegido else []
        for e in bloqueados:
            if e and K.CAPACIDAD_RESIDUAL_ACCIDENTE <= 0:
                habil[e["id"]] = False
            elif e:
                carriles[e["id"]] = max(K.CAPACIDAD_RESIDUAL_ACCIDENTE, carriles[e["id"]] - 1 + K.CAPACIDAD_RESIDUAL_ACCIDENTE)
        modos = {c: "normal" for c in self.cruces}
        modos.update(semaforos or {})
        norte = g["entradaNorte"]
        origen_norte = norte["desde"] if norte is not None and habil[norte["id"]] else \
            (g["ramal"]["desde"] if g["ramal"] is not None else None)
        # con el contraflujo de entrada, lo que viene del Centro entra SOLO por la Carrera 1 (desde el norte)
        origen_centro = g["nodoNorte"] if plan == "contraflujo_entrada" else \
            (g["entradaCentro"]["desde"] if g["entradaCentro"] is not None else None)
        return habil, carriles, modos, salidas, (origen_norte, origen_centro), [e["id"] for e in bloqueados if e]

    def parametros(self, carriles, modos):
        """t0 (s) y capacidad (veh/h) de cada arista dirigida."""
        t0 = np.array([e["largo"] / e["vmax"] for e in self.E])
        cap = carriles * K.FLUJO_SATURACION
        for e in self.E:
            if e["control"]:
                cruce, grupo = e["control"]
                gc, ciclo = verde_ciclo(modos.get(cruce, "normal"), grupo)
                cap[e["id"]] *= max(gc, 0.01)
                t0[e["id"]] += ciclo * (1 - gc) ** 2 / 2          # espera media en rojo a flujo bajo (Webster)
        return t0, cap

    # ------------------------------------------------------------ demanda (orígenes-destinos)
    def factor_hora(self, hora=None, fin_de_semana=False):
        if hora is None or not self.perfil:
            return 1.0
        return self.perfil["fin_de_semana" if fin_de_semana else "habil"][int(hora) % 24]

    def demanda(self, salidas, origenes, escala=1.0):
        """Viajes por hora: salir de Bocagrande (zonas -> salida), entrar (Centro y norte -> zonas) y circulación interna."""
        origen_norte, origen_centro = origenes
        pico = (self.demanda_pico or K.DEMANDA_PICO_VEH_H) * escala
        od = []
        nz = len(self.zonas)
        for z in self.zonas:
            od.append((z, "SALIDA", pico * K.REPARTO_DEMANDA["salir"] / nz))
            if origen_centro is not None:
                od.append((origen_centro, z, pico * K.REPARTO_DEMANDA["entrar_centro"] / nz))
            if origen_norte is not None:
                od.append((origen_norte, z, pico * K.REPARTO_DEMANDA["entrar_norte"] / nz))
            for w in self.zonas:
                if w != z:
                    od.append((z, w, pico * K.REPARTO_DEMANDA["interna"] / (nz * (nz - 1))))
        return od

    # ------------------------------------------------------------ Frank-Wolfe
    def asignar(self, habil, carriles, modos, salidas, origen_norte, escala=1.0, tol=None, max_iter=None):
        tol, max_iter = tol or K.FW_TOLERANCIA, max_iter or K.FW_MAX_ITER
        t0, cap = self.parametros(carriles, modos)
        n = len(self.nodos)
        SUMIDERO = n                                   # nodo virtual: cualquier salida vale
        ids = np.where(habil)[0]
        desde = np.array([self.E[i]["desde"] for i in ids] + list(salidas))
        hasta = np.array([self.E[i]["hasta"] for i in ids] + [SUMIDERO] * len(salidas))
        od = [(o, SUMIDERO if d == "SALIDA" else d, q) for o, d, q in self.demanda(salidas, origen_norte, escala)]
        origenes = sorted({o for o, _, _ in od})
        x = np.zeros(len(ids))

        def tiempos(flujo):
            c = cap[ids]
            z = flujo / c
            if K.FUNCION_COSTO == "bpr":
                return t0[ids] * (1 + K.BPR_ALFA * z ** K.BPR_BETA)
            T, J = K.AKCELIK_T_H, K.AKCELIK_J
            return t0[ids] + 900 * T * ((z - 1) + np.sqrt((z - 1) ** 2 + 8 * J * z / (c * T)))

        def todo_o_nada(t):
            # aristas paralelas (mismo par de nodos): se usa la más rápida (csr sumaría los pesos duplicados)
            arista_de = {}
            for k, (a, b) in enumerate(zip(desde[:len(ids)], hasta[:len(ids)])):
                par = (int(a), int(b))
                if par not in arista_de or t[k] < t[arista_de[par]]:
                    arista_de[par] = k
            pares = list(arista_de) + [(int(s), SUMIDERO) for s in salidas]
            pesos = [t[arista_de[p]] for p in arista_de] + [1e-6] * len(salidas)
            g = csr_matrix((pesos, ([p[0] for p in pares], [p[1] for p in pares])), shape=(n + 1, n + 1))
            dist, pred = dijkstra(g, directed=True, indices=origenes, return_predecessors=True)
            y = np.zeros(len(ids))
            sin_ruta, costo = 0.0, 0.0
            for o, d, q in od:
                fila = origenes.index(o)
                if not np.isfinite(dist[fila, d]):
                    sin_ruta += q
                    continue
                costo += q * dist[fila, d]
                v = d
                while v != o:
                    u = pred[fila, v]
                    k = arista_de.get((int(u), int(v)))
                    if k is not None:
                        y[k] += q
                    v = u
            return y, sin_ruta, costo

        y, sin_ruta, _ = todo_o_nada(tiempos(x))
        x = y
        brecha = 1.0
        for it in range(max_iter):
            t = tiempos(x)
            y, sin_ruta, costo_min = todo_o_nada(t)
            tstt = float(np.dot(x, t))
            brecha = (tstt - costo_min) / max(tstt, 1e-9)
            if brecha < tol:
                break
            d = y - x
            lo, hi = 0.0, 1.0                          # bisección: derivada de Beckmann = Σ d · t(x + λd)
            for _ in range(25):
                m = (lo + hi) / 2
                if np.dot(d, tiempos(x + m * d)) > 0:
                    hi = m
                else:
                    lo = m
            x = x + (lo + hi) / 2 * d
        t = tiempos(x)
        flujo = np.zeros(len(self.E))
        flujo[ids] = x
        tiempo = np.full(len(self.E), np.inf)
        tiempo[ids] = t
        total = sum(q for _, _, q in od)
        tstt_h = (float(np.dot(x, t)) + sin_ruta * K.PENALIZACION_SIN_RUTA_S) / 3600
        vc = np.zeros(len(self.E))
        vc[ids] = x / cap[ids]
        return {"tstt_vh": round(tstt_h, 2), "viaje_medio_min": round(tstt_h * 60 / max(total, 1e-9), 2),
                "demanda_vh": round(total, 1), "sin_ruta_vh": round(sin_ruta, 1), "iteraciones": it + 1,
                "brecha": round(brecha, 5), "flujo": flujo, "tiempo": tiempo, "vc": vc, "habil": habil}

    def calibrar(self, objetivo=None):
        """Escala la demanda pico para que en hora pico normal la Av. San Martín de salida (cruce5, por donde sale
        todo Bocagrande) quede en v/c = objetivo. Se ajusta por bisección sobre la escala de la demanda."""
        objetivo = objetivo or K.VC_PICO_OBJETIVO
        clave = self.geo["accSalida"]["id"]
        lo, hi = 500.0, 20000.0
        for _ in range(30):
            self.demanda_pico = (lo + hi) / 2
            vc = float(self.asignar(*self.escenario()[:5])["vc"][clave])
            lo, hi = (self.demanda_pico, hi) if vc < objetivo else (lo, self.demanda_pico)
        return self.demanda_pico

    # ------------------------------------------------------------ evaluación de planes
    def evaluar(self, plan=None, semaforos=None, accidente=None, hora=None, escala=1.0):
        if self.demanda_pico is None:
            self.calibrar()
        habil, carriles, modos, salidas, origen_norte, bloqueados = self.escenario(plan, semaforos, accidente)
        r = self.asignar(habil, carriles, modos, salidas, origen_norte, escala * self.factor_hora(hora))
        r.update(plan=plan or "nada", semaforos=modos, salidas=salidas, origen_norte=origen_norte[0],
                 origen_centro=origen_norte[1], bloqueados=bloqueados)
        return r

    def optimizar_semaforos(self, plan=None, accidente=None, hora=None, escala=1.0, base=None, pasadas=2):
        """Descenso por coordenadas: para cada cruce prueba los 3 modos y deja el que más baja el TSTT."""
        modos = {c: "normal" for c in self.cruces}
        modos.update(base or {})
        mejor = self.evaluar(plan, modos, accidente, hora, escala)
        for _ in range(pasadas):
            cambio = False
            for c in self.cruces:
                if modos[c] not in K.MODOS_ADAPTATIVOS:
                    continue
                for m in K.MODOS_ADAPTATIVOS:
                    if m == modos[c]:
                        continue
                    prueba = self.evaluar(plan, {**modos, c: m}, accidente, hora, escala)
                    if prueba["tstt_vh"] < mejor["tstt_vh"] - 1e-3:
                        mejor, modos, cambio = prueba, {**modos, c: m}, True
            if not cambio:
                break
        return {c: m for c, m in modos.items() if m != "normal"}, mejor

    # ------------------------------------------------------------ rutas sugeridas (Yen)
    def grafo_nx(self, resultado):
        G = nx.DiGraph()
        for e in self.E:
            if resultado["habil"][e["id"]] and np.isfinite(resultado["tiempo"][e["id"]]):
                t = float(resultado["tiempo"][e["id"]])
                if not G.has_edge(e["desde"], e["hasta"]) or G[e["desde"]][e["hasta"]]["t"] > t:
                    G.add_edge(e["desde"], e["hasta"], t=t, id=e["id"])
        return G

    def rutas(self, resultado, origen, destino, k=None):
        """k rutas más rápidas (Yen) entre dos nodos con los tiempos del equilibrio."""
        k = k or K.K_RUTAS
        G = self.grafo_nx(resultado)
        salida = []
        try:
            for camino in nx.shortest_simple_paths(G, origen, destino, weight="t"):
                ids = [G[u][v]["id"] for u, v in zip(camino[:-1], camino[1:])]
                calles = []
                for i in ids:
                    nombre = self.E[i]["nombre"] or "vía sin nombre"
                    if not calles or calles[-1] != nombre:
                        calles.append(nombre)
                salida.append({"calles": calles, "aristas": ids, "bases": sorted({self.E[i]["base"] for i in ids}),
                               "nodos": [int(self.E[ids[0]]["desde"])] + [int(self.E[i]["hasta"]) for i in ids] if ids else [],
                               "minutos": round(sum(float(resultado["tiempo"][i]) for i in ids) / 60, 1),
                               "km": round(sum(self.E[i]["largo"] for i in ids) / 1000, 2)})
                if len(salida) >= k:
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass
        # reparto de los conductores entre las rutas: logit sobre el tiempo de viaje (Dial 1971)
        if salida:
            t0 = min(x["minutos"] for x in salida)
            w = [np.exp(-(x["minutos"] - t0) / K.DISPERSION_RUTAS_MIN) for x in salida]
            for x, wi in zip(salida, w):
                x["reparto"] = round(float(wi / sum(w)), 3)
        return salida

    def rutas_plan(self, resultado):
        """Rutas que se proponen a la ciudadanía con un plan: salir de Bocagrande y entrar a Bocagrande."""
        centro = min(self.zonas, key=lambda z: np.hypot(*(self.nodos[z] - self.nodos[self.cruces["cruce3"]["nodos"][0]])))
        propuestas = {}
        for salida in resultado["salidas"]:
            propuestas["salir"] = self.rutas(resultado, centro, salida)
        entrada = resultado["origen_centro"]
        if entrada is not None:
            propuestas["entrar_centro"] = self.rutas(resultado, entrada, centro)
        if resultado["origen_norte"] is not None:
            propuestas["entrar_norte"] = self.rutas(resultado, resultado["origen_norte"], centro)
        return propuestas


_RED = None


def red():
    """Red única por proceso (se construye y calibra una vez)."""
    global _RED
    if _RED is None:
        _RED = RedVial()
        _RED.calibrar()
    return _RED


def resumen(r):
    return {k: r[k] for k in ("plan", "tstt_vh", "viaje_medio_min", "demanda_vh", "sin_ruta_vh", "iteraciones", "brecha")}


if __name__ == "__main__":
    import sys
    import time
    sys.stdout.reconfigure(errors="replace")
    t = time.time()
    R = red()
    print(f"red: {len(R.nodos)} nodos, {len(R.E)} aristas dirigidas, {len(R.entradas)} entradas, zonas {R.zonas}; "
          f"demanda pico calibrada = {R.demanda_pico:.0f} veh/h ({time.time() - t:.1f} s)")
    for acc in [None, {"cruce": "cruce5", "sentido": "salida"}, {"cruce": "cruce5", "sentido": "entrada"}]:
        print(f"\naccidente: {acc}")
        candidatos = [None, "contraflujo_salida" if not acc or acc.get("sentido") != "entrada" else "contraflujo_entrada"]
        for plan in candidatos:
            t = time.time()
            r = R.evaluar(plan, None, acc, hora=17)
            print(f"  {plan or 'nada':22s} TSTT={r['tstt_vh']:8.2f} veh·h  viaje medio={r['viaje_medio_min']:5.2f} min  "
                  f"sin ruta={r['sin_ruta_vh']:6.1f}  FW {r['iteraciones']} it, brecha {r['brecha']}  ({time.time() - t:.2f} s)")
        t = time.time()
        modos, mejor = R.optimizar_semaforos(None, acc, hora=17)
        print(f"  semáforos optimizados: {modos} -> TSTT {mejor['tstt_vh']:.2f} ({time.time() - t:.1f} s)")
        for nombre, rs in R.rutas_plan(mejor).items():
            for x in rs:
                print(f"    {nombre}: {' -> '.join(x['calles'])}  ({x['minutos']} min, {x['km']} km)")
