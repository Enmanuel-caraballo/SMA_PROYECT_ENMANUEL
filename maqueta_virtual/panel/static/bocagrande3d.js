// =====================================================================
// SMA Movilidad y Seguridad CTG - Gemelo digital 3D de Bocagrande
// Escena Three.js + simulación de tráfico vehicular y peatonal que obedece
// a los semáforos reales del SMA (sma/estado/<cruce>) y reacciona a los eventos.
// =====================================================================
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

// ---------------- Parámetros ----------------
const ANCHO_CARRIL = 3.2;
const VEL_MAX = { trunk: 14, primary: 13, secondary: 12, tertiary: 10, unclassified: 8, residential: 8, living_street: 5 };
const CARROS_BASE = 260, PEATONES_BASE = 220;
const MAX_CARROS = 600, MAX_PEATONES = 500;
const ESC_CARRO = 1.5, ESC_PEATON = 2.0;   // exagerados para que se vean junto a las torres
const LARGO_CARRO = 4.3 * ESC_CARRO + 1.5; // separación mínima entre carros
const COLORES_CARRO = [0x00e5ff, 0xff2bd6, 0x7c4dff, 0x00ff9c, 0xffb300, 0x2979ff, 0xe0e0ff, 0xff5252];
const C = { verde: 0x00ff7a, amarillo: 0xffd000, rojo: 0xff1744, apagado: 0x26303d };
const COLOR_MODO = { normal: 0x00e5ff, corredor: 0x00ff7a, cerco: 0xff1744 };

// ---------------- Estado global ----------------
export const estado = {
  semaforos: {},          // cruce -> {modo, A, B}
  cruces: [],             // datos de bocagrande.json + panel
  carros: [], peatones: [],
  accidentes: [],         // {cruce, arista, carril, s, hasta}
  objetivoCarros: CARROS_BASE, objetivoPeatones: PEATONES_BASE,
  congestionHasta: 0, aglomeraciones: [],
  metricas: { carros: 0, vel: 0, peatones: 0, detenidos: 0, colas: {} },
  alSeleccionar: null,
  bloom: true, fps: 0, ms: {},
};

let escena, camara, render, compositor, etiquetas, controles, reloj;
let mapa, aristasDir = [], salientes = [], entradas = [], control = {};
let mallaCarros, mallaFarosD, mallaFarosT, mallaBalizas, mallaPeatones;
const tmp = new THREE.Object3D(), colorTmp = new THREE.Color();
const anillos = {}, lucesCruce = {};
let tiempo = 0;

// =====================================================================
// 1. Carga y construcción de la escena
// =====================================================================
export async function iniciar(contenedor) {
  mapa = await (await fetch("/datos/bocagrande.json")).json();
  const panel = await (await fetch("/api/cruces")).json();
  const porId = Object.fromEntries(panel.map(c => [c.id, c]));
  estado.cruces = mapa.cruces.map(c => ({ ...c, ...porId[c.id], nodosSet: new Set(c.nodos) }));

  escena = new THREE.Scene();
  escena.background = new THREE.Color(0x03050a);
  escena.fog = new THREE.FogExp2(0x03050a, 0.00085);

  camara = new THREE.PerspectiveCamera(50, contenedor.clientWidth / contenedor.clientHeight, 1, 6000);
  camara.position.set(-260, 620, 330);

  render = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  render.setPixelRatio(1);
  render.setSize(contenedor.clientWidth, contenedor.clientHeight);
  render.toneMapping = THREE.ACESFilmicToneMapping;
  contenedor.appendChild(render.domElement);

  etiquetas = new CSS2DRenderer();
  etiquetas.setSize(contenedor.clientWidth, contenedor.clientHeight);
  etiquetas.domElement.className = "capa-etiquetas";
  contenedor.appendChild(etiquetas.domElement);

  controles = new OrbitControls(camara, render.domElement);
  controles.target.set(80, 0, -120);
  controles.enableDamping = true;
  controles.maxPolarAngle = Math.PI * 0.47;
  controles.minDistance = 40;
  controles.maxDistance = 2200;

  compositor = new EffectComposer(render);
  compositor.addPass(new RenderPass(escena, camara));
  // brillo neón a media resolución (mucho más liviano en GPUs integradas)
  compositor.addPass(new UnrealBloomPass(new THREE.Vector2(contenedor.clientWidth / 2, contenedor.clientHeight / 2), 0.9, 0.55, 0.18));
  compositor.addPass(new OutputPass());

  escena.add(new THREE.HemisphereLight(0x3a5a9a, 0x05070d, 0.9));
  const luna = new THREE.DirectionalLight(0x8fb4ff, 0.6);
  luna.position.set(-300, 600, 200);
  escena.add(luna);

  construirSuelo();
  construirVias();
  construirEdificios();
  construirGrafo();
  construirCruces();
  construirFlotas();

  window.addEventListener("resize", () => {
    const w = contenedor.clientWidth, h = contenedor.clientHeight;
    camara.aspect = w / h; camara.updateProjectionMatrix();
    render.setSize(w, h); compositor.setSize(w, h); etiquetas.setSize(w, h);
  });
  render.domElement.addEventListener("pointerdown", e => { abajo = [e.clientX, e.clientY]; });
  render.domElement.addEventListener("pointerup", e => {
    if (abajo && Math.hypot(e.clientX - abajo[0], e.clientY - abajo[1]) < 5) clicEscena(e);
  });

  window.__bocagrande = { camara, controles, escena };   // para depurar desde la consola
  reloj = new THREE.Clock();
  render.setAnimationLoop(cuadro);
}
let abajo = null;

