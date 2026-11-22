"""
SMA Movilidad y Seguridad CTG - CÁMARA DE VIGILANCIA CON RECONOCIMIENTO FACIAL (Caso 2)
=======================================================================================
La webcam del portátil hace de cámara de un cruce de Bocagrande (agentes/conocimiento.py · CAMARAS).

  Detección:      YuNet  (OpenCV Zoo, Wu et al. 2023)       -> caja y 5 puntos del rostro
  Reconocimiento: SFace  (OpenCV Zoo, Zhong et al. 2021)    -> vector de 128 números (embedding) del rostro alineado
  Comparación:    la hace el AGENTE CONSULTOR (similitud coseno contra el registro), no este programa.

Uso:
  python vision/camara.py --registrar "Alias" --consentimiento     registra a un compañero (solo guarda su vector)
  python vision/camara.py --camara cam1                            vigila: envía los vectores a sma/camaras/cam1
  python vision/camara.py --listar | --borrar "Alias"              registro (derecho de supresión, Ley 1581 de 2012)
  python vision/camara.py --camaras                                lista las cámaras del equipo (integrada, USB, virtuales)
  ... --indice 1  |  --indice HP  |  --indice USB                  elige la cámara por número o por parte de su nombre
                                                                   (sin --indice: la primera cámara física, no virtual)

Reglas éticas (obligatorias del proyecto):
  - Solo se registran compañeros que DIERON SU CONSENTIMIENTO (--consentimiento es obligatorio).
  - NUNCA se guardan fotos: ni al registrar ni al vigilar. Del registro solo queda el vector promedio.
  - El registro (vision/registro/) está en .gitignore: no se sube al repositorio.
  - Los rostros que no coinciden se descartan sin guardarse (lo hace el Consultor); la alerta dice "posible coincidencia".
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

CARPETA = Path(__file__).resolve().parent
MODELOS = CARPETA / "modelos"
REGISTRO = CARPETA / "registro" / "registro.json"
URL_ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
YUNET = "face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface/face_recognition_sface_2021dec.onnx"

sys.path.insert(0, str(CARPETA.parent / "agentes"))
import conocimiento as K  # noqa: E402


def modelo(ruta_zoo):
    archivo = MODELOS / Path(ruta_zoo).name
    if not archivo.exists():
        MODELOS.mkdir(exist_ok=True)
        print(f"descargando {archivo.name} de OpenCV Zoo ...")
        urllib.request.urlretrieve(URL_ZOO + ruta_zoo, archivo)
    return str(archivo)


class Rostros:
    """Detector YuNet + reconocedor SFace."""

    def __init__(self, umbral_deteccion=0.85):
        self.detector = cv2.FaceDetectorYN.create(modelo(YUNET), "", (320, 320), umbral_deteccion, 0.3, 5000)
        self.reconocedor = cv2.FaceRecognizerSF.create(modelo(SFACE), "")

    def detectar(self, imagen):
        h, w = imagen.shape[:2]
        self.detector.setInputSize((w, h))
        _, caras = self.detector.detect(imagen)
        return [] if caras is None else list(caras)

    def vector(self, imagen, cara):
        alineada = self.reconocedor.alignCrop(imagen, cara)
        v = self.reconocedor.feature(alineada).flatten().astype(float)
        return v / (np.linalg.norm(v) or 1.0)


def leer_registro():
    try:
        return json.loads(REGISTRO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"personas": []}


def guardar_registro(datos):
    REGISTRO.parent.mkdir(exist_ok=True)
    REGISTRO.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


VIRTUALES = ("virtual", "obs", "animaze", "snap camera", "droidcam", "manycam", "xsplit")


def nombres_camaras():
    """Nombres de las cámaras en el orden de DirectShow (el mismo índice que usa OpenCV en Windows).
    Usa el ffmpeg de imageio-ffmpeg si está instalado; si no, devuelve [] y se trabaja solo con números."""
    if sys.platform != "win32":
        return []
    try:
        import imageio_ffmpeg
        salida = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow",
                                 "-i", "dummy"], capture_output=True, text=True, errors="replace", timeout=15).stderr
    except Exception:
        return []
    return re.findall(r'"([^"]+)" \(video\)', salida)


def elegir_indice(texto):
    """--indice: número, parte del nombre ('HP', 'USB', 'Logitech') o nada (la primera cámara física)."""
    nombres = nombres_camaras()
    if texto is not None and str(texto).isdigit():
        i = int(texto)
    elif texto:
        coincide = [i for i, n in enumerate(nombres) if str(texto).lower() in n.lower()]
        if not coincide:
            sys.exit(f"no hay una cámara que se llame '{texto}'. Cámaras: {nombres or 'no pude leer los nombres; use un número'}")
        i = coincide[0]
    else:
        fisicas = [i for i, n in enumerate(nombres) if not any(v in n.lower() for v in VIRTUALES)]
        i = fisicas[0] if fisicas else 0
    return i, (nombres[i] if i < len(nombres) else f"cámara {i}")


def capturar(indice):
    return cv2.VideoCapture(indice, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(indice)


def brillo(cam, segundos=1.5):
    """Brillo medio tras calentar la cámara; None si no entrega imágenes."""
    fin, imagen = time.monotonic() + segundos, None
    while time.monotonic() < fin:
        ok, f = cam.read()
        if ok:
            imagen = f
    return None if imagen is None else float(imagen.mean())


def listar_camaras():
    nombres = nombres_camaras()
    for i in range(max(len(nombres), 4)):
        cam = capturar(i)
        if not cam.isOpened():
            if i >= len(nombres):
                break
            estado = "no abre (¿la usa otro programa?)"
        else:
            b = brillo(cam)
            estado = ("no entrega imágenes" if b is None else
                      "imagen NEGRA (tapada, obturador de privacidad o virtual sin programa)" if b < 8 else "OK")
        cam.release()
        nombre = nombres[i] if i < len(nombres) else "(sin nombre)"
        tipo = "virtual" if any(v in nombre.lower() for v in VIRTUALES) else "física"
        print(f"  --indice {i}   {nombre:32s} {tipo:8s} {estado}")


def abrir_camara(indice):
    i, nombre = elegir_indice(indice)
    cam = capturar(i)
    if not cam.isOpened():
        sys.exit(f"no pude abrir {nombre} (--indice {i}). ¿La usa otro programa (Teams, Zoom, el navegador)? "
                 f"Vea las cámaras con: python vision/camara.py --camaras")
    print(f"Usando {nombre} (--indice {i}).")
    b = brillo(cam)
    if b is not None and b < 8:
        print("  OJO: la imagen sale negra. Si es la cámara del portátil, abra el obturador de privacidad o pulse la tecla "
              "con el ícono de cámara (en los HP suele ser F10 o una tecla junto a F12). Si sigue negra, pruebe otra con "
              "--indice (lista: python vision/camara.py --camaras).")
    return cam


def dibujar(imagen, caras, texto, color=(0, 229, 255)):
    for c in caras:
        x, y, w, h = map(int, c[:4])
        cv2.rectangle(imagen, (x, y), (x + w, y + h), color, 2)
    cv2.putText(imagen, texto, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)


def registrar(alias, indice, muestras):
    """Toma `muestras` vectores de UNA sola cara frente a la cámara y guarda su promedio (no guarda imágenes)."""
    R, cam, vectores = Rostros(), abrir_camara(indice), []
    print(f"Registrando a '{alias}': mire a la cámara y mueva un poco la cabeza. ESC cancela.")
    ultimo = 0.0
    while len(vectores) < muestras:
        ok, imagen = cam.read()
        if not ok:
            continue
        caras = R.detectar(imagen)
        if len(caras) == 1 and time.monotonic() - ultimo > 0.4:
            vectores.append(R.vector(imagen, caras[0]))
            ultimo = time.monotonic()
        aviso = "una sola persona frente a la camara" if len(caras) != 1 else f"muestra {len(vectores)}/{muestras}"
        dibujar(imagen, caras, aviso)
        cv2.imshow("Registro (no se guardan fotos)", imagen)
        if cv2.waitKey(1) == 27:
            cam.release(); cv2.destroyAllWindows()
            sys.exit("registro cancelado")
    cam.release(); cv2.destroyAllWindows()
    promedio = np.mean(vectores, axis=0)
    promedio = promedio / np.linalg.norm(promedio)
    consistencia = float(np.mean([v @ promedio for v in vectores]))
    datos = leer_registro()
    datos["personas"] = [p for p in datos["personas"] if p["alias"] != alias]
    datos["personas"].append({"alias": alias, "vector": [round(float(x), 6) for x in promedio], "muestras": muestras,
                              "consistencia": round(consistencia, 3),
                              "consentimiento": datetime.now().isoformat(timespec="seconds")})
    guardar_registro(datos)
    print(f"'{alias}' registrado ({muestras} muestras, consistencia {consistencia:.2f}). Solo se guardó el vector.")


def vigilar(camara, indice, ventana, intervalo):
    """Detecta rostros y publica su vector en sma/camaras/<camara>; el Consultor decide si coincide."""
    import paho.mqtt.client as mqtt
    cruce = K.CAMARAS.get(camara)
    if not cruce:
        sys.exit(f"cámara desconocida '{camara}'; las de conocimiento.py son: {', '.join(K.CAMARAS)}")
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sma-{camara}")
    resultado = {"texto": "", "hasta": 0.0}

    def al_llegar(_c, _u, msg):          # respuesta del SMA (solo para mostrar en la ventana local)
        try:
            r = json.loads(msg.payload)
        except ValueError:
            return
        resultado.update(texto=r.get("texto", ""), hasta=time.monotonic() + 4)

    cliente.on_message = al_llegar
    cliente.connect("localhost", 1883)
    cliente.subscribe(f"sma/camaras/{camara}/resultado")
    cliente.loop_start()
    R, cam = Rostros(), abrir_camara(indice)
    print(f"{camara} vigilando {cruce} ({K.CAMARAS_NOMBRE.get(camara, '')}); no se guardan imágenes. ESC o Ctrl+C para salir.")
    ultimo = 0.0
    try:
        while True:
            ok, imagen = cam.read()
            if not ok:
                continue
            caras = R.detectar(imagen)
            if caras and time.monotonic() - ultimo >= intervalo:
                ultimo = time.monotonic()
                for c in caras:
                    cliente.publish(f"sma/camaras/{camara}", json.dumps({
                        "camara": camara, "ubicacion": cruce, "calidad": round(float(c[-1]), 3),
                        "embedding": [round(float(x), 5) for x in R.vector(imagen, c)]}))
            if ventana:
                activo = time.monotonic() < resultado["hasta"]
                color = (68, 23, 255) if activo and "coincidencia" in resultado["texto"] else (0, 229, 255)
                dibujar(imagen, caras, resultado["texto"] if activo else f"{camara} -> {cruce}: {len(caras)} rostro(s)", color)
                cv2.imshow(f"Camara {camara} (no se guardan imagenes)", imagen)
                if cv2.waitKey(1) == 27:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cam.release(); cv2.destroyAllWindows(); cliente.loop_stop(); cliente.disconnect()


def main():
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Cámara con reconocimiento facial del SMA (Caso 2)")
    p.add_argument("--registrar", metavar="ALIAS")
    p.add_argument("--consentimiento", action="store_true", help="confirmo que la persona dio su consentimiento")
    p.add_argument("--muestras", type=int, default=15)
    p.add_argument("--camara", default="cam1", help="id de la cámara (conocimiento.CAMARAS)")
    p.add_argument("--indice", default=None,
                   help="cámara: número o parte del nombre (HP, USB...); por defecto la primera física")
    p.add_argument("--camaras", action="store_true", help="lista las cámaras del equipo y si dan imagen")
    p.add_argument("--sin-ventana", action="store_true")
    p.add_argument("--intervalo", type=float, default=1.0, help="segundos entre envíos")
    p.add_argument("--listar", action="store_true")
    p.add_argument("--borrar", metavar="ALIAS")
    a = p.parse_args()
    if a.camaras:
        print("Cámaras del equipo (el número es el de --indice):")
        listar_camaras()
    elif a.listar:
        for x in leer_registro()["personas"]:
            print(f"{x['alias']}: {x['muestras']} muestras, consistencia {x['consistencia']}, consentimiento {x['consentimiento']}")
    elif a.borrar:
        datos = leer_registro()
        antes = len(datos["personas"])
        datos["personas"] = [x for x in datos["personas"] if x["alias"] != a.borrar]
        guardar_registro(datos)
        print("borrado" if len(datos["personas"]) < antes else "no estaba registrado")
    elif a.registrar:
        if not a.consentimiento:
            sys.exit("Falta --consentimiento: solo se registran personas que aceptaron participar (Ley 1581 de 2012).")
        registrar(a.registrar, a.indice, a.muestras)
    else:
        vigilar(a.camara, a.indice, not a.sin_ventana, a.intervalo)


if __name__ == "__main__":
    main()
