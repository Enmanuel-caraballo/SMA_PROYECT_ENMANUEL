# SMA Movilidad y Seguridad · Cartagena

Proyecto final de **Inteligencia Artificial** (Ingeniería de Sistemas, UNICOLOMBO).
Sistema Multiagente (SMA) con **7 agentes SPADE** que coordinan semáforos ante incidentes de
movilidad y seguridad en **Bocagrande (Cartagena)**. Se demuestra con una **maqueta física** (ESP32, un cruce real)
y un **gemelo digital 3D** de Bocagrande en el navegador.

Equipo: Enmanuel (líder técnico), Omar, Mauricio, Yohan, Deyker.

## Sector de trabajo: Bocagrande

Todo el proyecto se concentra en **Bocagrande**. La maqueta virtual es un **gemelo digital 3D** del barrio
(Three.js) construido con datos reales de OpenStreetMap: vías con sus sentidos y carriles, 924 edificios,
senderos, pasos peatonales y los **7 semáforos registrados en OSM**, agrupados en **5 intersecciones**.
Sobre esa red circulan carros y peatones simulados que **obedecen los semáforos del SMA** y reaccionan a los eventos.

## Arquitectura

```
 ESP32 (cruce1 real) ──MQTT──┐                         ┌── Gemelo digital 3D (navegador)
 Simulador de cruces ─MQTT───┤   Mosquitto :1883       │   http://localhost:8000
 (cruce2..cruce5)            └────────┬────────────────┘   (mapa 2D en /mapa2d)
                                      │ MQTT
                 ┌────────────────────┴───────────────────┐
                 │  agentes/sma_agentes.py  (7 agentes)   │
                 │  mensajes FIPA-ACL por XMPP embebido   │
                 └────────────────────────────────────────┘
```

| Tema MQTT | Contenido |
|---|---|
| `sma/eventos/<cruce>` | `accidente` o JSON `{"tipo": ...}` (botón del ESP32, panel o `mosquitto_pub`) |
| `sma/semaforo/<cruce>` | `normal` \| `prioridad_a` \| `prioridad_b` \| `peatonal` \| `cerco` \| `corredor` (órdenes del Agente Semafórico) |
| `sma/sensores/<cruce>` | JSON `{"A": n, "B": n}`: colas por acceso medidas por el gemelo 3D cada 2 s |
| `sma/agentes/<agente>` | JSON retenido con reglas, parámetros y estado interno de cada agente (página `/agentes`) |
| `sma/plan/<cruce>` | JSON del plan activo `{"plan","nombre","semaforos","alerta","cruce","sentido","duracion","activo"}` (lo dibuja el gemelo 3D) |
| `sma/estado/<cruce>` | JSON retenido `{"modo":"normal","A":"verde","B":"rojo"}` |
| `sma/panel/log` | JSON `{"ts","agente","texto"}`: bitácora de los agentes para el panel |

## Estructura

```
agentes/sma_agentes.py                  los 7 agentes del SMA
agentes/conocimiento.py                 reglas y parámetros de todos los agentes
agentes/aprendizaje.py                  aprendizaje (Q-learning del Semafórico, experiencia del Coordinador)
esp32/esp32_cruce/                      cruce físico (2 semáforos + botón)
esp32/esp32_mqtt_prueba/                prueba de conexión (LED azul)
maqueta_virtual/cruces.json             5 cruces semaforizados de Bocagrande (generado)
maqueta_virtual/bocagrande/             bocagrande.json: red vial, edificios y semáforos en metros (generado)
maqueta_virtual/preparar_bocagrande.py  descarga OSM (Overpass) y genera los dos archivos anteriores
maqueta_virtual/simulador_ciudad.py     simula los cruces con el mismo contrato MQTT que el ESP32
maqueta_virtual/panel/                  servidor FastAPI, vista 3D (index.html + static/bocagrande3d.js) y mapa 2D
docs/                                   diagramas, documento y checklist
```

## Cruces semaforizados de Bocagrande