function construirSuelo() {
  // Mar / suelo oscuro con rejilla holográfica
  const suelo = new THREE.Mesh(new THREE.PlaneGeometry(8000, 8000),
    new THREE.MeshStandardMaterial({ color: 0x040812, roughness: 0.9, metalness: 0.1 }));
  suelo.rotation.x = -Math.PI / 2; suelo.position.y = -0.6;
  escena.add(suelo);
  const rejilla = new THREE.GridHelper(4000, 200, 0x0b3a5a, 0x08182a);
  rejilla.position.y = -0.5;
  rejilla.material.transparent = true; rejilla.material.opacity = 0.55;
  escena.add(rejilla);
}

function cinta(pts, ancho, y) {
  // Geometría de una franja (vía) a lo largo de una polilínea
  const pos = [], idx = [];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    let dx = b[0] - a[0], dz = b[1] - a[1]; const l = Math.hypot(dx, dz) || 1; dx /= l; dz /= l;
    const nx = -dz * ancho / 2, nz = dx * ancho / 2;
    pos.push(pts[i][0] + nx, y, pts[i][1] + nz, pts[i][0] - nx, y, pts[i][1] - nz);
    if (i > 0) { const k = (i - 1) * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx); g.computeVertexNormals();
  return g;
}

function anchoVia(a) { return Math.max(a.carriles, a.unico ? 1 : 2) * ANCHO_CARRIL + 1; }

function construirVias() {
  const asfalto = [], andenes = [], bordes = [], centro = [];
  for (const a of mapa.aristas) {
    const w = anchoVia(a);
    asfalto.push(cinta(a.p, w, 0.12));
    andenes.push(cinta(a.p, w + 5, 0.0));
    // bordes neón de la vía
    for (const lado of [1, -1]) {
      const linea = desplazar(a.p, lado * w / 2);
      for (let i = 0; i < linea.length - 1; i++) bordes.push(linea[i][0], 0.2, linea[i][1], linea[i + 1][0], 0.2, linea[i + 1][1]);
    }
    if (!a.unico) for (let i = 0; i < a.p.length - 1; i++) centro.push(a.p[i][0], 0.2, a.p[i][1], a.p[i + 1][0], 0.2, a.p[i + 1][1]);
  }
  escena.add(new THREE.Mesh(mergeGeometries(andenes), new THREE.MeshStandardMaterial({ color: 0x0c1322, roughness: 0.8, side: THREE.DoubleSide })));
  escena.add(new THREE.Mesh(mergeGeometries(asfalto), new THREE.MeshStandardMaterial({ color: 0x070b14, roughness: 0.6, metalness: 0.3, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1 })));
  const gB = new THREE.BufferGeometry(); gB.setAttribute("position", new THREE.Float32BufferAttribute(bordes, 3));
  escena.add(new THREE.LineSegments(gB, new THREE.LineBasicMaterial({ color: 0x00b8ff, transparent: true, opacity: 0.55 })));
  const gC = new THREE.BufferGeometry(); gC.setAttribute("position", new THREE.Float32BufferAttribute(centro, 3));
  escena.add(new THREE.LineSegments(gC, new THREE.LineBasicMaterial({ color: 0xff9d00, transparent: true, opacity: 0.35 })));
  // senderos peatonales
  const sen = [];
  for (const s of mapa.senderos) for (let i = 0; i < s.length - 1; i++) sen.push(s[i][0], 0.06, s[i][1], s[i + 1][0], 0.06, s[i + 1][1]);
  const gS = new THREE.BufferGeometry(); gS.setAttribute("position", new THREE.Float32BufferAttribute(sen, 3));
  escena.add(new THREE.LineSegments(gS, new THREE.LineBasicMaterial({ color: 0xb04dff, transparent: true, opacity: 0.35 })));
  // pasos peatonales (cebras)
  const cebra = new THREE.InstancedMesh(new THREE.BoxGeometry(2.6, 0.05, 2.6),
    new THREE.MeshBasicMaterial({ color: 0xd8f6ff, transparent: true, opacity: 0.25 }), mapa.pasos.length);
  mapa.pasos.forEach((p, i) => { tmp.position.set(p[0], 0.22, p[1]); tmp.rotation.set(0, 0, 0); tmp.updateMatrix(); cebra.setMatrixAt(i, tmp.matrix); });
  escena.add(cebra);
}

