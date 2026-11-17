# SMA Movilidad y Seguridad · Cartagena

Proyecto final de **Inteligencia Artificial** (Ingeniería de Sistemas, UNICOLOMBO).
Sistema Multiagente (SMA) con **7 agentes SPADE** que coordinan semáforos ante incidentes de
movilidad y seguridad en Cartagena. Se demuestra con una **maqueta física** (ESP32, un cruce real)
y una **maqueta virtual** (mapa de Cartagena con 8 cruces en el navegador).

Equipo: Enmanuel (líder técnico), Omar, Mauricio, Yohan, Deyker.

## Arquitectura

```
 ESP32 (cruce1 real) ──MQTT──┐                         ┌── Panel web (maqueta virtual)
 Simulador de ciudad ─MQTT───┤   Mosquitto :1883       │   http://localhost:8000
 (cruce2..cruce8)            └────────┬────────────────┘
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
agentes/sma_agentes.py              los 7 agentes del SMA
esp32/esp32_cruce/                  cruce físico (2 semáforos + botón)
esp32/esp32_mqtt_prueba/            prueba de conexión (LED azul)
maqueta_virtual/cruces.json         8 cruces reales de Cartagena (coordenadas de OpenStreetMap)
maqueta_virtual/simulador_ciudad.py simula los cruces virtuales con el mismo contrato MQTT que el ESP32
maqueta_virtual/panel/              servidor FastAPI + página con mapa Leaflet
docs/                               diagramas, documento y checklist
```

## Cruces de la maqueta virtual

| Id | Cruce | Barrio | Vía A (principal) | Vía B (secundaria) |
|---|---|---|---|---|
| cruce1 | India Catalina (**ESP32**) | Chambacú | Av. Pedro de Heredia | Av. Rafael Núñez |
| cruce2 | Rotonda de Chambacú | El Espinal | Av. Pedro de Heredia | Rotonda de Chambacú |
| cruce3 | Pie de la Popa | Pie de la Popa | Av. Pedro de Heredia | Carrera 22 |
| cruce4 | Rotonda El Prado | Martínez Martelo | Av. Pedro de Heredia | Rotonda El Prado |
| cruce5 | Santander con Blas de Lezo | Centro | Av. Santander | Av. Blas de Lezo |
| cruce6 | San Martín con Calle 10 | Bocagrande | Av. San Martín | Calle 10 |
| cruce7 | Carrera 14 con Calle 6 | Castillogrande | Carrera 14 | Calle 6 |
| cruce8 | Mamonal con Corredor Vial | Mamonal | Vía Mamonal | Corredor Vial de Cartagena |

## Inicio rápido

Requisitos: Python 3.11–3.13 y Mosquitto corriendo en `localhost:1883`.

```powershell
pip install -r requirements.txt

# Terminal 1: cruces virtuales (sin ESP32 agregar --incluir-cruce1)
python maqueta_virtual\simulador_ciudad.py --incluir-cruce1
# Terminal 2: agentes (tardan ~15 s en arrancar)
python agentes\sma_agentes.py
# Terminal 3: panel web
python maqueta_virtual\panel\servidor_panel.py
```

Abrir **http://localhost:8000** y pulsar **"Simular accidente"**: el cruce pasa a *corredor*
(A verde, B rojo), la bitácora muestra la cadena Vigilancia → Clasificador → Coordinador → Consultor →
Semafórico → Comunicador y a los 10 s vuelve a *normal*.

Más detalles (instalación, ESP32, Git, problemas comunes) en [`COMANDOS.txt`](COMANDOS.txt).

## Casos de demostración

1. **Accidente → corredor de emergencia**: funcionando (ESP32 y maqueta virtual).
2. **Persona solicitada → cerco semafórico**: pendiente.
