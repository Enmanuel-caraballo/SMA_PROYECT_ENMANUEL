# CLAUDE.md — Proyecto SMA Movilidad y Seguridad Cartagena

> Contexto para Claude Code. Léelo completo antes de cambiar código.
> Idioma del proyecto: **español** (código, comentarios, mensajes de consola y commits).

## 1. Qué es el proyecto

Proyecto final de la asignatura **Inteligencia Artificial** (Ingeniería de Sistemas, UNICOLOMBO, Cartagena).
Es un **Sistema Multiagente (SMA)** que ataca dos problemas de Cartagena:

- **Movilidad**: trancones, accidentes, tiempos de semáforo fijos.
- **Seguridad**: hurtos, vehículos/personas solicitadas por la ley.

El SMA se demuestra **a escala**: una **maqueta física** (ESP32 con un cruce de 2 semáforos) y una
**maqueta virtual** (mapa de Cartagena en el navegador con varios cruces). La maqueta virtual es
la tarea actual (ver sección 8).

- Repositorio (privado): https://github.com/Enmanuel-caraballo/SMA_PROYECT_ENMANUEL
- Equipo: Enmanuel (líder técnico), Omar, Mauricio, Yohan, Deyker.
- Fechas: primer avance **jueves 19-nov-2026**; entrega final aprox. **jueves 26-nov-2026**.
- Todo debe ser **gratuito** y correr **en local** (un portátil Windows). Nada de servicios de pago.

## 2. Arquitectura

```
 ESP32 (cruce1 real) ──MQTT──┐                         ┌── Panel web (maqueta virtual)
 Simulador de ciudad ─MQTT───┤   Mosquitto :1883       │
 (cruces virtuales)          └────────┬────────────────┘
                                      │ MQTT
                 ┌────────────────────┴───────────────────┐
                 │  sma_agentes.py  (7 agentes SPADE)     │
                 │  mensajes FIPA-ACL por XMPP embebido   │
                 └────────────────────────────────────────┘
```

### Los 7 agentes (archivo `agentes/sma_agentes.py`)

| Agente | Tipo (Russell & Norvig) | Rol en el código |
|---|---|---|
| AgenteVigilancia | reactivo simple | Escucha `sma/eventos/#` por MQTT y envía `AlertaNovedad` al Clasificador |
| AgenteClasificadorAlertas | basado en objetivos | Tabla `REGLAS`: tipo de evento → (TipoAlerta, NivelPrioridad). Envía `Alerta` al Coordinador |
| AgenteCoordinador | basado en utilidad | Consulta al Consultor, genera planes de desvío (`generar_planes`), elige el de mayor utilidad, ordena al Semafórico (`activarPlan`) y al Comunicador, mantiene el plan `SEGUNDOS_PLAN`=300 s (`--duracion N`) y restaura el normal |
| AgenteConsultor | reactivo basado en modelos | Modelo del tráfico con los sensores `sma/sensores/<cruce>`: responde `consultarEstadoVial` (nivel por umbrales) y `consultarColaMedia` (historia 15 min), con caché de 5 s |
| AgentePredictor | híbrido: basado en modelos + utilidad | Responde `predecirRutaHuida` (por ahora reglas simples) |
| AgenteSemaforico | objetivos + **Q-learning** | Control adaptativo cada 12 s: con las colas A/B decide `normal`/`prioridad_a`/`prioridad_b` y aprende (tabla Q en `agentes/memoria/`); los modos del plan son sugerencias (`cerco`/`peatonal` se respetan). `activarPlan` trae un modo por cruce y lo publica en `sma/semaforo/<cruce>`, además publica el plan en `sma/plan/<cruce>`; `restaurarPlanNormal`→`normal` (se conservan `abrirCorredorEmergencia`→`corredor` y `activarCerco`→`cerco`) |
| AgenteComunicador | basado en objetivos | Redacta el aviso (por ahora lo imprime; luego Telegram + LLM) |

Enumeraciones del dominio: `TipoAlerta {Movilidad, Seguridad, Mixta}`,
`NivelPrioridad {Baja, Media, Alta, Critica}`, `ObjetivoSemaforico {Fluidez, CorredorEmergencia, CercoPersecucion}`.

### Detalles técnicos que ya funcionan (no romper)

- **SPADE 4.1.x** con servidor XMPP embebido: `spade.run(main(), embedded_xmpp_server=True)`.
  JIDs `nombre@localhost`, clave `sma123`, `auto_register=True`.