function texturaVentanas() {
  const cv = document.createElement("canvas"); cv.width = 64; cv.height = 64;
  const g = cv.getContext("2d");
  g.fillStyle = "#000"; g.fillRect(0, 0, 64, 64);
  g.fillStyle = "#fff"; g.fillRect(14, 18, 36, 26);
  const t = new THREE.CanvasTexture(cv);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

function construirEdificios() {
  const pos = [], uv = [], col = [], techos = [], lineas = [];
  const paleta = [new THREE.Color(0x00e5ff), new THREE.Color(0x7c4dff), new THREE.Color(0xff2bd6), new THREE.Color(0x2979ff)];
  for (const [n, e] of mapa.edificios.entries()) {
    const p = e.p, h = e.h;
    if (p.length < 3) continue;
    const brillo = paleta[n % paleta.length].clone().multiplyScalar(0.15 + (n * 37 % 10) / 22);
    let u = 0;
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length], l = Math.hypot(b[0] - a[0], b[1] - a[1]);
      pos.push(a[0], 0, a[1], b[0], 0, b[1], b[0], h, b[1], a[0], 0, a[1], b[0], h, b[1], a[0], h, a[1]);
      uv.push(u / 4, 0, (u + l) / 4, 0, (u + l) / 4, h / 3.2, u / 4, 0, (u + l) / 4, h / 3.2, u / 4, h / 3.2);
      for (let k = 0; k < 6; k++) col.push(brillo.r, brillo.g, brillo.b);
      u += l;
      lineas.push(a[0], h, a[1], b[0], h, b[1]);
      if (h > 30 && i % 2 === 0) lineas.push(a[0], 0, a[1], a[0], h, a[1]);
    }
    const forma = new THREE.Shape(p.map(q => new THREE.Vector2(q[0], -q[1])));
    const gt = new THREE.ShapeGeometry(forma);
    gt.rotateX(-Math.PI / 2); gt.translate(0, h, 0);
    techos.push(gt);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.computeVertexNormals();
  const ventanas = texturaVentanas();
  const muros = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
    color: 0x0a1020, roughness: 0.35, metalness: 0.6, side: THREE.DoubleSide,
    emissive: 0xffffff, emissiveMap: ventanas, emissiveIntensity: 0.9, vertexColors: false,
  }));
  // las ventanas toman el color de cada edificio
  muros.material.onBeforeCompile = sh => {
    sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nattribute vec3 color;\nvarying vec3 vTinte;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvTinte = color;");
    sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\nvarying vec3 vTinte;")
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\ntotalEmissiveRadiance *= vTinte;");
  };
  escena.add(muros);
  if (techos.length) escena.add(new THREE.Mesh(mergeGeometries(techos), new THREE.MeshStandardMaterial({ color: 0x0b1428, roughness: 0.7, side: THREE.DoubleSide })));
  const gl = new THREE.BufferGeometry(); gl.setAttribute("position", new THREE.Float32BufferAttribute(lineas, 3));
  escena.add(new THREE.LineSegments(gl, new THREE.LineBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.45 })));
}

function desplazar(pts, d) {
  return pts.map((q, i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    let dx = b[0] - a[0], dz = b[1] - a[1]; const l = Math.hypot(dx, dz) || 1;
    return [q[0] - dz / l * d, q[1] + dx / l * d];
  });
}

// =====================================================================
// 2. Grafo dirigido para la simulación
// =====================================================================
function construirGrafo() {
  const nombreDe = {};
  mapa.aristas.forEach((a, i) => {
    const sentidos = a.unico ? [[a.a, a.b, a.p]] : [[a.a, a.b, a.p], [a.b, a.a, [...a.p].reverse()]];
    const carriles = a.unico ? Math.max(1, a.carriles) : Math.max(1, Math.floor(a.carriles / 2));
    for (const [desde, hasta, p] of sentidos) {
      const acum = [0];
      for (let k = 1; k < p.length; k++) acum.push(acum[k - 1] + Math.hypot(p[k][0] - p[k - 1][0], p[k][1] - p[k - 1][1]));
      aristasDir.push({ id: aristasDir.length, base: i, desde, hasta, p, acum, largo: acum[acum.length - 1],
        carriles, unico: a.unico, nombre: a.nombre, vmax: VEL_MAX[a.tipo] || 8, ocup: [] });
    }
  });
  const entrantes = [];
  for (const e of aristasDir) {
    (salientes[e.desde] ||= []).push(e);
    (entrantes[e.hasta] ||= []).push(e);
  }
  // Entradas: aristas que salen de nodos sin aristas entrantes de otro nodo (bordes del mapa)
  for (const e of aristasDir) {
    const llegan = (entrantes[e.desde] || []).filter(x => x.base !== e.base);
    if (llegan.length === 0 && e.largo > 25) entradas.push(e);
  }
  // Control semafórico: arista que entra a un cruce desde fuera de él
  for (const c of estado.cruces) {
    for (const e of aristasDir) {
      if (c.nodosSet.has(e.hasta) && !c.nodosSet.has(e.desde)) {
        const grupo = e.nombre && e.nombre === c.via_principal ? "A" : "B";
        control[e.id] = { cruce: c.id, grupo };
      }
    }
  }
}

