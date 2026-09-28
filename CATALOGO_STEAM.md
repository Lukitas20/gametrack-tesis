# Catálogo local de Steam

También puede funcionar como **catálogo compartido**, con PostgreSQL y un worker
independiente de la API. Ver [despliegue y migración al servidor](DESPLIEGUE.md).
Las secciones siguientes describen el modo local `embedded`; en modo `external`
las actualizaciones continúan mientras el servicio del servidor esté encendido,
aunque se cierre la app o se apague esta PC.

GameTrack conserva el catálogo en SQLite y lo actualiza mientras el backend está
abierto. El índice usa la API oficial `IStoreService/GetAppList/v1`, con clave,
paginación y cambios desde el último recorrido completo. La descarga del índice
y la obtención de fichas son tareas separadas: un juego puede estar disponible
en el buscador antes de tener información suficiente para recomendarlo.

## Activación en esta PC

1. Obtener una [clave Web API de Steam](https://steamcommunity.com/dev/apikey).
2. Agregar `STEAM_API_KEY=tu_clave` a `backend/.env`. Ese archivo está ignorado por
   Git. No incluir la clave en comandos compartidos, capturas ni commits.
3. Iniciar o reiniciar el backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

El proceso de fondo comienza automáticamente. Sin clave, no intenta descargar el
índice y el catálogo existente continúa disponible. Las fichas de AppIDs que ya
se conocen sí pueden enriquecerse mediante la tienda pública sin esa clave.

El catálogo muestra cantidad de juegos guardados, fichas verificadas, pendientes,
reintentos y fecha de última pasada completa. **Actualizar vista** consulta el
estado y los resultados locales: no inicia una importación desde el navegador.
También puede consultarse `GET /api/v1/steam/catalog/status`; es una lectura sin
credenciales, no devuelve la clave ni dispara solicitudes a Steam.

## Qué se guarda y cuándo se actualiza

| Datos | Comportamiento |
|---|---|
| Índice oficial | AppID, nombre y última modificación de la fuente; sólo juegos según el filtro de Steam. |
| Fichas | Géneros, modalidades, descripción, URL de imagen y otros datos existentes de GameTrack. |
| Reseñas | Muestra acotada de hasta `STEAM_REVIEWS_IMPORT_LIMIT` reseñas de Steam por juego. |
| Actividad propia | Valoraciones, reseñas de GameTrack, listas y amistades se conservan. |

La primera pasada recorre el índice público. Las siguientes utilizan
`if_modified_since`, con un solapamiento de cinco minutos. No se restringe el
idioma del índice para evitar perder juegos sin descripción en español. Los
slugs y las relaciones internas se conservan al actualizar nombres.
Si Steam devuelve un AppID sin nombre público, se avanza el cursor sin crear
un título inventado ni sobrescribir uno existente. Puede incorporarse cuando
Steam publique su nombre en una siguiente pasada de cambios.

Cada página se confirma en una transacción junto con su cursor y sus contadores.
Un corte permite retomar desde ese checkpoint. La marca temporal de la siguiente
pasada sólo avanza cuando termina la actual. Si falla Steam, no se declara que
el catálogo está completo y nunca se eliminan juegos por ausencia en una página.

Las fichas se procesan en tandas pequeñas. Abrir una ficha la prioriza en la cola;
la página sirve inmediatamente los datos locales. Las fichas pendientes revisan
su estado cada diez segundos durante un máximo de dos minutos, y conservan un
botón para volver a consultar. Las fichas vencidas también se procesan sin visitas.

Una reseña propia no consume el cupo de Steam. El límite de muestras evita sumar
otras cien reseñas en cada refresco; al alcanzar el cupo no se descargan nuevas
reseñas para ese juego. Los totales agregados declarados por Steam sí se vuelven
a consultar. Esta muestra limitada no representa todas las reseñas ni garantiza
que incluya las más recientes. No se borran muestras anteriores automáticamente.

## Configuración

Valores predeterminados en `backend/.env.example`:

```dotenv
STEAM_CATALOG_WORKER_ENABLED=true
STEAM_CATALOG_PAGE_SIZE=10000
STEAM_CATALOG_INTERVAL_MINUTES=60
STEAM_CATALOG_DETAIL_BATCH_SIZE=5
STEAM_CATALOG_REQUEST_DELAY_SECONDS=2
STEAM_SYNC_TTL_MINUTES=360
```

El intervalo horario se cuenta desde la última pasada completa. El proceso
alterna una página del índice con una tanda de fichas; por eso la duración inicial
depende de la red y de Steam. La pausa configurable está entre fichas y entre
páginas sucesivas, no es una garantía de cuota concedida por Steam. Las consultas
de una ficha pueden necesitar varias solicitudes HTTP.

Los errores y los límites de solicitudes activan reintentos. Los `Retry-After`
numéricos se respetan hasta un día, con un mínimo de un minuto. Una caída al obtener
fichas pausa también la tanda siguiente, para no recorrer el catálogo acumulando
errores. La cola conserva su propio reintento exponencial entre cinco minutos y
un día. El worker y las herramientas de consola comparten un bloqueo por base,
liberado por el sistema operativo si el proceso se cierra.

Al apagar la PC o cerrar el backend, las actualizaciones se detienen. El siguiente
arranque continúa el trabajo pendiente. No se instala un servicio de Windows ni
una tarea programada independiente. SQLite usa WAL y espera breve por bloqueos
para permitir lecturas de la interfaz durante las escrituras del proceso de fondo.

## Herramientas manuales

Desde `backend`, importar una página y luego continuar:

```powershell
.\.venv\Scripts\python.exe scripts/import_steam_appindex.py --limit-pages 1
.\.venv\Scripts\python.exe scripts/import_steam_appindex.py
```

`--page-size` ajusta el tamaño (máximo 50.000) y `--delay` la pausa entre páginas.
La salida distingue importación parcial, finalizada, ocupada y error. `--max-pages`
se conserva como alias de `--limit-pages`; el viejo `--limit` de fichas se reemplaza
por el límite de páginas para mantener checkpoints coherentes.

Procesar hasta cincuenta fichas pendientes o vencidas:

```powershell
.\.venv\Scripts\python.exe scripts/enrich_stubs.py --count 50 --delay 2
```

Si la app ya está procesando una tanda, el comando informa que está ocupado. Para
una importación manual exclusiva, cerrar el backend o desactivar el worker y
reiniciar. No hace falta ejecutar estos scripts para el mantenimiento habitual.

## Conservación de datos y recomendador

- Una ficha inaccesible queda `unavailable` y se reintenta; no prueba que el juego
  haya sido retirado. Se conserva la última información obtenida.
- Sólo una respuesta válida que indique otro tipo de producto lo marca
  `non_game`. Se oculta del catálogo y del recomendador sin borrar interacciones.
- La revisión del catálogo se guarda en la base: el proceso web detecta cambios
  de géneros, etiquetas y popularidad aunque otro proceso los haya escrito.
- Los stubs sin rasgos no fuerzan reconstrucciones del motor por sí solos.
- La matriz de notas usa CSR/CSC: guarda interacciones observadas, evitando
  reservar memoria para cada combinación posible de usuario y juego. Las
  similitudes colaborativas se calculan por bloques.

Se agregan dos tablas (`steam_catalog_sync` y `steam_catalog_entries`), creadas
por el arranque local. Para bases gestionadas mediante Alembic se incluye la
revisión `c917a03e6b24` sobre `b621f7389c02`. No se agregan columnas a `games` ni
se reinicia el dataset. Las copias locales y modelos generados permanecen fuera
de Git en `backend/data/generated/`.

## Alcance y comprobación

El objetivo es cubrir el catálogo que expone la tienda pública de Steam. No hay
garantía de incluir productos privados, inaccesibles o retirados, ni de detectar
cambios en el instante en que se publican. Un recorrido incremental tampoco
certifica retiradas: no se inventa ese estado por no recibir un AppID.

Tener el índice completo no significa tener todas las fichas completas o disponer
de valoraciones para entrenar IA sobre cada juego. El enriquecimiento es gradual.
La memoria de metadatos/TF-IDF y el ranking del quiz siguen creciendo con el
catálogo recomendable; el cambio a matrices dispersas elimina el principal costo
usuarios × juegos, no garantiza tiempo constante para toda consulta.

Los tests usan respuestas HTTP controladas y bases temporales para verificar
checkpoints, reintentos, transacciones, cambios entre procesos, límites de reseñas,
conservación de historial y memoria dispersa. La descarga completa real requiere
la clave local; no se presenta un test simulado como validación de cobertura real.

Validación del 25/09/2026: **352 pruebas aprobadas y 13 omitidas** (`GOLDEN_LIVE`,
opcionales). Sintaxis de los módulos JavaScript modificados verificada. Con el
servidor real se comprobaron el estado del worker y las recomendaciones, amigos
y asistente. La primera descarga real del índice terminó el 25/09/2026 a las
20:16 UTC: **188.703 entradas recorridas y 188.685 juegos incorporados** en
19 páginas. Las 18 entradas sin nombre se omitieron. Sumados los 45 juegos
previos, la base alcanzó 188.730 juegos; las fichas se completan gradualmente.
La prueba manual completó tres fichas y agregó 238 reseñas de Steam; después
se dejó el worker activo. Se comparó la base con la copia previa: se conservaron
íntegras las 1.166 valoraciones, 416 reseñas, 260 listas, 1.008 entradas de listas
y 215 preferencias existentes. El buscador y el estado del catálogo respondieron
en menos de un segundo en esta PC durante la comprobación HTTP.
La clave se configuró exclusivamente en el `.env` local, fuera de Git.
Se guardó una copia local en `backend/data/generated/` antes de iniciar el worker.
La revisión visual automatizada no pudo realizarse por la conexión de Browser
pendiente de reparación.

Referencias: [índice oficial y sincronización incremental](https://partner.steamgames.com/doc/webapi/IStoreService#GetAppList),
[reemplazo de la API antigua](https://partner.steamgames.com/doc/webapi/ISteamApps#GetAppList)
y [formato de Web API](https://partner.steamgames.com/doc/webapi_overview).
