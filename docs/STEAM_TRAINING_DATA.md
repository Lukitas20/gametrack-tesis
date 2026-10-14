# Observaciones reales para el recomendador

Este recolector prepara un dataset **offline** de votos reales de Steam. No
entrena, no publica modelos, no inserta cuentas o reseñas en la base de
GameTrack y no consulta bibliotecas de terceros. No necesita `STEAM_API_KEY`.

La fuente oficial es
[IUserReviewsService/GetAppReviews](https://partner.steamgames.com/doc/webapi/IUserReviewsService).
Steam sustituyó `/appreviews` por este servicio, con parámetros en `input_json`.
La respuesta vigente no contiene `success`: se valida el estado HTTP y su
estructura `response.reviews` y `response.cursor`.

## Ejecutar una tanda acotada

Desde la raíz del repositorio, con las dependencias del backend instaladas:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/collect_steam_training_data.py --appids-file ruta/al/catalogo.json --max-games 50 --pages-per-game 2 --max-pages 100 --max-requests 120 --delay 1
```

También se pueden indicar `--appids 730,570,440`. El archivo admite una lista
de AppIDs o un objeto `games: [...]`; sus elementos pueden contener
`steam_appid`, `steam_app_id` o `appid`. Las categorías del catálogo sirven para
seleccionar cobertura, **no son opiniones ni etiquetas de entrenamiento**.

La salida predeterminada es `backend/data/generated/steam_training`, ignorada
por Git. Se puede cambiar con `--output-dir`. Cada directorio lleva también
su propio `.gitignore` para no versionar accidentalmente los datos privados.
Ejecutar otra vez el mismo comando continúa desde el cursor guardado, con un
nuevo presupuesto por ejecución. Los juegos cuyo resultado ya se agotó se
saltan. El cursor sólo permite continuar; no se interpreta como dato del usuario.

Las llamadas son seriales, separadas por al menos un segundo. Hay como máximo
dos reintentos predeterminados para errores de transporte, HTTP 429 y 5xx; los
reintentos también gastan `--max-requests`. Se respeta `Retry-After`; si exige
más de 120 segundos, se detiene la tanda para reanudar más tarde. Un error
conserva las páginas ya obtenidas y termina con estado `partial`, sin fabricar
resultados para cubrir el faltante. No hay `verify=False` ni acceso a secretos.

## Qué se conserva

`observations.jsonl` contiene una observación vigente por autor y juego:

- `user_key`: alias HMAC-SHA256 estable del autor, con clave privada local.
- `steam_appid`, `review_id` y el voto booleano original `voted_up`.
- `created_at`, `updated_at`, `language` cuando Steam los proporciona.
- `minutes_at_review`, `minutes_forever`, `minutes_recent`, separados y en
  minutos. Ausencia o valor inválido se conserva como `null`, nunca como cero.
- `source=steam_public_review_api` y `retrieved_at`.

No se conservan texto de reseñas, nombres, SteamIDs ni URLs de perfiles. La
clave de 32 bytes está en `.author_hmac.key`: **no compartir ni publicar**.
Los alias son seudónimos, no una promesa de anonimización irreversible; también
se deben proteger el dataset y los identificadores de reseñas. Si la clave se
pierde, el recolector se niega a mezclar nuevas identidades con el archivo
existente. Las copias privadas para continuar la misma tanda deben conservarla.

Los duplicados no suman votos. Una actualización más reciente reemplaza el
estado anterior de la reseña; una versión más antigua no revierte ese estado.
Un ID de reseña que cambia de autor o juego se rechaza y contabiliza. El voto
debe ser booleano explícito; los campos ausentes no se infieren.

`checkpoint.json` conserva los cursores y el SHA-256 de cada respuesta recibida
(sin guardar su cuerpo crudo). `manifest.json` registra fuente, parámetros,
fechas, presupuesto, contadores, clave identificada por una huella, hash del
JSONL y cobertura. Los archivos se sustituyen de forma atómica por página;
si un cierre ocurre entre datos y checkpoint, se podrá repetir una página,
pero la deduplicación evita inventar interacciones adicionales.

## Qué hay que verificar antes de entrenar

El reporte muestra usuarios con al menos 2, 3 y 5 juegos reseñados, usuarios
con votos de ambos tipos, fechas conocidas y cobertura de horas al escribir la
reseña. **Ningún conteo mínimo declara el dataset listo para entrenar**.

Se consultan las reseñas recientes (`filter=1`), todos los idiomas y ambos
votos, con todos los tipos de compra y filtrado de actividad ajena al tema.
La selección de juegos y el límite de páginas introducen sesgos; no es una
muestra representativa de todos los jugadores. Hace falta evaluar solapamiento,
clases, diversidad y una separación temporal sin filtraciones.

Un juego sin reseña no se transforma en un rechazo. Las horas actuales
(`minutes_forever` o `minutes_recent`) pueden ser posteriores al voto: **no se
deben usar como si fueran conocidas en la fecha de la reseña**. Sólo
`minutes_at_review` describe ese momento, y puede faltar. No se transforman las
horas en estrellas ni se generan perfiles o interacciones sintéticos.