function puntoEn(e, s, lateral) {
  const a = e.acum; let k = 1;
  while (k < a.length - 1 && a[k] < s) k++;
  const p0 = e.p[k - 1], p1 = e.p[k], seg = a[k] - a[k - 1] || 1;
  const t = Math.min(1, Math.max(0, (s - a[k - 1]) / seg));
  const dx = (p1[0] - p0[0]) / seg, dz = (p1[1] - p0[1]) / seg;
  return { x: p0[0] + (p1[0] - p0[0]) * t - dz * lateral, z: p0[1] + (p1[1] - p0[1]) * t + dx * lateral, ang: Math.atan2(dx, dz) };
}

function lateralCarril(e, carril) {
  return e.unico ? (carril - (e.carriles - 1) / 2) * ANCHO_CARRIL : (carril + 0.5) * ANCHO_CARRIL;
}

// =====================================================================
// 3. Cruces: anillos holográficos, semáforos y etiquetas
// =====================================================================
function construirCruces() {
  for (const c of estado.cruces) {
    const g = new THREE.Group(); g.position.set(c.x, 0, c.z);
    const anillo = new THREE.Mesh(new THREE.RingGeometry(16, 18, 64),
      new THREE.MeshBasicMaterial({ color: COLOR_MODO.normal, transparent: true, opacity: 0.85, side: THREE.DoubleSide }));
    anillo.rotation.x = -Math.PI / 2; anillo.position.y = 0.25;
    anillo.userData.cruce = c.id;
    const columna = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.4, 60, 8, 1, true),
      new THREE.MeshBasicMaterial({ color: COLOR_MODO.normal, transparent: true, opacity: 0.25 }));
    columna.position.y = 30;
    g.add(anillo, columna);
    const div = document.createElement("div");
    div.className = "etq-cruce";
    div.innerHTML = `<b>${c.id}</b>${c.fisico ? '<span class="esp">ESP32</span>' : ""}<small>${c.nombre || ""}</small><em>sin señal</em>`;
    div.addEventListener("click", () => seleccionar(c.id));
    const etq = new CSS2DObject(div); etq.position.set(0, 64, 0);
    g.add(etq);
    escena.add(g);
    anillos[c.id] = { anillo, columna, div };
    // postes de semáforo en cada acceso controlado
    lucesCruce[c.id] = [];
    for (const [id, ctl] of Object.entries(control)) {
      if (ctl.cruce !== c.id) continue;
      const e = aristasDir[id];
      const pto = puntoEn(e, Math.max(0, e.largo - 4), lateralCarril(e, e.carriles - 1) + ANCHO_CARRIL);
      const poste = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 6, 6), new THREE.MeshStandardMaterial({ color: 0x334455 }));
      poste.position.set(pto.x, 3, pto.z);
      const luz = new THREE.Mesh(new THREE.SphereGeometry(0.9, 12, 8), new THREE.MeshBasicMaterial({ color: C.apagado }));
      luz.position.set(pto.x, 6.6, pto.z);
      const halo = new THREE.Mesh(new THREE.CircleGeometry(3.2, 24), new THREE.MeshBasicMaterial({ color: C.apagado, transparent: true, opacity: 0.0 }));
      halo.rotation.x = -Math.PI / 2;
      const linea = puntoEn(e, Math.max(0, e.largo - 3), lateralCarril(e, 0));
      halo.position.set(linea.x, 0.15, linea.z);
      escena.add(poste, luz, halo);
      lucesCruce[c.id].push({ grupo: ctl.grupo, luz, halo });
    }
  }
}

export function actualizarSemaforo(id, est) {
  estado.semaforos[id] = est;
  const a = anillos[id]; if (!a) return;
  const col = COLOR_MODO[est.modo] ?? COLOR_MODO.normal;
  a.anillo.material.color.setHex(col); a.columna.material.color.setHex(col);
  a.div.querySelector("em").textContent = `${est.modo} · A ${est.A} · B ${est.B}`;
  a.div.dataset.modo = est.modo;
  for (const l of lucesCruce[id] || []) {
    const color = C[est[l.grupo]] ?? C.apagado;
    l.luz.material.color.setHex(color);
    l.halo.material.color.setHex(color); l.halo.material.opacity = 0.35;
  }
}

