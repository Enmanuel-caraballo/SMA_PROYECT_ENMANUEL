// ============================================================
// SMA Movilidad y Seguridad CTG - Prueba 2.2
// ESP32 DevKit V1: se conecta al WiFi y a Mosquitto (MQTT)
// y prende/apaga el LED azul de la placa (GPIO 2) según el mensaje.
// Librería necesaria: PubSubClient (de Nick O'Leary)
// ============================================================

#include <WiFi.h>
#include <PubSubClient.h>

// ---- CAMBIA ESTOS 3 DATOS ----
const char* WIFI_NOMBRE = "Familia Caraballo";          // solo redes de 2.4 GHz
const char* WIFI_CLAVE  = "743125662";
const char* IP_BROKER   = "192.168.1.7";     // IP del PC donde corre Mosquitto
// ------------------------------

const int   PUERTO_MQTT = 1883;
const char* TEMA_ORDENES = "sma/semaforo/cruce1";   // aquí escucha órdenes
const char* TEMA_ESTADO  = "sma/estado/esp32";      // aquí avisa que está vivo
const int   LED_PLACA = 2;                          // LED azul integrado

WiFiClient wifi;
PubSubClient mqtt(wifi);

// Se ejecuta cada vez que llega un mensaje al tema suscrito
void alRecibirMensaje(char* tema, byte* datos, unsigned int largo) {
  String mensaje;
  for (unsigned int i = 0; i < largo; i++) mensaje += (char)datos[i];
  mensaje.trim();

  Serial.print("Mensaje en ["); Serial.print(tema); Serial.print("]: ");
  Serial.println(mensaje);

  if (mensaje == "verde") {
    digitalWrite(LED_PLACA, HIGH);
    mqtt.publish(TEMA_ESTADO, "LED encendido (verde)");
  } else if (mensaje == "rojo") {
    digitalWrite(LED_PLACA, LOW);
    mqtt.publish(TEMA_ESTADO, "LED apagado (rojo)");
  } else {
    mqtt.publish(TEMA_ESTADO, "Orden desconocida");
  }
}

void conectarWiFi() {
  Serial.print("Conectando al WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_NOMBRE, WIFI_CLAVE);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi OK. IP del ESP32: ");
  Serial.println(WiFi.localIP());
}

void conectarMQTT() {
  while (!mqtt.connected()) {
    Serial.print("Conectando a Mosquitto... ");
    if (mqtt.connect("esp32-semaforos")) {
      Serial.println("OK");
      mqtt.subscribe(TEMA_ORDENES);
      mqtt.publish(TEMA_ESTADO, "ESP32 conectado y escuchando");
    } else {
      Serial.print("falló (código ");
      Serial.print(mqtt.state());
      Serial.println("). Reintento en 3 s");
      delay(3000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PLACA, OUTPUT);
  digitalWrite(LED_PLACA, LOW);

  conectarWiFi();
  mqtt.setServer(IP_BROKER, PUERTO_MQTT);
  mqtt.setCallback(alRecibirMensaje);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();
  if (!mqtt.connected()) conectarMQTT();
  mqtt.loop();   // revisa si llegaron mensajes
}