- En Windows con Python 3.13 hace falta `pip install greenlet` (ya está en `requirements.txt`).
- **paho-mqtt 2.x**: `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=...)`.
- Los callbacks de paho corren en otro hilo: pasar datos a asyncio con `loop.call_soon_threadsafe(cola.put_nowait, dato)`.
- Mensajes entre agentes: metadata `performative`, `ontology="sma-ctg"`, `action=<acción>`, cuerpo **JSON**.
  Helpers `mensaje()` y `plantilla()` en `sma_agentes.py`.
- `python agentes/sma_agentes.py --simular` dispara un accidente de prueba sin ESP32.

## 3. Contrato MQTT (broker Mosquitto en `localhost:1883`)

| Tema | Quién publica | Quién escucha | Contenido |
|---|---|---|---|
| `sma/eventos/<cruce>` | ESP32 (botón), simulador, panel | AgenteVigilancia | texto (`accidente`) o JSON `{"tipo": "...", ...}` |
| `sma/semaforo/<cruce>` | AgenteSemaforico | ESP32 / simulador | `normal` \| `prioridad_a` \| `prioridad_b` \| `peatonal` \| `cerco` \| `corredor` (compat.: `verde`=corredor, `rojo`=normal) |
| `sma/sensores/<cruce>` | Panel (lo mide el gemelo 3D, vía WebSocket) | Semafórico, Consultor | JSON `{"A": n, "B": n}` cada 2 s |
| `sma/agentes/<agente>` | cada agente | Panel `/agentes` | JSON **retenido**: tipo, reglas, parámetros, estado interno |
| `sma/plan/<cruce>` | AgenteSemaforico | Panel (gemelo 3D) | JSON `{"plan","nombre","semaforos","alerta","cruce","evento","sentido","duracion","activo"}` |
| `sma/estado/<cruce>` | ESP32 / simulador | Panel | JSON **retenido** `{"modo":"normal","A":"verde","B":"rojo"}` |

- `<cruce>` = `cruce1`, `cruce2`, … `cruce1` es el **cruce físico** del ESP32.
- Semáforo **A** = vía principal, **B** = vía secundaria.
- Ciclo normal: A verde 8 s → A amarillo 2 s → todo rojo 1 s → B verde 6 s → B amarillo 2 s → todo rojo 1 s.
- `corredor`: A verde fijo, B rojo. `cerco`: A y B en rojo.
- Planes de desvío (mismas 6 fases, otros tiempos): `prioridad_a` A 20 s / B 5 s; `prioridad_b` A 5 s / B 20 s;
  `peatonal` todo rojo de 8 s en cada cambio.
- Eventos: `accidente` en el cruce5 puede traer `{"tipo":"accidente","sentido":"salida"|"entrada"}`.

## 4. Catálogo de mensajes entre agentes (resumen)

| # | Emisor → Receptor | Performativa | Acción | Ejemplo JSON |
|---|---|---|---|---|
| 2 | Vigilancia → Clasificador | inform | AlertaNovedad | `{"tipo":"accidente","ubicacion":"cruce1","ts":"..."}` |
| 3 | Vigilancia → Consultor | query-ref | consultarPersonaSolicitada | `{"embedding":[...],"camara":"cam2"}` |
| 4 | Consultor → Vigilancia | inform | Coincidencia | `{"coincide":true,"similitud":0.93,"persona":{...}}` |
| 5 | Clasificador → Coordinador | inform | Alerta | `{"id":"AL-0001","tipo":"Seguridad","prioridad":"Critica",...}` |
| 6–7 | Coordinador ↔ Consultor | query-ref / inform | consultarEstadoVial / EstadoTrafico | `{"sector":"cruce1"}` / `{"nivel":"alto"}` |
| 8–9 | Coordinador ↔ Predictor | request / inform | predecirRutaHuida / Ruta | `{"ubicacion":"cam2"}` / `{"cruces":["cruce2","cruce1"],"confianza":0.7}` |
| 10–12 | Coordinador → Semafórico | request | abrirCorredorEmergencia / activarCerco / restaurarPlanNormal | `{"ruta":["cruce1"]}` |
| 14 | Semafórico → Coordinador | inform | ResultadoAccion | `{"accion":"...","estado":"ok"}` |
| 15 | Coordinador → Comunicador | request | comunicar / notificarPolicia | `{"receptor":"Ciudadania"|"Policia",...}` |

