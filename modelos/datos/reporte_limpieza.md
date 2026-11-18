# Reporte de limpieza y depuración de datos reales

Generado por `python modelos/datos_reales.py`. Cada regla indica cuántas filas eliminó o corrigió.

## Accidentes de tránsito de Cartagena (datos.gov.co 5z62-bn99)

- Filas descargadas: 8066
- Duplicados por número de IPAT eliminados: 573
- Sin fecha u hora válida (descartadas): 0
- Coordenadas nulas o fuera de Cartagena (descartadas): 69
- Con etiqueta barrio=BOCAGRANDE: 196; ubicados por coordenadas sobre las vías del gemelo (< 60 m): **204** (descartados por lejanía a la vía: 0)
- Asignados a un cruce semaforizado (< 80 m): cruce1=3, cruce2=2, cruce3=2, cruce4=3, cruce5=8; en tramos entre cruces: 186
- Periodo 2020-01-03 a 2025-12-27; hora con más accidentes: 13:00; gravedad: {'CON HERIDOS': 200, 'CON MUERTOS': 4}

## Registro de semaforización de Cartagena (datos.gov.co mybb-py6y)

- Semáforos registrados: 90
- cruce1 (CARRERA 2 x CALLE 6): OFICIAL código 1024 'Hotel Almirante Estelar'
- cruce2 (CARRERA 3 x CALLE 6): OFICIAL código 1038 'Olimpica 24 horas'
- cruce3 (CARRERA 2 x CALLE 10): OFICIAL código 1021 'Banismo'
- cruce4 (CARRERA 3 x CALLE 10): OFICIAL código 1022 'Base Naval'
- cruce5 (CARRERA 2 x CALLE 15): OFICIAL código 1132 'Seguros Bolivar'

## Velocidad e intensidad vehicular de Medellín (SIMM)

- Filas descargadas: 431490 (553 carriles, 49 corredores)
- Fechas en formato de serie de Excel convertidas: 357059; ilegibles descartadas: 0
- marcadas como dato falso/no validado (identificador F): 8669
- intensidad negativa o > 2200 veh/h por carril: 1193
- velocidad > 130 km/h: 168
- velocidad 0 con vehículos pasando (sensor inconsistente): 1211
- ocupación fuera de 0-100 %: 0
- categorías que suman más que la intensidad: 2
- Registros duplicados carril-hora promediados: 0
- Atípicos por carril y hora del día (fuera de 3 x IQR) marcados como hueco: 2335
- Estaciones (puntos de medición, carriles sumados): 199; periodo 2020-07-01 00:00:00 a 2020-08-31 23:00:00 (1488 horas)
- Estaciones con >= 80 % de cobertura por mes: 2020-07=156, 2020-08=0 -> se usa **2020-07** (744 horas)
- Estaciones con < 80 % de horas válidas descartadas: 43; quedan **156**
- Huecos: 6465 celdas; interpolados (≤ 2 h): 3006; los largos se llenan con el promedio de esa estación a esa hora y día de la semana: 3459
- Perfil de demanda: hora pico en día hábil = 17:00; madrugada (3:00) = 0.12 del pico
