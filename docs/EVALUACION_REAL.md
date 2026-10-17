# Procedimiento de evaluación y comprobaciones

No editar respuestas, generar juicios ni completar ausencias con ceros. Los
paquetes con textos o perfiles quedan en `backend/data/evaluation/`, fuera de
Git, Docker y Cloud Build. Los fixtures unitarios no son observaciones del estudio.
Los comandos Python siguientes se ejecutan desde `backend` con su entorno virtual.

## Recomendaciones y lectura de informes

1. Una persona real usa su cuenta. En Recomendaciones → Ajustes, **Exportar
   resultado para evaluar** conserva la respuesta recibida, el orden, los aportes
   y el contexto de estrategia. Comparar estrategias no permite exportar una
   respuesta anterior. En el informe de estudio/juego, **Evaluación** exporta el
   informe mostrado. El CSV habitual también conserva los segmentos agregados.
2. `python scripts/evaluation_study.py capture --input respuesta.json --output data/evaluation/sesion-01`
   crea una carpeta nueva: respuesta sin cambios, hashes, casos, hoja ciega y
   `annotations.csv` con etiquetas, anotador, tiempo, error y notas **vacíos**.
3. Para reunir varias capturas del mismo tipo antes de anotar:
   `python scripts/evaluation_study.py combine --packets data/evaluation/sesion-01 data/evaluation/sesion-02 --output data/evaluation/lote-01`.
   Se conserva cada corrida para calcular P@3 por lista y luego promediar.
   Conservar también los originales; no sobrescribir carpetas ni anotaciones.
4. El dueño del perfil juzga `relevance`: 1 pertinente, 0 no pertinente, vacío
   si no puede juzgar. Debe considerar su intención y el contexto completo.
   Un tercero no puede declarar la satisfacción personal del dueño del perfil.
   Para informes, `report_supported` juzga respaldo en las fuentes; no equivale
   a medir individualmente los clasificadores.

## Clasificador de voto, seis temas y cuatro aspectos

`python scripts/evaluation_study.py reviews --sqlite COPIA_LOCAL.db --output data/evaluation/resenas-01 --limit 50`
lee una base local existente en modo `ro` y `query_only`; no inicia la app ni
modifica reseñas. Selecciona las primeras reseñas reales guardadas en español por
ID, sin seleccionar por acierto. No exporta identidades de autores. La muestra es
un **piloto sin separación acreditada**, no un nuevo conjunto de evaluación.

La hoja ciega conserva el texto y oculta las predicciones y el voto guardado.
Los seis temas se anotan como presencia 1/ausencia 0. Los cuatro aspectos se
anotan `ausente`, `positivo`, `neutro` o `negativo`: ausencia no es neutralidad.
Usar dos anotadores independientes y conservar sus hojas; crear una tercera de
adjudicación sin modificar los originales. Una mención temática no demuestra
crítica, causa de un problema ni intención de comprar. Las horas no son estrellas.

El voto del autor ya observado se conserva aparte de la predicción. El evaluador
puede comparar ambos una vez acreditada la separación; no lo llama satisfacción
ni sentimiento de cada aspecto. Los temas sólo se evalúan si su cabeza estaba
habilitada; no se activan cabezas rechazadas por validación para completar la tabla.

## Tareas de uso y tiempos

Preparar un JSON de tareas diseñadas, con `id` e `instruction`, sin participantes
ni resultados inventados. Ejemplos de consignas: localizar un juego adecuado a la
intención declarada por la persona; explicar por qué se calculó un puntaje; abrir
un informe de estudio y distinguir tema de aspecto; interpretar el origen y la
cobertura de una audiencia; exportar el resultado. Son instrumentos, no acciones
observadas. Separar las tareas piloto de las reservadas para evaluación final.

`python scripts/evaluation_study.py usability --tasks tareas.json --output data/evaluation/uso-01`
crea una hoja vacía. El observador registra `task_complete` (1/0), seudónimo del
anotador/observador, `elapsed_seconds` medidos, errores y notas literales. Cronometrar
desde que se lee la consigna hasta completar/abandonar; registrar interrupciones
y ayuda. No equiparar éxito de tarea con satisfacción. Las preguntas de satisfacción
se hacen directamente a las personas y sus respuestas se conservan sin inferirlas.

## Separación y cálculo

El modelo de reseñas separa juegos por hash fijo 80/10/10, deduplica texto
normalizado y ajusta vocabulario en entrenamiento y umbrales en validación. El
modelo de afinidad separa autores por HMAC/hash fijo, exige historial anterior al
voto objetivo y elige parámetros/activación sólo con validación. El test publicado
no debe reutilizarse para ajustar y luego presentarse como una nueva evaluación.