// =====================================================================
// 4. Vehículos y peatones (InstancedMesh)
// =====================================================================
function construirFlotas() {
  const cuerpo = new THREE.BoxGeometry(1.9, 1.3, 4.3); cuerpo.translate(0, 0.85, 0);
  const cabina = new THREE.BoxGeometry(1.6, 0.6, 2.0); cabina.translate(0, 1.75, -0.3);
  mallaCarros = new THREE.InstancedMesh(mergeGeometries([cuerpo, cabina]),
    new THREE.MeshBasicMaterial({ color: 0xffffff }), MAX_CARROS);   // carrocería neón (el color va por instancia)
  const faro = new THREE.BoxGeometry(1.7, 0.25, 0.1);
  mallaFarosD = new THREE.InstancedMesh(faro.clone().translate(0, 0.9, 2.18), new THREE.MeshBasicMaterial({ color: 0xeaffff }), MAX_CARROS);
  mallaFarosT = new THREE.InstancedMesh(faro.clone().translate(0, 0.9, -2.18), new THREE.MeshBasicMaterial({ color: 0xff1744 }), MAX_CARROS);
  mallaBalizas = new THREE.InstancedMesh(new THREE.BoxGeometry(1.2, 0.35, 0.5).translate(0, 2.25, -0.3),
    new THREE.MeshBasicMaterial({ color: 0xffffff }), 60);
  const persona = new THREE.CapsuleGeometry(0.28, 1.1, 3, 8); persona.translate(0, 0.85, 0);
  mallaPeatones = new THREE.InstancedMesh(persona, new THREE.MeshBasicMaterial({ color: 0xffffff }), MAX_PEATONES);
  for (const m of [mallaCarros, mallaFarosD, mallaFarosT, mallaBalizas, mallaPeatones]) {
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage); m.count = 0; m.frustumCulled = false; escena.add(m);
  }
}

function nuevoCarro(e, tipo = "normal", destino = null, s = 0) {
  const carril = Math.floor(Math.random() * e.carriles);
  if (e.ocup.some(c => c.carril === carril && Math.abs(c.s - s) < LARGO_CARRO + 4)) return null;
  if (luzPara(e) && e.largo - s < 25) return null;   // no aparecer encima de una línea de pare
  const carro = { e, carril, s, v: e.vmax * 0.6, tipo, destino, ruta: null,
    color: tipo === "ambulancia" ? 0xffffff : tipo === "policia" ? 0x1e4bff : COLORES_CARRO[Math.floor(Math.random() * COLORES_CARRO.length)],
    pac: 0.8 + Math.random() * 0.4 };
  if (destino != null) carro.ruta = rutaHacia(e.hasta, destino);
  e.ocup.push(carro); estado.carros.push(carro);
  return carro;
}

function rutaHacia(origen, nodosDestino) {
  // Dijkstra sobre el grafo dirigido hasta cualquiera de los nodos destino
  const dist = new Map([[origen, 0]]), prev = new Map(), abiertos = [[0, origen]];
  while (abiertos.length) {
    abiertos.sort((a, b) => a[0] - b[0]);
    const [d, n] = abiertos.shift();
    if (nodosDestino.has(n)) {
      const ruta = []; let x = n;
      while (prev.has(x)) { const e = prev.get(x); ruta.unshift(e); x = e.desde; }
      return ruta;
    }
    if (d > (dist.get(n) ?? Infinity)) continue;
    for (const e of salientes[n] || []) {
      const nd = d + e.largo;
      if (nd < (dist.get(e.hasta) ?? Infinity)) { dist.set(e.hasta, nd); prev.set(e.hasta, e); abiertos.push([nd, e.hasta]); }
    }
  }
  return null;
}

function siguienteArista(carro) {
  if (carro.ruta && carro.ruta.length) return carro.ruta.shift();
  const op = (salientes[carro.e.hasta] || []).filter(x => x.base !== carro.e.base || x.unico);
  if (!op.length) return null;
  const libres = op.filter(x => !estado.accidentes.some(a => a.e === x));
  const lista = libres.length ? libres : op;
  // preferencia por seguir por la misma vía
  const misma = lista.filter(x => x.nombre && x.nombre === carro.e.nombre);
  if (misma.length && Math.random() < 0.65) return misma[Math.floor(Math.random() * misma.length)];
  return lista[Math.floor(Math.random() * lista.length)];
}

function luzPara(e) {
  const ctl = control[e.id];
  if (!ctl) return null;
  const est = estado.semaforos[ctl.cruce];
  return est ? est[ctl.grupo] : null;
}

