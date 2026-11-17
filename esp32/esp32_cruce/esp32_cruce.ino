// ============================================================
// SMA Movilidad y Seguridad CTG - Maqueta física (tareas 2.3 y 2.4)
// ESP32 DevKit V1: 1 cruce con 2 semáforos + botón de accidente
//
// Semáforo A = vía principal  |  Semáforo B = vía secundaria
// Modos (los ordena el Agente Semafórico por MQTT en sma/semaforo/cruce1):
//   "normal"   -> ciclo automático A/B
//   "corredor" -> corredor de emergencia: A en verde fijo, B en rojo
//   "cerco"    -> cerco de persecución: ambos en rojo
//   "prioridad_a" / "prioridad_b" -> desvío: verde largo (20 s) para A o para B
//   "peatonal" -> aglomeración: todo rojo de 8 s en cada cambio para que crucen los peatones
//   (por compatibilidad: "verde" = corredor, "rojo" = normal)
// Botón: publica "accidente" en sma/eventos/cruce1
// Estado: publica JSON en sma/estado/cruce1 cada vez que cambia una luz
// Librería: PubSubClient (de Nick O'Leary)
// ============================================================

#include <WiFi.h>
#include <PubSubClient.h>

// ---- CAMBIA ESTOS 3 DATOS ----
const char* WIFI_NOMBRE = "Familia Caraballo";         
const char* WIFI_CLAVE  = "743125662";
const char* IP_BROKER   = "192.168.1.7";      
// ------------------------------

const int   PUERTO_MQTT   = 1883;
const char* TEMA_ORDENES  = "sma/semaforo/cruce1";
const char* TEMA_EVENTOS  = "sma/eventos/cruce1";
const char* TEMA_ESTADO   = "sma/estado/cruce1";

// ---- Pines (ver tabla de conexiones) ----
const int A_ROJO = 25, A_AMARILLO = 26, A_VERDE = 27;   // Semáforo A (principal)
const int B_ROJO = 32, B_AMARILLO = 33, B_VERDE = 13;   // Semáforo B (secundaria)
const int BOTON  = 4;                                   // pulsador a GND
const int LED_PLACA = 2;                                // azul: encendido = conectado a MQTT

// ---- Tiempos del ciclo normal (milisegundos) ----
const unsigned long T_VERDE_A = 8000, T_VERDE_B = 6000;
const unsigned long T_AMARILLO = 2000, T_TODO_ROJO = 1000;

enum Luz { ROJO, AMARILLO, VERDE };
enum Modo { NORMAL, CORREDOR, CERCO, PRIORIDAD_A, PRIORIDAD_B, PEATONAL };

// Fases del ciclo normal: {luz A, luz B, duración}
struct Fase { Luz a; Luz b; unsigned long ms; };
const Fase CICLO[] = {
  {VERDE,    ROJO,     T_VERDE_A},
  {AMARILLO, ROJO,     T_AMARILLO},
  {ROJO,     ROJO,     T_TODO_ROJO},
  {ROJO,     VERDE,    T_VERDE_B},
  {ROJO,     AMARILLO, T_AMARILLO},
  {ROJO,     ROJO,     T_TODO_ROJO},
};
const int N_FASES = sizeof(CICLO) / sizeof(CICLO[0]);
// Planes de desvío (mismas 6 fases, otros tiempos)
const unsigned long MS_PRIORIDAD_A[] = {20000, 2000, 1000, 5000, 2000, 1000};
const unsigned long MS_PRIORIDAD_B[] = {5000, 2000, 1000, 20000, 2000, 1000};
const unsigned long MS_PEATONAL[]    = {8000, 2000, 8000, 6000, 2000, 8000};

unsigned long duracionFase(int f);

WiFiClient wifi;
PubSubClient mqtt(wifi);

Modo modo = NORMAL;
int fase = 0;
unsigned long inicioFase = 0;
Luz luzA = ROJO, luzB = ROJO;
bool primeraVez = true;

// Botón con antirrebote
bool ultimoBoton = HIGH;
unsigned long ultimoCambioBoton = 0;
unsigned long ultimoAccidente = 0;

const char* nombreLuz(Luz l)  { return l == VERDE ? "verde" : (l == AMARILLO ? "amarillo" : "rojo"); }
const char* nombreModo(Modo m) {
  switch (m) {
    case CORREDOR: return "corredor";
    case CERCO: return "cerco";
    case PRIORIDAD_A: return "prioridad_a";
    case PRIORIDAD_B: return "prioridad_b";
    case PEATONAL: return "peatonal";
    default: return "normal";
  }
}

unsigned long duracionFase(int f) {
  if (modo == PRIORIDAD_A) return MS_PRIORIDAD_A[f];
  if (modo == PRIORIDAD_B) return MS_PRIORIDAD_B[f];
  if (modo == PEATONAL)    return MS_PEATONAL[f];
  return CICLO[f].ms;
}

void encender(int r, int a, int v, Luz l) {
  digitalWrite(r, l == ROJO);
  digitalWrite(a, l == AMARILLO);
  digitalWrite(v, l == VERDE);
}