## 5. Casos de demostración

- **Caso 1 – Accidente → plan de desvío** (YA FUNCIONA de punta a punta con el ESP32 y el gemelo 3D):
  evento → Vigilancia → Clasificador (Movilidad, Alta) → Coordinador consulta estado vial y elige plan por
  utilidad → Semafórico aplica los modos y publica el plan → Comunicador avisa a la ciudadanía → a los 5 min `normal`.
  **En Cartagena no hay corredores de emergencia** (decisión del 16-nov): el plan fuerte es el **contraflujo de la
  Carrera 1** cuando el accidente bloquea la única salida/entrada de Bocagrande (cruce5, Av. San Martín × Calle 15):
  sentido salida → desde el cruce4 todo sale por Calle 10 → Carrera 1 (solo salida, 2 carriles) y el ingreso solo por la
  Av. San Martín; sentido entrada → ingreso solo por la Carrera 1 y la Av. San Martín para salir.
- **Caso 2 – Persona solicitada → cerco semafórico** (PENDIENTE):
  cámara detecta rostro → Consultor confirma coincidencia → alerta Crítica → Predictor da ruta de huida →
  Semafórico pone `cerco` en **todos los cruces de la ruta** → Comunicador avisa **solo a la Policía**.
  Reglas éticas obligatorias: los "solicitados" son compañeros que dieron consentimiento; los rostros que no
  coinciden se descartan sin guardarse (Ley 1581 de 2012); la alerta dice "posible coincidencia"; nunca se
  notifica a la ciudadanía.

## 6. Estructura del repositorio

```
SMA_IA/
├── CLAUDE.md                  ← este archivo
├── README.md
├── COMANDOS.txt               ← cómo instalar, correr, probar y subir a Git
├── requirements.txt
├── .gitignore
├── agentes/
│   ├── sma_agentes.py         (los 7 agentes)
│   ├── conocimiento.py        (TODAS las reglas y parámetros)
│   ├── aprendizaje.py         (Q-learning del Semafórico, experiencia del Coordinador)
│   └── memoria/               (lo aprendido, *.json; se versiona en Git desde el 17-nov; --olvidar lo reinicia)
├── esp32/
│   ├── esp32_mqtt_prueba/esp32_mqtt_prueba.ino   (prueba del LED azul)
│   └── esp32_cruce/esp32_cruce.ino               (cruce con 2 semáforos + botón en GPIO4)
├── maqueta_virtual/
│   ├── cruces.json            (5 cruces semaforizados de Bocagrande, generado desde OSM)
│   ├── bocagrande/bocagrande.json (red vial, edificios, semáforos en metros; generado)
│   ├── preparar_bocagrande.py (descarga OSM/Overpass y genera los dos JSON)
│   ├── simulador_ciudad.py
│   └── panel/                 (servidor_panel.py, index.html = vista 3D, static/bocagrande3d.js, mapa2d.html)
└── docs/                      (diagramas .drawio/.pdf, documento Word, checklist, informe)
```

Pines del ESP32 (cruce1): A rojo/amarillo/verde = GPIO 25/26/27, B = 32/33/13, botón = GPIO4 a GND (INPUT_PULLUP),
LED azul GPIO2 = conectado a MQTT.

## 7. Estado del proyecto

Hecho: diseño (diagrama de clases v4, diagramas de secuencia de los 2 casos, catálogo de mensajes), documento v3
corregido, Mosquitto + ESP32 conectados, 7 agentes SPADE funcionando con el Caso 1, código del cruce físico
(sin LEDs todavía: solo hay ESP32, faltan componentes). **Maqueta virtual terminada y probada (16-nov-2026)**.
**Decisión 16-nov-2026: todo el proyecto se concentra en BOCAGRANDE.** La maqueta virtual es un gemelo digital 3D
(Three.js) con datos reales de OSM: 5 cruces semaforizados, carros y peatones simulados en el navegador que obedecen
los semáforos y reaccionan a eventos (accidente, congestión, aglomeración, cerco). Ver sección 9.

Pendiente (en orden): **Caso 2** (reconocimiento facial con DeepFace o face_recognition +
cerco) → bot de Telegram para el Comunicador (+ LLM local con Ollama o Gemini gratis) → mediciones y resultados → informe.