function pasoCarros(dt) {
  for (const e of aristasDir) if (e.ocup.length > 1) e.ocup.sort((a, b) => b.s - a.s);
  const quitar = [];
  for (const carro of estado.carros) {
    if (carro.tipo === "chocado") continue;
    const e = carro.e;
    if (!carro.sig && e.largo - carro.s < 40) carro.sig = siguienteArista(carro) || "fin";
    // 1) distancia libre al vehículo de adelante (mismo carril)
    let libre = Infinity;
    for (const o of e.ocup) if (o !== carro && o.carril === carro.carril && o.s > carro.s) libre = Math.min(libre, o.s - carro.s - LARGO_CARRO);
    if (libre === Infinity && carro.sig && carro.sig !== "fin") {
      const sig = carro.sig, cs = Math.min(carro.carril, sig.carriles - 1);
      for (const o of sig.ocup) if (o.carril === cs) libre = Math.min(libre, e.largo - carro.s + o.s - LARGO_CARRO);
    }
    // 2) accidente en este carril
    for (const a of estado.accidentes) if (a.e === e && a.carril === carro.carril && a.s > carro.s) libre = Math.min(libre, a.s - carro.s - LARGO_CARRO - 1.5);
    // 3) semáforo al final de la arista
    const luz = luzPara(e);
    const hastaFin = e.largo - carro.s;
    if (luz && carro.tipo !== "ambulancia" && (luz === "rojo" || (luz === "amarillo" && hastaFin > 10)))
      libre = Math.min(libre, hastaFin - 3);
    // 4) velocidad objetivo (seguidor simple tipo IDM)
    const vmax = (carro.tipo === "ambulancia" || carro.tipo === "policia" ? e.vmax * 1.4 : e.vmax) * carro.pac;
    const objetivo = Math.min(vmax, Math.sqrt(Math.max(0, 2 * 5 * Math.max(0, libre - 1))));
    carro.v += Math.max(-9 * dt, Math.min(2.6 * dt, objetivo - carro.v));
    carro.v = Math.max(0, carro.v);
    carro.s += carro.v * dt;
    // cambio de carril si hay accidente adelante
    if (e.carriles > 1 && estado.accidentes.some(a => a.e === e && a.carril === carro.carril && a.s - carro.s < 30 && a.s > carro.s))
      carro.carril = (carro.carril + 1) % e.carriles;
    // 5) paso a la siguiente arista
    if (carro.s >= e.largo) {
      const sig = carro.sig === "fin" || !carro.sig ? null : carro.sig;
      e.ocup.splice(e.ocup.indexOf(carro), 1);
      if (!sig) { quitar.push(carro); continue; }
      carro.s -= e.largo; carro.e = sig; carro.sig = null;
      carro.carril = Math.min(carro.carril, sig.carriles - 1);
      sig.ocup.push(carro);
      if (carro.destino && carro.ruta && carro.ruta.length === 0) { carro.destino = null; carro.ruta = null; }
    }
  }
  for (const c of quitar) estado.carros.splice(estado.carros.indexOf(c), 1);
  // población objetivo
  const objetivo = estado.congestionHasta > tiempo ? estado.objetivoCarros * 2 : estado.objetivoCarros;
  const normales = estado.carros.filter(c => c.tipo === "normal").length;
  // Mantener la población: al inicio se llena toda la red; después entran por los bordes
  // y, si hace falta, aparecen en tramos libres (la red de Bocagrande tiene muchas salidas)
  const falta = Math.min(objetivo, MAX_CARROS - 40) - normales;
  const intentos = tiempo < 1 ? falta : Math.min(falta, 4);
  for (let i = 0; i < intentos; i++) {
    if (entradas.length && Math.random() < 0.4) nuevoCarro(entradas[Math.floor(Math.random() * entradas.length)]);
    else {
      const e = aristasDir[Math.floor(Math.random() * aristasDir.length)];
      if (e.largo > 20) nuevoCarro(e, "normal", null, Math.random() * (e.largo - 10));
    }
  }
}

// ---------------- Peatones: caminan por los andenes ----------------
function nuevoPeaton(e = null, s = null) {
  e = e || aristasDir[Math.floor(Math.random() * aristasDir.length)];
  const p = { e, s: s ?? Math.random() * e.largo, v: 1.1 + Math.random() * 0.6,
    lado: Math.random() < 0.5 ? 1 : -1, color: Math.random() < 0.5 ? 0xff2bd6 : 0x00e5ff, espera: 0 };
  estado.peatones.push(p);
  return p;
}

function cruceDe(nodo) { return estado.cruces.find(c => c.nodosSet.has(nodo)); }

function pasoPeatones(dt) {
  for (const p of estado.peatones) {
    if (p.s < p.e.largo) { p.s += p.v * dt; p.espera = 0; continue; }
    const c = cruceDe(p.e.hasta);
    if (c) {
      // cruza la otra vía: solo si su semáforo está en rojo (o todo en rojo / cerco)
      const est = estado.semaforos[c.id];
      const grupo = p.e.nombre === c.via_principal ? "B" : "A";
      if (est && est[grupo] !== "rojo") { p.espera += dt; continue; }
    }
    const op = (salientes[p.e.hasta] || []).filter(x => x.base !== p.e.base);
    const sig = op.length ? op[Math.floor(Math.random() * op.length)] : (salientes[p.e.hasta] || [])[0];
    if (!sig) { p.s = 0; continue; }
    p.e = sig; p.s = 0;
  }
  const vencidas = estado.aglomeraciones.filter(a => a.hasta <= tiempo);
  if (vencidas.length) {
    estado.peatones = estado.peatones.filter(p => !vencidas.includes(p.grupo));
    estado.aglomeraciones = estado.aglomeraciones.filter(a => a.hasta > tiempo);
  }
  let normales = estado.peatones.filter(p => !p.grupo).length;
  while (normales++ < estado.objetivoPeatones && estado.peatones.length < MAX_PEATONES) nuevoPeaton();
}

// =====================================================================
// 5. Eventos del SMA
// =====================================================================
function cruceObj(id) { return estado.cruces.find(c => c.id === id); }

