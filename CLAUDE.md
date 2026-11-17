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
| AgenteCoordinador | basado en utilidad | Consulta al Consultor, genera planes, elige el de mayor utilidad, ordena al Semafórico y al Comunicador, restaura el plan normal a los 10 s |
| AgenteConsultor | reactivo basado en modelos | Responde `query-ref` (estado vial) con caché |
| AgentePredictor | híbrido: basado en modelos + utilidad | Responde `predecirRutaHuida` (por ahora reglas simples) |
| AgenteSemaforico | basado en objetivos | Traduce órdenes a MQTT: `abrirCorredorEmergencia`→`corredor`, `restaurarPlanNormal`→`normal`, `activarCerco`→`cerco` |
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
| `sma/semaforo/<cruce>` | AgenteSemaforico | ESP32 / simulador | `normal` \| `corredor` \| `cerco` (compat.: `verde`=corredor, `rojo`=normal) |
| `sma/estado/<cruce>` | ESP32 / simulador | Panel | JSON **retenido** `{"modo":"normal","A":"verde","B":"rojo"}` |

- `<cruce>` = `cruce1`, `cruce2`, … `cruce1` es el **cruce físico** del ESP32.
- Semáforo **A** = vía principal, **B** = vía secundaria.
- Ciclo normal: A verde 8 s → A amarillo 2 s → todo rojo 1 s → B verde 6 s → B amarillo 2 s → todo rojo 1 s.
- `corredor`: A verde fijo, B rojo. `cerco`: A y B en rojo.

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

- **Caso 1 – Accidente → corredor de emergencia** (YA FUNCIONA de punta a punta con el ESP32):
  evento → Vigilancia → Clasificador (Movilidad, Alta) → Coordinador consulta estado vial y elige plan por
  utilidad → Semafórico pone `corredor` → Comunicador avisa a la ciudadanía → a los 10 s `normal`.
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
│   └── sma_agentes.py
├── esp32/
│   ├── esp32_mqtt_prueba/esp32_mqtt_prueba.ino   (prueba del LED azul)
│   └── esp32_cruce/esp32_cruce.ino               (cruce con 2 semáforos + botón en GPIO4)
├── maqueta_virtual/
│   ├── cruces.json            (8 cruces reales, coordenadas OSM)
│   ├── simulador_ciudad.py
│   └── panel/                 (servidor_panel.py + index.html)
└── docs/                      (diagramas .drawio/.pdf, documento Word, checklist, informe)
```

Pines del ESP32 (cruce1): A rojo/amarillo/verde = GPIO 25/26/27, B = 32/33/13, botón = GPIO4 a GND (INPUT_PULLUP),
LED azul GPIO2 = conectado a MQTT.

## 7. Estado del proyecto

Hecho: diseño (diagrama de clases v4, diagramas de secuencia de los 2 casos, catálogo de mensajes), documento v3
corregido, Mosquitto + ESP32 conectados, 7 agentes SPADE funcionando con el Caso 1, código del cruce físico
(sin LEDs todavía: solo hay ESP32, faltan componentes). **Maqueta virtual terminada y probada (16-nov-2026)**:
8 cruces, simulador, panel web con mapa y bitácora; `log()` publica en `sma/panel/log`.

Pendiente (en orden): **Caso 2** (reconocimiento facial con DeepFace o face_recognition +
cerco) → bot de Telegram para el Comunicador (+ LLM local con Ollama o Gemini gratis) → mediciones y resultados → informe.

Ojo para el Caso 2: hoy `AgenteCoordinador.ResponderAccidente` abre **siempre** corredor y avisa a la Ciudadanía,
sin importar el tipo de alerta. Una alerta `persona_solicitada` debe ir por otro plan (Predictor → `activarCerco` en
la ruta → aviso solo a la Policía). En el panel el botón "Simular persona solicitada" está deshabilitado hasta entonces.

## 8. Maqueta virtual (HECHA — especificación original, se mantiene como referencia)

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