Para el Caso 2: `generar_planes` ya manda las alertas de tipo Seguridad a un plan `cerco` en el cruce del evento con
aviso **solo a la Policía** ("posible coincidencia"); falta el reconocimiento facial y usar el Predictor para poner el
cerco en todos los cruces de la ruta de huida. En el panel el botón "Persona solicitada" sigue deshabilitado.

## 8. Maqueta virtual v1 (HECHA — especificación original de 8 cruces de la ciudad; reemplazada por la sección 9)

### Objetivo
Mostrar en el navegador un **mapa de Cartagena** con **6–8 cruces** cuyos semáforos cambian de color en vivo,
las **alertas** y la **conversación entre agentes**, de modo que el SMA se vea a escala de ciudad. Debe funcionar
**con o sin el ESP32 conectado** (Enmanuel aún no tiene LEDs).

### Componentes a construir
1. **`maqueta_virtual/cruces.json`**: id (`cruce1`…`cruce8`), nombre, lat, lon, vía principal/secundaria.
   Usa intersecciones **reales** de Cartagena (p. ej. Av. Pedro de Heredia, India Catalina, Av. San Martín en
   Bocagrande, Av. Santander, Castillogrande, Mamonal). **Verifica las coordenadas** en OpenStreetMap; no inventes.
   `cruce1` es el cruce físico.
2. **`maqueta_virtual/simulador_ciudad.py`**: simula los cruces virtuales **exactamente con el mismo contrato MQTT
   que el ESP32** (sección 3): escucha `sma/semaforo/<cruce>`, ejecuta el ciclo con los mismos tiempos y modos, y
   publica `sma/estado/<cruce>` retenido en cada cambio. Opción `--incluir-cruce1` para simular también el cruce
   físico cuando el ESP32 no está conectado. Así los agentes no distinguen cruces reales de virtuales.
3. **Panel web** (`maqueta_virtual/panel/`): servidor **FastAPI + uvicorn** que se suscribe a `sma/#` con paho y
   reenvía todo al navegador por **WebSocket**; página `index.html` con **Leaflet + teselas de OpenStreetMap**
   (gratis, sin API key):
   - Un marcador por cruce con sus dos semáforos (A y B) pintados con el color actual y el modo
     (normal / corredor / cerco). El cruce físico marcado como "ESP32".
   - Panel lateral con la **bitácora de agentes** en vivo y las **alertas** (id, tipo, prioridad).
   - Botones: "Simular accidente en <cruce>" (publica `sma/eventos/<cruce>` = `accidente`) y, más adelante,
     "Simular persona solicitada".
   - Abre en `http://localhost:8000`.
4. **Cambios mínimos en `agentes/sma_agentes.py`**: que la función `log()` también publique cada línea en
   `sma/panel/log` (JSON `{"ts","agente","texto"}`) para que el panel muestre la conversación. No cambiar el
   resto del comportamiento ni los temas existentes. Mantener `--simular`.

### Criterios de aceptación
- `python maqueta_virtual/simulador_ciudad.py --incluir-cruce1` + `python agentes/sma_agentes.py` +
  `python maqueta_virtual/panel/servidor_panel.py` → el mapa muestra los 8 cruces ciclando.
- Clic en "Simular accidente en cruce3" → en el mapa el cruce3 pasa a **corredor** (A verde, B rojo), la bitácora
  muestra toda la cadena de agentes y a los 10 s vuelve a **normal**.
- Con el ESP32 conectado (sin `--incluir-cruce1`), presionar el botón físico produce lo mismo sobre `cruce1`.
- Agregar las dependencias nuevas a `requirements.txt` y actualizar `COMANDOS.txt` y `README.md`.

### Convenciones
- Python 3.11–3.13, Windows (PowerShell). Rutas con `pathlib`. Sin rutas absolutas.
- Nombres y textos en español; prints con el formato `[HH:MM:SS] Agente | texto`.
- El repositorio es privado: los `.ino` llevan el WiFi real (decisión del equipo). No subir otras credenciales
  (tokens de Telegram, API keys): esas van en `.env`, que está en `.gitignore`.
- Commits en español y sin línea `Co-Authored-By`.
- Sin fotos de personas reales en el repo (los rostros de prueba van en una carpeta ignorada por Git).
- Probar cada cambio corriéndolo antes de darlo por terminado.

## 9. Gemelo digital 3D de Bocagrande (versión actual)