export function eventoAccidente(id) {
  const c = cruceObj(id); if (!c) return;
  const accesos = Object.entries(control).filter(([, ctl]) => ctl.cruce === id).map(([k, ctl]) => ({ e: aristasDir[k], ctl }));
  const acc = accesos.find(a => a.ctl.grupo === "B" && a.e.largo > 30) || accesos.find(a => a.e.largo > 30) || accesos[0];
  if (!acc) return;
  const s = Math.max(8, acc.e.largo - 22);
  const accidente = { cruce: id, e: acc.e, carril: 0, s, hasta: tiempo + 60, carros: [] };
  for (const d of [0, LARGO_CARRO - 1]) {
    const choc = { e: acc.e, carril: 0, s: s + d, v: 0, tipo: "chocado", color: 0xff6d00 };
    acc.e.ocup.push(choc); estado.carros.push(choc); accidente.carros.push(choc);
  }
  estado.accidentes.push(accidente);
  // curiosos alrededor
  aglomerar(id, 18, 45);
}

export function terminarAccidente(id) {
  for (const a of estado.accidentes.filter(a => a.cruce === id)) {
    for (const ch of a.carros) { a.e.ocup.splice(a.e.ocup.indexOf(ch), 1); estado.carros.splice(estado.carros.indexOf(ch), 1); }
  }
  estado.accidentes = estado.accidentes.filter(a => a.cruce !== id);
}

export function enviarEmergencia(id, tipo) {
  const c = cruceObj(id); if (!c || !entradas.length) return;
  const n = tipo === "policia" ? 2 : 1;
  // entradas ordenadas por la longitud de su ruta hasta el cruce (la más cercana primero)
  const opciones = entradas.map(e => ({ e, ruta: rutaHacia(e.hasta, c.nodosSet) }))
    .filter(o => o.ruta).map(o => ({ ...o, largo: o.e.largo + o.ruta.reduce((s, x) => s + x.largo, 0) }))
    .sort((a, b) => a.largo - b.largo);
  let enviados = 0;
  for (const o of opciones) {
    if (enviados >= n) break;
    if (nuevoCarro(o.e, tipo, c.nodosSet)) enviados++;
  }
}

export function aglomerar(id, n = 60, segundos = 40) {
  const c = cruceObj(id); if (!c) return;
  const cerca = aristasDir.filter(e => c.nodosSet.has(e.hasta) || c.nodosSet.has(e.desde));
  const grupo = { n, hasta: tiempo + segundos };
  for (let i = 0; i < n && estado.peatones.length < MAX_PEATONES && cerca.length; i++) {
    const e = cerca[Math.floor(Math.random() * cerca.length)];
    const p = nuevoPeaton(e, c.nodosSet.has(e.hasta) ? Math.max(0, e.largo - Math.random() * 25) : Math.random() * 25);
    p.grupo = grupo; p.color = 0xffd000; p.v *= 0.5;
  }
  estado.aglomeraciones.push(grupo);
}

export function congestion(segundos = 45) { estado.congestionHasta = tiempo + segundos; }

// =====================================================================
// 6. Bucle principal
// =====================================================================
function dibujar() {
  let i = 0, b = 0;
  const destello = Math.floor(tiempo * 4) % 2 === 0;
  for (const c of estado.carros) {
    const lat = lateralCarril(c.e, c.carril);
    const p = puntoEn(c.e, Math.min(c.s, c.e.largo), lat);
    tmp.position.set(p.x, 0, p.z);
    tmp.rotation.set(0, p.ang + (c.tipo === "chocado" ? 0.5 * (c.s % 2 ? 1 : -1) : 0), 0);
    tmp.scale.setScalar(ESC_CARRO);
    tmp.updateMatrix();
    mallaCarros.setMatrixAt(i, tmp.matrix); mallaFarosD.setMatrixAt(i, tmp.matrix); mallaFarosT.setMatrixAt(i, tmp.matrix);
    let col = c.color;
    if (c.tipo === "chocado") col = destello ? 0xff6d00 : 0x331100;
    mallaCarros.setColorAt(i, colorTmp.setHex(col));
    // luces de freno más intensas al detenerse
    mallaFarosT.setColorAt(i, colorTmp.setHex(c.v < 1 ? 0xff1744 : 0x661018));
    if ((c.tipo === "ambulancia" || c.tipo === "policia") && b < 60) {
      mallaBalizas.setMatrixAt(b, tmp.matrix);
      mallaBalizas.setColorAt(b++, colorTmp.setHex(destello ? 0xff1744 : 0x2962ff));
    }
    i++;
  }
  for (const m of [mallaCarros, mallaFarosD, mallaFarosT]) { m.count = i; m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; }
  mallaBalizas.count = b; mallaBalizas.instanceMatrix.needsUpdate = true; if (mallaBalizas.instanceColor) mallaBalizas.instanceColor.needsUpdate = true;

  let j = 0;
  for (const p of estado.peatones) {
    const lat = (anchoVia(mapa.aristas[p.e.base]) / 2 + 1.6) * p.lado * (p.e.desde === mapa.aristas[p.e.base].a ? 1 : -1);
    const q = puntoEn(p.e, Math.min(p.s, p.e.largo), lat);
    tmp.position.set(q.x, Math.abs(Math.sin(tiempo * 6 + j)) * 0.25, q.z); tmp.rotation.set(0, q.ang, 0); tmp.scale.setScalar(ESC_PEATON); tmp.updateMatrix();
    mallaPeatones.setMatrixAt(j, tmp.matrix);
    mallaPeatones.setColorAt(j++, colorTmp.setHex(p.espera > 0 ? 0xffffff : p.color));
  }
  tmp.scale.setScalar(1);
  mallaPeatones.count = j; mallaPeatones.instanceMatrix.needsUpdate = true; if (mallaPeatones.instanceColor) mallaPeatones.instanceColor.needsUpdate = true;

  // anillos: pulso según modo
  for (const [id, a] of Object.entries(anillos)) {
    const modo = estado.semaforos[id]?.modo;
    const k = modo === "cerco" ? 1 + 0.25 * Math.sin(tiempo * 8) : modo === "corredor" ? 1 + 0.1 * Math.sin(tiempo * 4) : 1;
    a.anillo.scale.setScalar(k);
    a.anillo.material.opacity = estado.accidentes.some(x => x.cruce === id) && destello ? 1 : 0.8;
  }
}

