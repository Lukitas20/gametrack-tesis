# Audiencias por historiales públicos — publicada en Cloud

Estado al 17 de octubre de 2026: **publicada y comprobada en Cloud**. Código
`3c8ea7a` en `feature/IA`, revisión `gametrack-00025-v2j`, 100 % del tráfico.
El límite de uso que interrumpió el primer intento fue restablecido por el
usuario; la publicación se retomó con la revisión automática funcionando.

## Cambio

Se retira la división redundante de reseñas Steam entre votos positivos/negativos.
Se conservan por separado los grupos y permisos existentes de GameTrack. Para
Steam se forman grupos descriptivos de autores con al menos dos recomendaciones
explícitas de otros juegos que comparten género o etiqueta comunitaria. Se
excluyen todos los juegos del informe y se deduplican autores por grupo. Sólo
se publican grupos y juegos compartidos con cinco autores o más. Los grupos
pueden superponerse; no son clusters exclusivos ni predicciones de compradores.

Cada grupo muestra tamaño, denominador, criterio y recepción del conjunto:
autores que sólo recomiendan, sólo desaconsejan, tienen votos distintos entre
títulos o no tienen voto guardado. La recepción procede de la muestra guardada;
no de una predicción del clasificador. Las horas no participan en estos grupos.
El CSV del informe conserva los grupos, recepción, fuente, selección y cobertura.

La muestra incluye hasta 60 autores identificados, ordenados por SHA-256 de su
identificador antes de consultar sus gustos. No es representativa del mercado.
Se muestran datos faltantes: reseñas sin autor recuperado, perfiles privados,
inaccesibles, pendientes, historiales parciales y recomendaciones sin metadatos.
Los géneros o etiquetas no se infieren del nombre del juego.

## Consulta y almacenamiento

`GET /api/v1/analytics/audience` sólo agrega la caché vigente, sin HTTP externo.
`POST /api/v1/analytics/audience/collect` procesa un lote: recupera identidades
para un juego por coincidencia exacta del recommendationid y consulta hasta
cuatro autores, con un máximo de dos páginas de Steam Community por autor.
Ambos endpoints requieren rol desarrollador y envían `Cache-Control: no-store`.

La solapa inicia la consulta al abrirse, muestra avances y ofrece continuar o
reintentar. Cada petición conserva su avance en PostgreSQL; no depende de una
tarea en memoria ejecutándose después de responder. Un lease global evita
consultas simultáneas desde distintas instancias. La caché pública dura 24 horas;
los fallos se reintentan después de una hora. Un estado privado/inaccesible vacía
los ítems guardados; una caché vencida no alimenta los grupos.

`b71f4a920c83`, sobre `d8a104f3b927`, agrega dos tablas; no modifica ni elimina
tablas existentes. La caché interna sólo conserva appid/voto explícito y el
estado/fecha de consulta, vinculados al identificador público del autor. No
guarda textos, alias, horas, amigos ni bibliotecas privadas. Ningún identificador
de autor se incluye en las respuestas del panel. No hay datasets operativos
sintéticos ni entrenamiento nuevo.

## Evidencia disponible

La prueba de viabilidad previa, del 17/10/2026 a las 15:26 Argentina, fue local y
con base en sólo lectura: 56 autores vinculados por reseña exacta de una muestra
de 100 reseñas de Cyberpunk 2077; se consultaron 30 historiales públicos reales,
229 reseñas observadas y 26 historiales completos dentro de dos páginas. Veinte
autores tenían al menos dos recomendaciones ajenas a CD PROJEKT RED. Se observaron
8 autores con dos juegos indie recomendados, 6 con dos de mundo abierto, 6 con dos
RPG y 5 con dos shooters. Fueron grupos superpuestos. Las 30 reseñas objetivo
seleccionadas eran positivas: no permiten medir diferencias de recepción entre
audiencias satisfechas e insatisfechas. De 178 recomendaciones de otros juegos,
116 tenían metadatos del catálogo disponibles. Esa prueba **no es una ejecución
de la integración nueva en Cloud**. Sus agregados están en el archivo local
ignorado `backend/data/generated/audience-feasibility-20261017.json`; no se
conservó el corpus de perfiles ni se lo convirtió en entrenamiento.

Comprobaciones ejecutadas sobre fixtures aislados, nunca publicados como datos:

- 34 pruebas iniciales aprobadas: audiencias, nuevos historiales, migraciones y
  recuperación de autores. Incluyen API real con TestClient, permisos 401/403,
  integración, `no-store`, ausencia de identidades y GET sin HTTP externo.