- **Sector único: Bocagrande.** Todo lo nuevo (Caso 2, mediciones, informe) debe ubicarse en este sector.
- Cruces (de `highway=traffic_signals` en OSM, 7 semáforos → 5 intersecciones). `cruce1` = Av. San Martín (en OSM
  "Carrera 2") × Calle 6 = cruce físico ESP32 (punto crítico citado por la DATT). cruce2 = Cra. 3 × Calle 6,
  cruce3 = San Martín × Calle 10, cruce4 = Cra. 3 × Calle 10, cruce5 = San Martín × Calle 15 (Base Naval).
- Grupo **A** de un acceso = tramos cuyo nombre es `via_principal` del cruce; el resto es **B**.
- `preparar_bocagrande.py` regenera `cruces.json` y `bocagrande/bocagrande.json` (coordenadas en metros,
  origen 10.4035, -75.5550; el norte es -z en Three.js). Si Overpass da 502/504, reintentar más tarde.
- Vista 3D: `panel/index.html` (interfaz) + `panel/static/bocagrande3d.js` (escena y simulación). Three.js 0.160.0
  por importmap desde unpkg. La simulación de tráfico corre en el navegador; las luces vienen de `sma/estado/<cruce>`.
- El servidor reenvía la historia al conectar con `"historial": true`; la vista 3D no dispara efectos con eso.
- Efectos: `sma/eventos` accidente → `eventoAccidente(cruce, sentido)` (en el cruce5 a ~100 m del cruce sobre la
  Av. San Martín); congestion → `congestion`; aglomeracion → `aglomerar`; `sma/semaforo` cerco → 2 patrullas;
  `sma/plan` activo → `activarPlan` (+ ambulancia si es accidente); inactivo → `desactivarPlan`, retira el accidente a los
  15 s y termina congestión/aglomeración.
- Contraflujo: `construirGrafo` crea tramos de **reserva** (sentido contrario, deshabilitados) para la Carrera 1 norte y la
  Calle 10 cruce3–Carrera 1; `prepararGeografia` busca los tramos por nombre/posición (no por índice). `aplicarContraflujo`
  cierra/abre tramos, presta el carril contrario en vías de doble sentido (cuando ese sentido se vacía) y `reencaminar`
  recalcula rutas (Dijkstra respeta cierres, sin vueltas en U). 20 % de los carros nuevos van a la salida vigente.
- Duración de cada evento/plan: **5 min** (decisión 16-nov; las decisiones de los agentes siguen siendo inmediatas).
- Vehículos ×1.5 y peatones ×2 de escala real a propósito (si no, no se ven junto a las torres).
- Inteligencia (decisión 17-nov): reglas/parámetros SOLO en `agentes/conocimiento.py`; aprendizaje en
  `agentes/aprendizaje.py`. El gemelo envía cada 2 s las colas por acceso (`metricas.colasGrupo`: vehículos con
  v < 2 m/s a menos de 100 m de la línea de pare) por WebSocket y el servidor las publica en `sma/sensores/<cruce>`
  (si la pestaña 3D está oculta Chrome pausa la simulación y no hay sensores). Página `/agentes` = `panel/agentes.html`.
  Entre modos cíclicos el simulador y el ESP32 conservan la fase (solo cambian los tiempos).
  Semafórico (17-nov): histéresis `MARGEN_CAMBIO`=0.3 (solo cambia si la mejor opción supera a la actual por ese
  margen); con el cruce vacío (a0-b0) queda en `normal` o en el modo del plan y no explora; la bitácora muestra la
  cola con un decimal.
- Terminar contingencia: botón "Terminar" del aviso → `POST /api/terminar` → `sma/eventos/<cruce>`
  `{"tipo":"fin_contingencia","alerta"}` → Vigilancia → Coordinador (`TerminarAlerta`). Si el SMA no avisa el cierre
  20 s después de la hora, el panel retira el plan de la vista por su cuenta.
- Depuración desde la consola: `window.__bocagrande` (cámara, controles, escena, geo, aristasDir, `avanzar(s)`) e
  `import('/static/bocagrande3d.js')` para leer `estado` (carros, peatones, métricas, planes, fps, ms).
  Ojo: `avanzar(s)` es síncrono y los semáforos no cambian mientras corre; para medir colas usar pasos cortos
  (p. ej. `avanzar(0.8)` + esperar 1 s) para que lleguen los cambios de luz por MQTT.
