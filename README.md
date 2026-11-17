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
| `sma/semaforo/<cruce>` | `normal` \| `corredor` \| `cerco` (órdenes del Agente Semafórico) |
| `sma/estado/<cruce>` | JSON retenido `{"modo":"normal","A":"verde","B":"rojo"}` |
| `sma/panel/log` | JSON `{"ts","agente","texto"}`: bitácora de los agentes para el panel |

## Estructura

```
agentes/sma_agentes.py                  los 7 agentes del SMA
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

## Gemelo digital 3D

- **Vehículos**: siguen la red real (sentidos únicos incluidos), mantienen distancia y frenan en la línea de pare
  según la luz de su acceso (A o B). Luces de freno al detenerse.
- **Peatones**: caminan por los andenes y cruzan cuando la vía que atraviesan está en rojo.
- **Eventos** (botones del panel o `sma/eventos/<cruce>`):
  - *Accidente*: dos carros chocados bloquean un carril, se forma cola, se acercan curiosos y, cuando el SMA abre el
    corredor, entra una **ambulancia** por la ruta más corta.
  - *Congestión*: se duplica el tráfico durante 90 s.
  - *Aglomeración*: 60 peatones extra alrededor del cruce durante 75 s.
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
Elegir un cruce (lista de la izquierda o clic en su anillo) y pulsar **Accidente**: el cruce pasa a *corredor*
(anillo verde), aparecen los carros chocados y la cola, entra la ambulancia, la bitácora muestra la cadena
Vigilancia → Clasificador → Coordinador → Consultor → Semafórico → Comunicador el corredor se mantiene 30 s (`--duracion N` para cambiarlo) y vuelve a *normal*;
los carros chocados se retiran 15 s después.
Controles: arrastrar = girar, rueda = zoom, clic derecho = desplazar.

Más detalles (instalación, ESP32, Git, problemas comunes) en [`COMANDOS.txt`](COMANDOS.txt).

## Casos de demostración

1. **Accidente → corredor de emergencia**: funcionando (ESP32 y maqueta virtual).
2. **Persona solicitada → cerco semafórico**: pendiente.