function medir() {
  const m = estado.metricas, normales = estado.carros.filter(c => c.tipo !== "chocado");
  m.carros = normales.length;
  m.vel = normales.length ? normales.reduce((s, c) => s + c.v, 0) / normales.length * 3.6 : 0;
  m.detenidos = normales.filter(c => c.v < 0.8).length;
  m.peatones = estado.peatones.length;
  m.esperando = estado.peatones.filter(p => p.espera > 0).length;
  m.colas = {};
  for (const c of estado.cruces) m.colas[c.id] = 0;
  for (const car of normales) {
    const ctl = control[car.e.id];
    if (ctl && car.v < 0.8 && car.e.largo - car.s < 80) m.colas[ctl.cruce]++;
  }
}

let acumMedir = 0, cuadros = 0, acumFps = 0, lentos = 0;
function cuadro() {
  const real = reloj.getDelta();
  // la simulación avanza en pasos de máx. 50 ms (hasta 4 por cuadro) para seguir en tiempo real con pocos FPS
  let resto = Math.min(real, 0.2);
  const t0 = performance.now();
  while (resto > 0) {
    const dt = Math.min(0.05, resto); resto -= dt;
    tiempo += dt;
    pasoCarros(dt);
    pasoPeatones(dt);
  }
  for (const a of estado.accidentes) if (a.hasta < tiempo) terminarAccidente(a.cruce);
  dibujar();
  const t1 = performance.now();
  if ((acumMedir += real) > 0.5) { acumMedir = 0; medir(); }
  controles.update();
  if (estado.bloom) compositor.render(); else render.render(escena, camara);
  etiquetas.render(escena, camara);
  estado.ms = { sim: +(t1 - t0).toFixed(1), dibujo: +(performance.now() - t1).toFixed(1) };
  // calidad automática: si el equipo no llega a ~24 FPS se apaga el brillo (bloom)
  cuadros++; acumFps += real;
  if (acumFps > 3) {
    estado.fps = Math.round(cuadros / acumFps);
    if (estado.bloom && estado.fps < 24 && document.hasFocus() && ++lentos >= 2) estado.bloom = false;
    cuadros = 0; acumFps = 0;
  }
}

// =====================================================================
// 7. Selección y cámara
// =====================================================================
const rayo = new THREE.Raycaster();
function clicEscena(ev) {
  const r = render.domElement.getBoundingClientRect();
  rayo.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), camara);
  const hit = rayo.intersectObjects(Object.values(anillos).map(a => a.anillo))[0];
  if (hit) seleccionar(hit.object.userData.cruce);
}

export function seleccionar(id, volar = true) {
  for (const [k, a] of Object.entries(anillos)) a.div.classList.toggle("sel", k === id);
  const c = cruceObj(id);
  if (c && volar) volarA(c.x, c.z, 170);
  estado.alSeleccionar?.(id);
}

export function volarA(x, z, dist = 700) {
  const desde = controles.target.clone(), hasta = new THREE.Vector3(x, 0, z);
  const off = camara.position.clone().sub(controles.target).setLength(dist);
  const camDesde = camara.position.clone(), camHasta = hasta.clone().add(off);
  let t = 0;
  const paso = () => {
    t = Math.min(1, t + 0.035); const k = t * t * (3 - 2 * t);
    controles.target.lerpVectors(desde, hasta, k); camara.position.lerpVectors(camDesde, camHasta, k);
    if (t < 1) requestAnimationFrame(paso);
  };
  paso();
}

export function vistaGeneral() { volarA(0, 0, 1000); }