Fuente: nodos `highway=traffic_signals` de OpenStreetMap (nov-2026). La DATT cita como puntos críticos
la Calle 6 con San Martín y la Calle 15 frente a la Base Naval ([El Universal, 2024](https://www.eluniversal.com.co/cartagena/datt-realizo-mantenimiento-a-semaforos-en-puntos-criticos-de-movilidad-HL9973898)).

| Id | Cruce | Vía A (principal) | Vía B (secundaria) |
|---|---|---|---|
| cruce1 | Av. San Martín (Cra. 2) × Calle 6 (**ESP32**) | Carrera 2 | Calle 6 |
| cruce2 | Cra. 3 × Calle 6 | Carrera 3 | Calle 6 |
| cruce3 | Av. San Martín × Calle 10 | Av. San Martín | Calle 10 |
| cruce4 | Cra. 3 × Calle 10 | Carrera 3 | Calle 10 |
| cruce5 | Av. San Martín × Calle 15 (frente a la Base Naval) | Av. San Martín | Calle 15 |

En OSM el tramo sur de la Av. San Martín figura como "Carrera 2". Si en campo hay más semáforos que no están
en OSM, se pueden agregar en OpenStreetMap y volver a correr `preparar_bocagrande.py`.

## Planes que decide el SMA (Caso 1)

En Cartagena no existen corredores de emergencia, así que ante un evento el **Coordinador** genera planes de
**desvío** y elige el de mayor utilidad (`impacto × nivel de tráfico − 0,5 × costo`). Cada plan dura **5 min**
(`--duracion N` para cambiarlo) y luego se restaura el plan normal.

| Evento | Plan elegido | Semáforos |
|---|---|---|
| Accidente en **cruce5**, sentido **salida** (Av. San Martín ~100 m antes de la Calle 15, hacia el Centro) | **Contraflujo Carrera 1 (salida)**: desde el cruce4 todo lo que sale toma la Calle 10 hacia la Carrera 1, que queda **solo de salida** (ambos carriles); el ingreso es **solo por la Av. San Martín**. Los que ya estaban detrás del accidente pasan despacio por el carril libre. | cruce4 `prioridad_a`, cruce3 `prioridad_b`, cruce5 `prioridad_b` |
| Accidente en **cruce5**, sentido **entrada** (después de la Calle 15, hacia Bocagrande) | **Contraflujo Carrera 1 (entrada)**: el ingreso es **solo por la Carrera 1**; la Av. San Martín queda para salir. | cruce5 `prioridad_a`, cruce3 `prioridad_b` |
| Accidente en otro cruce | Desvío local: más verde para la vía libre | `prioridad_a` en ese cruce |
| Congestión | Verde largo para la vía principal | `prioridad_a` |
| Aglomeración | Fase peatonal extendida (todo rojo de 8 s en cada cambio) | `peatonal` |
| Persona solicitada / placa (Caso 2) | Cerco semafórico; aviso **solo a la Policía** ("posible coincidencia") | `cerco` |

Modos de semáforo (ESP32 y simulador): `normal` (A 8 s / B 6 s), `prioridad_a` (A 20 s / B 5 s),
`prioridad_b` (A 5 s / B 20 s), `peatonal`, `cerco` (todo rojo) y `corredor` (se conserva por compatibilidad).

## Inteligencia de los agentes: reglas, decisiones y aprendizaje

Todo se ve en vivo en **http://localhost:8000/agentes** ("Mente de los agentes"): qué percibe cada agente, sus reglas,
sus parámetros, la decisión que tomó y por qué, y lo que lleva aprendido.

| Dónde | Qué hay |
|---|---|
| `agentes/conocimiento.py` | **Todas** las reglas y parámetros: tabla del Clasificador, umbrales del Consultor, catálogo de planes (impacto, costo), fórmula de utilidad, parámetros de Q-learning del Semafórico. |
| `agentes/aprendizaje.py` | `QSemaforo` (Q-learning) y `ExperienciaPlanes` (impacto real de cada plan). |
| `agentes/memoria/*.json` | Lo aprendido; se conserva entre ejecuciones (`--olvidar` empieza de cero). No va a Git. |

- **Agente Semafórico: decide por su cuenta y aprende con Q-learning.** Cada 12 s mira las colas de cada acceso
  (sensores del gemelo, `sma/sensores/<cruce>`) y decide `normal`, `prioridad_a` o `prioridad_b`. El estado es la
  cubeta de cola en A y en B (0, 1-3, 4-7, 8-12, 13+). La recompensa es cuánto bajó la cola total menos una
  penalización por cambiar de modo. Empieza con la regla experta ("dar prioridad al acceso más cargado") y la corrige
  con la experiencia. Los modos de un plan del Coordinador son **sugerencias**: si las colas dicen otra cosa, el
  Semafórico prioriza el acceso cargado ("las colas pesan más que el plan"). No toca `cerco` ni `peatonal`.
- **Agente Coordinador: decide por utilidad y aprende el impacto real de cada plan.** Cada plan tiene un impacto
  esperado y un costo; utilidad = impacto × factor de tráfico − 0,5 × costo. Al cerrar un plan mide las colas de todo
  el sector antes y durante el plan y corrige el impacto (sube si Bocagrande fluyó mejor, baja si fluyó peor).
- **Agente Consultor: modelo del tráfico.** Calcula el nivel (bajo/medio/alto) con las colas reales de los sensores y
  guarda 15 min de historia para que el Coordinador mida el efecto de sus planes.
- **Terminar una contingencia antes de tiempo:** botón **Terminar** en el aviso del plan (o
  `sma/eventos/<cruce>` = `{"tipo":"fin_contingencia","alerta":"AL-0001"}`).

## Gemelo digital 3D

- **Vehículos**: siguen la red real (sentidos únicos incluidos), mantienen distancia y frenan en la línea de pare
  según la luz de su acceso (A o B). Luces de freno al detenerse.
- **Peatones**: caminan por los andenes y cruzan cuando la vía que atraviesan está en rojo.
- **Eventos** (botones del panel o `sma/eventos/<cruce>`):
  - *Accidente*: dos carros chocados bloquean un carril, se forma cola, se acercan curiosos y entra una
    **ambulancia**. En el cruce5 se elige el sentido (salida/entrada) y el plan de contraflujo se ve en el mapa:
    ruta de desvío en **verde** con flechas animadas, ruta alterna en **azul**, **barreras rojas** en los tramos
    cerrados, la Carrera 1 usando sus dos carriles en un solo sentido y una cuenta regresiva del plan arriba.
  - *Congestión*: se duplica el tráfico mientras dure el plan (5 min).
  - *Aglomeración*: 60 peatones extra alrededor del cruce mientras dure el plan (5 min).
  - 20 % de los carros nuevos quieren **salir de Bocagrande** y buscan la ruta más corta a la salida vigente.
  - *Cerco* (Caso 2): todos los accesos en rojo y llegan **patrullas**.
- **Métricas en vivo**: vehículos, velocidad promedio (con gráfica), detenidos, peatones esperando y cola por cruce.
- Si el equipo no llega a ~24 FPS se apaga solo el efecto de brillo (bloom).

## Inicio rápido

Requisitos: Python 3.11–3.13 y Mosquitto corriendo en `localhost:1883`.

```powershell
pip install -r requirements.txt

# Terminal 1: cruces de Bocagrande (sin ESP32 agregar --incluir-cruce1)
python maqueta_virtual\simulador_ciudad.py --incluir-cruce1
# Terminal 2: agentes (tardan ~15 s en arrancar)
python agentes\sma_agentes.py
# Terminal 3: panel web
python maqueta_virtual\panel\servidor_panel.py
```

Abrir **http://localhost:8000** (requiere internet la primera vez para cargar Three.js y las fuentes).
Elegir el **cruce5**, el sentido **salida** y pulsar **Accidente**: los agentes deciden en menos de 1 s
(Vigilancia → Clasificador → Coordinador → Consultor → Semafórico → Comunicador), se activa el
**contraflujo de la Carrera 1**, la cámara muestra el desvío y el plan dura 5 min; luego todo vuelve a *normal*
y los carros chocados se retiran 15 s después. Clic en el aviso del plan (arriba) para volver a la vista del desvío.
Controles: arrastrar = girar, rueda = zoom, clic derecho = desplazar.

Más detalles (instalación, ESP32, Git, problemas comunes) en [`COMANDOS.txt`](COMANDOS.txt).

## Casos de demostración

1. **Accidente → plan de desvío por utilidad** (contraflujo de la Carrera 1 en la salida/entrada de Bocagrande):
   funcionando (ESP32 y maqueta virtual).
2. **Persona solicitada → cerco semafórico**: pendiente.