Antes de habilitar métricas, contrastar las capturas con los índices originales
de entrenamiento y ajuste: juegos/textos para reseñas; autores/cronología y
consultas/tareas para afinidad y uso. Los artefactos publicados conservan huellas y
conteos, pero **no contienen esos índices ni prueban por sí solos la ausencia de
intersecciones de una nueva muestra**. Si faltan, obtenerlos del responsable del
corpus y documentar su reconstrucción; no declarar independencia.

El responsable deja una auditoría JSON con `source_sha256` del manifiesto,
`purpose: "evaluation"`, `disjoint_train_tune_evaluation: true`, `reviewer`,
`training_index_sha256` y `tuning_index_sha256`, **sólo después de comprobarlo**.
`python scripts/evaluation_study.py metrics --packet data/evaluation/lote-01 --annotations adjudicadas.csv --audit separacion.json`
valida integridad y registra que la separación fue acreditada externamente;
no reconstruye esos índices ni sustituye la auditoría con una etiqueta.

Con juicios faltantes, menos de veinte casos distintos o sin auditoría, no se
publican métricas. Este mínimo es una barrera operativa, no un cálculo de potencia
ni veinte participantes. Por dimensión se exige cobertura de clases (cinco por
clase) para publicar exactitud; se conservan matrices y denominadores. P@3 se
informa por corrida, sin afirmar MAP/NDCG sobre todo un catálogo desconocido.
Sin tiempos observados se conserva `null`. No se inventan participantes, votos,
relevancia ni satisfacción. El arnés anterior de variantes queda como instrumento
de ajuste exploratorio: el histórico se conserva, no se usa como evaluación actual
y no se convierten sus juicios faltantes en negativos.

## PostgreSQL, carga y restauración

- No hay cambios de esquema para audiencias: se agregan consultas derivadas.
  La única cabeza actual de Alembic es `d8a104f3b927`.
- Instalar las dependencias de `requirements-postgres.txt` y disponer de un
  PostgreSQL de pruebas cuyo nombre comience por `gametrack_test`. Definir
  `GAMETRACK_TEST_POSTGRES_URL` y ejecutar `pytest tests/test_postgres_integration.py tests/test_steam_postgres_limits.py -q`.
  El primer archivo crea y elimina sólo un esquema temporal con migraciones,
  copia, secuencias, bloqueo y presencia del worker. No usar la URL de producción.
- Para carga local con catálogo real: definir `GAMETRACK_TEST_SQLITE_SNAPSHOT`
  a una copia local. Iniciar `uvicorn scripts.test_readonly_server:app --host 127.0.0.1 --port 8008`.
  Este servidor expone sólo los GET de catálogo y salud, con base de sólo lectura,
  sin startup operativo. Ejecutar `python scripts/check_test_environment.py load --url http://127.0.0.1:8008 --requests 50 --concurrency 5`.
  Sólo acepta loopback y una carga acotada; no estresa Cloud Run ni mide tareas humanas.
- Para restaurar, disponer de `pg_dump`/`pg_restore` compatibles y dos bases
  dedicadas **distintas**: `GAMETRACK_TEST_SOURCE_URL` y
  `GAMETRACK_TEST_RESTORE_URL`, ambas `gametrack_test*`; destino sin tablas.
  Usar observaciones reales autorizadas en el origen de pruebas, con acceso
  restringido; no fabricar un dataset para el producto. Ejecutar primero
  `python scripts/check_test_environment.py restore --backup ../deploy/backups/test.dump`
  y luego repetir con `--execute`. El script no usa `DATABASE_URL`, no crea/borra
  bases, no sobrescribe respaldos, no usa `--clean`, restaura en una transacción y
  compara conteos por tabla y hash del archivo. Validar luego migraciones,
  constraints, secuencias y recorridos de la app en ese destino de pruebas.
  No efectuar esta operación contra producción. Restringir y conservar el respaldo
  fuera de Git/Cloud Build; su contenido puede incluir información personal.

Para verificar el funcionamiento de los dos modelos y audiencias sin escrituras:
`python scripts/verify_application_evidence.py --sqlite COPIA_LOCAL.db`. En Cloud,
el mismo script sin `--sqlite` exige PostgreSQL y una transacción de sólo lectura;
emite únicamente agregados. No inicia sincronización, entrenamiento ni migraciones.
