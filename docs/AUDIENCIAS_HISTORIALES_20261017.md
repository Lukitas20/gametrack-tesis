# Audiencias por historiales públicos — integración local

Estado al 17 de octubre de 2026: código integrado localmente; **sin commit, push,
migración en Cloud, nueva compilación ni publicación confirmados**. La revisión
automática de permisos falló por límite de uso de la cuenta al consultar la
configuración de PostgreSQL. No fue una determinación de riesgo. No se intentó
eludir ese control. La versión pública sigue mostrando la audiencia anterior.

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

## Pendiente para publicar y comprobar

1. Resolver el límite que impide completar la revisión automática de permisos.
   Revisar el diff y guardar/subir a `feature/IA` conservando cambios remotos.
2. Consultar el nombre/configuración de la instancia PostgreSQL y sus respaldos.
   El intento de consultar `gametrack-db` respondió 404; no se supone su nombre.
3. Auditar fuentes con `verify_source_upload.py`, compilar la imagen y actualizar
   el job de migración a su digest. Ejecutar la migración **antes** de dirigir
   tráfico a la imagen nueva. No restaurar ni sobrescribir la base de producción.
4. Publicar el servicio y verificar salud, catálogo, permisos y los módulos
   públicos. Las tablas sólo agregan caché; no cambian modelos o identidades GT.
5. Usar un job con argumentos puntuales para ejecutar
   `scripts/collect_audience_histories.py --studio "CD PROJEKT RED" --steps 16`.
   No ejecutar entrenamiento ni importación normal por accidente. Este script
   escribe sólo las identidades recuperadas y la caché de observaciones públicas;
   imprime contadores y el informe agregado. No crea usuarios ni reseñas.
6. Comprobar el informe agregado en PostgreSQL y una sesión real de desarrollador:
   abrir Audiencias en el estudio y en un juego; ver grupos, cobertura, progreso,
   reintento y exportación CSV. Verificar también 401/403 sin permisos y que
   Evaluación continúe ausente. Guardar sólo evidencia agregada de lo ejecutado.

Siguen pendientes el recorrido autenticado en navegador, la consulta nueva de
Steam desde Cloud y la ejecución de esta migración en PostgreSQL. No se modificó
el Google Doc ni el LaTeX.