void publicarEstado() {
  char json[96];
  snprintf(json, sizeof(json), "{\"modo\":\"%s\",\"A\":\"%s\",\"B\":\"%s\"}",
           nombreModo(modo), nombreLuz(luzA), nombreLuz(luzB));
  if (mqtt.connected()) mqtt.publish(TEMA_ESTADO, json, true);   // retenido: el panel ve el último estado
  Serial.println(json);
}

void ponerLuces(Luz a, Luz b) {
  if (a == luzA && b == luzB && !primeraVez) return;
  primeraVez = false;
  luzA = a; luzB = b;
  encender(A_ROJO, A_AMARILLO, A_VERDE, a);
  encender(B_ROJO, B_AMARILLO, B_VERDE, b);
  publicarEstado();
}

void cambiarModo(Modo nuevo) {
  modo = nuevo;
  fase = 0;
  inicioFase = millis();
  Serial.print("Modo -> "); Serial.println(nombreModo(modo));
  primeraVez = true;   // fuerza publicar el estado aunque las luces no cambien
}

void actualizarSemaforos() {
  if (modo == CORREDOR) { ponerLuces(VERDE, ROJO); return; }
  if (modo == CERCO)    { ponerLuces(ROJO, ROJO);  return; }
  // Modo normal o de desvío: avanza el ciclo sin usar delay()
  if (millis() - inicioFase >= duracionFase(fase)) {
    fase = (fase + 1) % N_FASES;
    inicioFase = millis();
  }
  ponerLuces(CICLO[fase].a, CICLO[fase].b);
}

void revisarBoton() {
  bool lectura = digitalRead(BOTON);
  if (lectura != ultimoBoton && millis() - ultimoCambioBoton > 50) {
    ultimoCambioBoton = millis();
    ultimoBoton = lectura;
    // Se presionó (va a LOW) y no se repite antes de 3 s
    if (lectura == LOW && millis() - ultimoAccidente > 3000) {
      ultimoAccidente = millis();
      Serial.println("Botón: ACCIDENTE");
      if (mqtt.connected()) mqtt.publish(TEMA_EVENTOS, "accidente");
    }
  }
}

void alRecibirMensaje(char* tema, byte* datos, unsigned int largo) {
  String m;
  for (unsigned int i = 0; i < largo; i++) m += (char)datos[i];
  m.trim(); m.toLowerCase();
  Serial.print("Orden recibida: "); Serial.println(m);

  if (m == "corredor" || m == "verde")      cambiarModo(CORREDOR);
  else if (m == "cerco")                    cambiarModo(CERCO);
  else if (m == "normal" || m == "rojo")    cambiarModo(NORMAL);
  else if (m == "prioridad_a")              cambiarModo(PRIORIDAD_A);
  else if (m == "prioridad_b")              cambiarModo(PRIORIDAD_B);
  else if (m == "peatonal")                 cambiarModo(PEATONAL);
  else Serial.println("Orden desconocida");
}

void conectarWiFi() {
  Serial.print("Conectando al WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_NOMBRE, WIFI_CLAVE);
  while (WiFi.status() != WL_CONNECTED) {
    actualizarSemaforos();      // los semáforos siguen funcionando mientras conecta
    delay(200);
    Serial.print(".");
  }
  Serial.print("\nWiFi OK. IP del ESP32: ");
  Serial.println(WiFi.localIP());
}

unsigned long ultimoIntentoMQTT = 0;
void conectarMQTT() {
  if (millis() - ultimoIntentoMQTT < 3000) return;   // reintenta cada 3 s sin bloquear
  ultimoIntentoMQTT = millis();
  Serial.print("Conectando a Mosquitto... ");
  if (mqtt.connect("esp32-cruce1")) {
    Serial.println("OK");
    mqtt.subscribe(TEMA_ORDENES);
    digitalWrite(LED_PLACA, HIGH);
    primeraVez = true;   // publica el estado actual apenas se conecta
  } else {
    Serial.print("falló (código "); Serial.print(mqtt.state()); Serial.println(")");
    digitalWrite(LED_PLACA, LOW);
  }
}

void setup() {
  Serial.begin(115200);
  int salidas[] = {A_ROJO, A_AMARILLO, A_VERDE, B_ROJO, B_AMARILLO, B_VERDE, LED_PLACA};
  for (int p : salidas) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
  pinMode(BOTON, INPUT_PULLUP);

  // Prueba de luces al encender: todas prenden 1 segundo
  for (int p : salidas) digitalWrite(p, HIGH);
  delay(1000);
  for (int p : salidas) digitalWrite(p, LOW);

  inicioFase = millis();
  conectarWiFi();
  mqtt.setServer(IP_BROKER, PUERTO_MQTT);
  mqtt.setCallback(alRecibirMensaje);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();
  if (!mqtt.connected()) { digitalWrite(LED_PLACA, LOW); conectarMQTT(); }
  mqtt.loop();
  revisarBoton();
  actualizarSemaforos();
}