- 26 pruebas finales aprobadas (una API deseleccionada) de historiales y autores,
  incluyendo esquema de respuesta tipado y recuperación de un autor con alias
  conocido sin requerir clave API. Este último test es adicional a los 34.
- 25 pruebas de frontend aprobadas, incluyendo inicio al abrir la solapa, avance
  entre lotes y grupos Steam aunque no haya usuarios GameTrack.
- Comparación de tablas/columnas/índices entre migraciones SQLite y ORM aprobada
  dentro de las 34 pruebas. SQL offline PostgreSQL compilado para la nueva
  revisión: exactamente dos CREATE TABLE, sin ALTER/DROP ni conexión a una base.
- Comprobación sintáctica de los módulos JavaScript y `git diff --check`.

No se atribuyen estas pruebas a personas ni a calidad de la segmentación.

## Publicación y comprobaciones ejecutadas en Cloud

Se volvió a consultar `origin/feature/IA`, sin cambios remotos pendientes. La
validación final aprobó **35 pruebas de backend y 25 de frontend**. La auditoría
de fuentes aprobó 271 archivos, los tres archivos de modelos permitidos y cero
rutas privadas, fixtures o bases de datos. Build
`13d4bf25-4112-4af4-9f1f-c2164def962d`, digest
`sha256:293294882fa3e2e1a8000043b84799de553ae8b5db68c18ca86925450275aa8c`.

La instancia real es `gametrack-postgres`. Se verificaron respaldos habilitados,
PITR y retención de siete días; el respaldo `1792216800000` terminó con estado
`SUCCESSFUL`. No se restauró ni sobrescribió la base. La ejecución
`gametrack-migrate-mlcvp` aplicó Alembic correctamente, antes de publicar el
servicio nuevo. Las cuatro tareas conservan sus argumentos permanentes y usan
la misma imagen nueva; las ejecuciones especiales sólo reemplazan argumentos
para esa ejecución.

`gametrack-train-n52mv` ejecutó el recolector de historiales públicos con
`--studio "CD PROJEKT RED" --steps 16`, sin entrenamiento ni creación de perfiles
o reseñas. Terminó correctamente. Para las 436 reseñas de siete juegos:

- 433 reseñas quedaron vinculadas a 421 autores distintos por identificador
  exacto; tres reseñas siguen sin identidad recuperada.
- Se consultaron los 60 autores seleccionados: 60 historiales públicos, seis
  parciales dentro del límite de dos páginas; cero pendientes de la muestra.
- 42 autores tenían al menos dos recomendaciones de otros juegos observadas.
  De 341 recomendaciones externas al conjunto, 224 tenían metadatos para agrupar.
- Se publican 23 grupos superpuestos: 17 por etiquetas y seis por géneros.
  Por ejemplo, mundo abierto: 11 autores (nueve sólo recomiendan juegos del
  informe, dos sólo desaconsejan); shooters: nueve (siete/dos); rol: 17 (14/3).
  Son conteos descriptivos de la muestra guardada, no métricas de relevancia,
  inferencias causales ni estimaciones de mercado.
- Siguen siendo cero usuarios GameTrack con interacciones en este conjunto;
  se mantienen separados de los reseñadores de Steam.

`gametrack-train-92fc9` verificó el informe completo con
`SET TRANSACTION READ ONLY` y validó el esquema `AudienceReport`; terminó
correctamente. Los resultados agregados se conservaron, con Unicode intacto:
`docs/evidence/audience-histories-cloud-20261017.json` y
`docs/evidence/audience-report-cloud-20261017.json`. No contienen identidades de
autores ni textos de reseñas.

El HTML, `api.js`, `developer.js` y `developer-audience.js` públicos coinciden con
las fuentes probadas y envían `no-cache`. El esquema público incorpora los
grupos tipados; las rutas de consulta y colección sin sesión responden 401.
Aprobaron salud, catálogo, callback HTTPS de Steam y protección privada. La
solapa Evaluación sigue ausente del componente compartido de estudio y juego.

Se verificó visualmente el componente con el informe agregado real de Cloud en
un servidor local aislado que sólo contenía archivos públicos y ese informe:
`docs/evidence/audiences-component-cloud-20261017.png`. **No es una captura de
una sesión autenticada de desarrollador en producción.** La sesión de navegador
disponible era de jugador y el acceso profesional la redirigió a su inicio;
no se cambió su tipo ni se creó una cuenta para la prueba.

Sigue pendiente el recorrido completo en navegador con una sesión real de
desarrollador, incluyendo colección y descarga CSV desde la interfaz pública.
La API y el avance entre lotes se comprobaron con tests aislados; la consulta
real, persistencia y agregación también se ejecutaron en Cloud. No se modificó
el Google Doc ni el LaTeX.
