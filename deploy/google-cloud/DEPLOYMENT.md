# Registro de la instalación en Google Cloud

> Registro histórico. El 14/10/2026 se retiró de uso el modelo demo descrito
> abajo. La política vigente admite sólo datos reales; ver `docs/REAL_DATA.md`.

Fecha: 7 de octubre de 2026. Proyecto: `gametrack-510920`.
Región: `us-central1`.

## Código e imagen

Base utilizada para construir la imagen: commit de `feature/IA`
`f5c63117302ca06e7e6badee12dcdad73e503908`, más la adaptación a Cloud y las
correcciones de Steam y caché incluidas en esta rama. El código de estos
ajustes se incorpora al repositorio el 10 de octubre de 2026. Publicar en Git
no reconstruye ni despliega automáticamente la imagen de Cloud Run.

Cloud Build inicial `6fbc5cbb-9f79-450b-906e-3622a858211d`: `SUCCESS`.
Build de instalación `19c4476d-1b41-4e32-8aad-2d0d9f35f826`: `SUCCESS`.
Build de Valoraciones `5aaec7e8-ba91-481a-8571-f19af48710d3`: `SUCCESS`.
Build de nombres Steam `989106ce-2752-4ff5-bf19-57c0db5b59fe`: `SUCCESS`.
Build de caché frontend `a2ab941c-5c7c-48ac-8e24-08656607d451`: `SUCCESS`.
Imagen final compartida por web y jobs:

```text
us-central1-docker.pkg.dev/gametrack-510920/gametrack/app@sha256:f30557eae353946eaeb6d8e205ddb708b85d36888a8974acd32477271fc2a013
```

## Datos y respaldo

Alembic: ejecución `gametrack-migrate-tk226` completada, revisión
`c6d910a74e32`. Copia: `gametrack-import-vn64p` completada.
El importador verificó los recuentos de origen y destino dentro de la misma
transacción, conservó IDs y relaciones y ajustó las secuencias PostgreSQL.

| Tabla | Filas migradas |
|---|---:|
| games | 190789 |
| genres | 30 |
| steam_catalog_entries | 190744 |
| steam_catalog_sync | 1 |
| steamspy_sync | 101 |
| tags | 394 |
| users | 87 |
| friend_invites | 1 |
| friendships | 0 |
| game_genres | 19412 |
| game_lists | 263 |
| game_tags | 42907 |
| play_feedback | 0 |
| ratings | 1166 |
| reviews | 231811 |
| steam_auth_flows | 0 |
| steam_identities | 1 |
| steam_profile_cache | 1 |
| user_preferences | 218 |
| game_list_items | 1008 |
| review_aspects | 91999 |
| **Total** | **770933** |

Se omitió la presencia transitoria de workers. La base SQLite original se
conservó. El backup local portable quedó en
`deploy/backups/google-cloud-origen-20261007-portable.db`, excluido de Git.
La copia privada subida tiene 261308416 bytes y CRC32C `s+yuNg==`, igual al
archivo local. El objeto de migración caduca por política después de siete días.

Cloud SQL: respaldo posterior a la copia `1791406845348`, `SUCCESSFUL`,
completado a las `2026-10-07T21:02:16.929Z`. Backups diarios y recuperación a
un punto en el tiempo con retención inicial de siete días. La restauración
en una instancia de prueba queda como ensayo operativo previo a producción.

## Acceso y consumo

Secret Manager contiene URL SQL, clave JWT y clave Steam; sus valores no se
incluyen en código, imagen, planes ni este registro. El usuario autorizó
reutilizar su clave Steam. Las identidades de web, importación, entrenamiento,
sincronización, construcción y Scheduler tienen permisos separados.

Presupuesto: USD 60 mensuales, consumo bruto excluyendo créditos, alertas al
50/80/100 %. El presupuesto avisa y no detiene el gasto. La prueba y el crédito
fueron confirmados por el titular; no se convirtió la cuenta a pago.

## Publicación

Modelo `demo-20261007-2054`, ejecución `gametrack-train-tbf9r`: completada.
Se publicaron `local_model.npz` y `local_model.json` en el bucket privado.
Entrenamiento: 1166 notas, 79 jugadores, 45 juegos, etiqueta `demo`.
Validación: 79 notas, cobertura 79/79; RMSE 0.7247 y MAE 0.5619 frente al
baseline 0.6195 y 0.4916. `automatic_eligible=false`: disponible como estrategia
manual, sin reemplazar la estrategia automática existente. Esta validación
sirve para decidir activación y no constituye una prueba final independiente.

Web publicada en https://gametrack-591398735968.us-central1.run.app.
El frontend, `/api` y `/api/v1/health` responden 200 y la base responde `ok`.
La web reconoce HTTPS detrás del proxy de Cloud Run y el callback de Steam
vuelve al mismo origen seguro. La sonda interna `/healthz` responde al probe;
para acceso público se usa `/api/v1/health`, ya que la entrada de Cloud Run
reserva algunas rutas terminadas en `z`.

Pruebas sobre la imagen final: login y perfil de `jugador.demo`, `nuevo.demo`
y `dev.demo`; recomendaciones para jugador con historial y sin historial;
modelo cargado con estado `ready` y estrategia manual `ia_local`; amigos;
panel de desarrollador y exploración de reseñas. Respuestas 200 en los casos
autorizados, 401 sin sesión y 403 al pedir analítica con rol jugador.
Se comprobó la redirección inicial de Steam y su callback HTTPS; no se
completó el acceso personal del titular dentro de Steam.

Sincronización manual `gametrack-sync-t94wp`: completada, cinco ciclos,
cinco páginas de índice, final `idle` sin errores, base 267.75 MiB. No había
fichas solicitadas pendientes en esa tanda. La web vio la finalización.
Scheduler está habilitado cada seis horas: `17 */6 * * *` UTC, equivalente a
03:17, 09:17, 15:17 y 21:17 en Argentina. Invocación OAuth con identidad
`gametrack-scheduler`, cero reintentos automáticos. Primer disparo de prueba
aceptado por Google a las `2026-10-07T21:19:44.688766Z`; ejecución
`gametrack-sync-lz9tm` completada con éxito a las
`2026-10-07T21:22:57.046057Z`, invocada por la cuenta de Scheduler y usando
la imagen final. Terraform: plan final sin cambios pendientes.

La comprobación final del código remoto confirmó nuevamente
`feature/IA = f5c63117302ca06e7e6badee12dcdad73e503908`. Los textos de IA ya
hablan de un modelo propio entrenado con valoraciones, sin atribuir su
entrenamiento a la PC del visitante. No se hizo prueba de carga.

## Corrección de Valoraciones

La pantalla `/valoraciones` consultaba únicamente `/me/ratings`, por lo que
una cuenta con reseñas en Steam pero sin notas locales veía un mensaje vacío
incorrecto. Ahora consulta también `/steam/me/reviews`, permite sincronizar
y cargar las páginas siguientes, y distingue las reseñas binarias de Steam
de las notas de 1 a 5 de GameTrack. El perfil utiliza el mismo renderer.
Los errores de una fuente no ocultan la otra; los perfiles privados y los
fallos de Steam no se presentan como ausencia de reseñas.

Publicación: build `5aaec7e8-ba91-481a-8571-f19af48710d3`, revisión web
`gametrack-00004-cnq`, Terraform con cinco actualizaciones de imagen,
cero recursos nuevos y cero eliminaciones. Se conservaron recursos, límites,
presupuesto, secretos y versión del modelo.

Verificación: 33 pruebas existentes de reseñas e historial Steam aprobadas;
en el navegador, fixtures sin notas locales y con reseñas positivas/negativas,
paginación con deduplicación, sincronización, perfil privado, historial vacío
y fallos independientes de cada fuente. Los tres módulos publicados coinciden
con los archivos probados. El chequeo público de salud, Steam y cuentas demo
aprobó. El artefacto local figura como `stale` porque las notas actuales difieren
del entrenamiento; conserva compatibilidad y la estrategia manual `ia_local`
responde correctamente. El chequeo admite tanto `ready` como `stale`, según el
contrato del cargador y sus pruebas existentes. No se reentrenó para este arreglo.
La verificación del contenido personal del titular queda para su sesión Steam;
las pruebas de navegador no utilizaron sus datos ni credenciales.

## Nombres de las cuentas Steam

Las cuentas nuevas toman el nombre de Steam como nombre visible y como usuario
de GameTrack, hasta los límites de cada campo. Los nombres ocupados reciben un
sufijo legible; se conservan acentos y emoji. Si Steam no entrega el perfil,
se utiliza un usuario `jugador_steam` único, sin exponer el SteamID.

Las cuentas verificadas antiguas, sin contraseña y con el usuario automático
`steam_<SteamID>`, se actualizan al acceder a `/auth/me` o volver a ingresar
con Steam. Se conserva el ID interno, la identidad Steam y las relaciones de
la cuenta. Los usuarios de cuentas con contraseña y los nombres personales
elegidos previamente no se reemplazan. El nombre público Steam puede cambiar
en el siguiente acceso; el usuario de GameTrack ya asignado queda estable.

Las pruebas cubren nombres repetidos, cortos, largos y Unicode, recuperación de
cuentas existentes con y sin nombre cacheado, perfil ausente o fallido, login
con contraseña, conflictos de identidad y rollback de la transacción completa.
La reserva por savepoint también respeta la transacción exterior de SQLite.
Se aprobaron 112 casos distintos de acceso Steam, API, perfil Steam y amistades;
la última protección de conflictos se verificó en los 36 casos de acceso Steam.

Publicación final: build `989106ce-2752-4ff5-bf19-57c0db5b59fe`, revisión
`gametrack-00005-2jl` lista y con tráfico activo. Terraform actualizó únicamente
las cinco imágenes existentes, sin nuevos recursos ni eliminaciones. El chequeo
público de salud, callback Steam HTTPS, cuentas demo, perfiles, amistades y
recomendaciones aprobó sobre esta imagen. La actualización personal del titular
se realizará al restaurar su sesión; no se accedió a sus credenciales para probarla.

## Actualización del frontend y caché

El titular todavía veía el mensaje anterior de Valoraciones. Una consulta
directa al despliegue confirmó que los módulos públicos coincidían con el
código nuevo y ya no contenían esa frase. Los archivos no tenían una política
explícita de caché y la página mantenía las versiones anteriores del importmap.

Ahora HTML, JavaScript y CSS envían `Cache-Control: no-cache`, tanto en respuestas
200 como 304. Se conservan ETag y la descarga condicional: la siguiente recarga
comprueba si el archivo cambió antes de reutilizarlo. Los módulos modificados
del frontend y el arranque de la aplicación utilizan la versión
`cloud-20261007-3`; Valoraciones y su nuevo módulo de reseñas también se incluyen
en el importmap. Cinco pruebas de frontend y revalidación aprobaron.

El despliegue se realiza expresamente después de probar cada arreglo. Editar
el workspace no publica por sí mismo; todavía no se configuró CI/CD por push.
Cambiar únicamente la ruta `#/...` no reinicia una aplicación ya abierta.

Publicación: build `a2ab941c-5c7c-48ac-8e24-08656607d451`, revisión activa
`gametrack-00006-n8z`. Terraform cambió únicamente las cinco imágenes existentes.
La verificación pública confirmó la política `no-cache` en la página, el arranque,
Valoraciones, reseñas Steam y CSS, incluyendo respuestas 304. La raíz con
`?v=cloud-20261007-3` entrega el importmap nuevo; el módulo de Valoraciones contiene
la sección Steam y no el mensaje anterior. `/api/v1/health` confirmó base `ok`.
Una pestaña nueva del navegador cargó la aplicación y el acceso protegido a
Valoraciones correctamente, sin sesión personal disponible en ese navegador.

## Registro y experiencias de cuenta — 16 de octubre de 2026

Publicada la revisión `gametrack-00016-dbj`, con el 100 % del tráfico, a partir
del código `81d1e05`, integrado sobre `c35799d` de `feature/IA`. Se conservan
las mejoras existentes de afinidad, análisis de reseñas reales, autores y
catálogo profesional. El espacio profesional se identifica como demostración
del prototipo; esa indicación no convierte los datos analizados en sintéticos
ni representa una suscripción contratada.

El registro permite elegir jugador o desarrollador/estudio. Las cuentas nuevas
de Steam eligen en su primer acceso. La configuración inicial guarda gustos o
el nombre del estudio y un juego opcional, propio o de referencia. El perfil y
el menú permiten alternar experiencias conservando la cuenta y sus datos.

Build `014ef434-57f6-4b00-a639-77b1235c0ddc`, imagen fijada por digest
`sha256:c3cf4da23d4ae2195077c58509fe7a3c9898fb26bcf846202734106ecc41a254`.
Se actualizó únicamente la imagen de la web y de los cuatro jobs existentes,
mediante Google Cloud CLI, conservando sus configuraciones activas, secretos,
límites, horarios y modelos. Los parámetros locales conservan `untrained` para
el recomendador histórico retirado y la etiqueta de entrenamiento `observed`.
No se ejecutaron importaciones ni entrenamientos.

Respaldo Cloud SQL `1792190065962`: `SUCCESSFUL`, completado antes de la
migración. Ejecución `gametrack-migrate-hz8p8`: completada correctamente;
aplicó `a2e7c91d6b40 -> d8a104f3b927`, ampliando el esquema de usuarios sin
eliminar registros ni solicitar de nuevo la configuración a las cuentas actuales.

Validación: 197 pruebas de backend y 8 de frontend aprobadas, incluidas
migraciones, autenticación Steam, conservación de datos, política de datos
reales y empaquetado de modelos. La auditoría de Cloud Build verificó 257
archivos y los tres artefactos de modelos aprobados, sin fuentes privadas.
En la web pública se verificaron salud y base `ok`, catálogo, callback inicial
HTTPS de Steam, el nuevo contrato de cuenta y rechazo de cambios anónimos (401).
Nueve archivos publicados coinciden con el código probado y envían `no-cache`.
El navegador mostró las dos opciones del nuevo registro sin errores de consola.
No se crearon perfiles ni interacciones ficticias en la base publicada.

## Afinidad de próximos lanzamientos — 16 de octubre de 2026

Corregido el cálculo `prelaunch-2.0`, código `a39912c`, publicado en la revisión
`gametrack-00017-pqd` con el 100 % del tráfico. Build
`b52c2681-95ef-4de4-b715-09b3fc81babf`, digest
`sha256:ec15900c9012d7f208c05706a2d562fe278afde5358fc78f94c376745971f236`.

La ficha normal de algunos anuncios, incluido el AppID 4435490 de Call of Duty,
redirige a una pantalla de edad y no entrega sus etiquetas al importador. Se
obtienen ahora de la ficha breve pública `apphoverpublic` cuando la página
normal no las aporta, sin enviar una fecha de nacimiento ficticia. Las etiquetas
mantienen su fuente visible y las consultas incompletas se reintentan tras dos
minutos en lugar de conservarlas una hora.

El cálculo revisa todo el historial personal con rasgos disponibles en el
catálogo mediante un vector anuncio x historial. No hereda los 40 juegos con
más horas del feed ni calcula predicciones sobre todo el catálogo. Sólo las
comparaciones con rasgos específicos compartidos intervienen en el puntaje y
sus ejemplos; los juegos de otro tipo no diluyen el interés ni aportan una
penalización por compartir únicamente un género. Los rechazos comparables se
conservan y las horas siguen representando interés, no satisfacción.
Cuando faltan rasgos comparables no se inventa un número ni se anuncia afinidad
baja; se informa la cobertura del historial y la falta de evidencia. Se conserva
el límite provisional de 75 y no se modifica la escala de los juegos publicados.

Validación: 192 casos distintos de cálculo, historial Steam, próximos
lanzamientos, experiencias, recomendaciones y modelos reales aprobados. Las
regresiones incluyen un shooter por debajo de los 40 títulos con más horas,
rechazos de otros tipos de juegos, fallos de etiquetas, referencias y caché.
Las fichas reales de Call of Duty, Phantom Blade Zero y Final Fantasy Resonance
entregaron sus etiquetas en la comprobación local. La ejecución de nube
`gametrack-migrate-ctq22` ejecutó únicamente `verify_prelaunch_runtime.py` para
el AppID 4435490 y terminó correctamente, sin consultar perfiles ni escribir
datos; no ejecutó migraciones. La salud pública, base, catálogo, protección de
API y redirección inicial HTTPS de Steam también aprobaron.

Se actualizaron sólo las imágenes de la web y los cuatro jobs existentes. Se
conservaron secretos, modelos, horarios y límites. No hubo cambios de esquema,
entrenamientos, importaciones de usuarios ni datos de prueba en la base publicada.

## Elección de experiencia sin repetición — 16 de octubre de 2026

El registro ya guardaba el tipo de usuario, pero la bienvenida mostraba de nuevo
el selector. Ahora continúa con los gustos del jugador o la configuración del
estudio. El selector sólo aparece para una cuenta nueva de Steam que todavía
no eligió experiencia; también se respeta la elección hecha antes de entrar por
Steam y la configuración de estudio abierta desde el perfil.

Código `35b3b2e`, build `adce06f2-d395-488f-95d1-aab16882786c`, digest
`sha256:777a4f5203643e42dbe616457be880f6f25fada8f2b46854bb593698fb7d1649`.
Revisión `gametrack-00018-n8w` activa con el 100 % del tráfico. Se actualizaron
únicamente las imágenes de la web y los cuatro jobs existentes, conservando sus
configuraciones. No hubo cambios en la base ni ejecuciones de esos jobs.

Validación: 16 pruebas de frontend y 13 del control de fuentes aprobadas. La
auditoría verificó 256 archivos, los modelos aprobados y ninguna fuente privada.
Las pruebas de frontend se excluyen del paquete y la imagen; sus rutas públicas
devuelven 404. Los archivos públicos de bienvenida e inicio coinciden con los
probados y envían `no-cache`. Aprobaron salud, catálogo, callback HTTPS de Steam
y protección de acceso privado. No se crearon cuentas de prueba en producción.

## Rasgos conservados y coincidencias parciales — 16 de octubre de 2026

Se reprodujo el 30/100 de Probably Stolen, AppID 4348910, con la copia local del
historial real. Cyberpunk 2077 intervenía, pero faltaban rasgos que la fuente sí
publicaba: el refresco de Steam conservaba sólo etiquetas clasificadas como
community, mientras el motor también reconocía etiquetas antiguas clasificadas
como platform con votos comunitarios reales. El refresco podía eliminar esas
asociaciones y sus votos, entre ellas Cyberpunk y FPS.

El refresco conserva ahora la misma evidencia que admite el motor. El worker
también revalida cada siete días las fichas de SteamSpy previamente enriquecidas,
con una hora entre reintentos fallidos. Las respuestas incompletas o de otro
AppID conservan los rasgos anteriores. No se amplía el catálogo por ese camino.

La explicación `prelaunch-2.1` distingue coincidencias parciales y fuertes,
expone rasgos sin respaldo comparable y no presenta esa ausencia como rechazo.
Las coincidencias explícitas débiles no bastan para anunciar confianza media.
No se cambió la fórmula del índice ni su límite provisional de 75. En la prueba
local, recuperar los rasgos reales de Cyberpunk llevó el caso de 30 a 31 y volvió
a incluir ese juego entre las referencias principales; el interés por la
ambientación no confirma afinidad con la gestión y el comercio del anuncio.

Código `da626e5`, build `81522cd4-5b41-4d93-a1a4-576ab7f393a7`, digest
`sha256:7129f8514d3855ce9e0dca9eca67dd94d4ceadf10066773c5ec2bdc488023ff4`.
Revisión `gametrack-00019-gtk`, con el 100 % del tráfico. Se sincronizaron las
imágenes de los cuatro jobs existentes conservando sus configuraciones.

Validación: 189 casos distintos de afinidad, Steam, ingesta, worker y privacidad
de fuentes aprobados. Auditoría de 257 archivos y modelos permitidos, sin fuentes
privadas ni fixtures. La ejecución puntual `gametrack-migrate-l2pvv` completó
`scripts/refresh_known_traits.py --appid 1091500`: obtuvo únicamente etiquetas y
votos reales actuales de esa ficha de SteamSpy, sin ejecutar migraciones ni
modificar usuarios u opiniones. La lectura pública de `/api/v1/games/83` confirmó
la recuperación de Cyberpunk y FPS. Aprobaron salud, catálogo, callback HTTPS de
Steam y protección de datos personales. No hubo entrenamientos ni datos ficticios.

## Tipos de cuenta exclusivos y permanentes — 16 de octubre de 2026

Cada cuenta es jugador o desarrollador/estudio. El registro fija `role`; sólo
el registro Steam nuevo pendiente de elegir tipo puede establecerlo una vez.
La escritura condicionada impide consumir esa elección para tipos distintos
en solicitudes concurrentes. Editar el perfil o completar la configuración no
permite cambiar de tipo. Se conserva el tipo actual de las cuentas existentes.

Se retiraron los botones de alternancia del menú y del perfil. Los parámetros
de bienvenida no modifican un tipo ya definido y las rutas del otro tipo
redirigen al inicio propio. La ficha profesional no ofrece listas ni puntaje
personal. El servidor restringe listas, notas, reseñas propias, gustos,
biblioteca/logros Steam, amistades y recomendaciones personales a jugadores;
la analítica y el análisis libre de reseñas son de desarrolladores. La columna
histórica `developer_enabled` permanece por compatibilidad de esquema, pero no
concede permisos ni se publica en la respuesta de usuario. Los permisos usan
el tipo de la base, sin confiar en una indicación de rol del token.

La elección inicial de Steam precede a la importación de gustos: éstos se
infieren al confirmar una cuenta de jugador. Las sesiones posteriores conservan
el tipo, el estudio y el juego de análisis elegidos.

Código `8865b93`, build `d228a7f4-79ee-4be3-a2f2-27ed0f61044e`, digest
`sha256:94ed2aff3f36a2b3c8e5868ed6893cae68a7ba00d49796729c963c8b76e3ca0a`.
Revisión `gametrack-00020-f9w`, activa con el 100 % del tráfico. Sólo se
actualizaron las imágenes de la web y los cuatro jobs existentes, conservando
sus configuraciones. No se ejecutaron jobs, migraciones ni cambios en datos
de usuarios durante esta publicación.

Validación: 972 casos distintos de backend aprobados entre la corrida general
y la repetición final de los 117 de API, registro, permisos y Steam; 16 pruebas
condicionales omitidas. Las 18 pruebas de frontend aprobaron. La auditoría de
fuentes verificó 257 archivos y los modelos permitidos, sin datos privados ni
fixtures. Diez archivos públicos coinciden con el código probado y envían
`no-cache`. El esquema público rechaza campos extra en el registro y la edición
de perfil, no admite editar el tipo por perfil y no expone la habilitación
histórica. Aprobaron salud, catálogo, callback HTTPS de Steam y acceso privado
401; los archivos de pruebas permanecen fuera de la web. No se crearon cuentas
ni opiniones ficticias en producción.

## Planes como entrada y accesos separados — 17 de octubre de 2026

La entrada de cuentas presenta Jugador gratuito, Jugador Premium (USD 3/mes)
y Plan Estudio (USD 15/mes por estudio), según las secciones 10.2.1 y 10.2.2
del modelo de negocio. Los precios y la distribución de funciones se identifican
como propuestas; no se implementaron cobros, suscripciones ni nuevos permisos
por plan. Las dos opciones de jugador encaminan a `/acceso/jugadores`; Estudio
encamina a `/acceso/estudios`. Ambos accesos ofrecen ingreso y registro propios.
Steam sólo se ofrece en la pantalla de jugadores. El registro recibe el tipo
del acceso elegido, sin repetir el selector. El ingreso con contraseña rechaza
el acceso del otro tipo después de validar las credenciales y antes de emitir
una sesión. El tipo de cuenta sigue siendo permanente.

Las cuentas nuevas creadas con Steam quedan definidas como jugadores desde el
inicio. Las identidades existentes conservan su tipo, incluso los estudios con
identidades Steam históricas. No se alteraron cuentas operativas ni el esquema
de base de datos; el flujo de elección pendiente anterior sigue siendo compatible.

Código `23390fa`, build `27fd836e-38f8-4d6e-95a3-bb11496b39f9`, digest
`sha256:3dae21682184935644207a5e933905c4c4f7ac2fa6a5da24d610c0e833570f48`.
Revisión `gametrack-00021-9sc`, activa con el 100 % del tráfico. Se actualizaron
las imágenes de los cuatro jobs existentes sin ejecutarlos y conservando sus
configuraciones. No se ejecutaron migraciones, importaciones ni entrenamientos.

Validación: 121 casos distintos de backend y 21 pruebas de frontend aprobados.
La auditoría de fuente verificó 258 archivos y los modelos permitidos, sin datos
privados ni fixtures. Siete archivos públicos coinciden con el código probado y
envían `no-cache`; el esquema publicado admite el tipo opcional en el ingreso y
las fuentes de pruebas no están disponibles en la web. Aprobaron salud, catálogo,
callback HTTPS de Steam y protección privada 401. Se recorrieron visualmente los
planes, el acceso y registro de estudios sin Steam, y el acceso de jugadores con
Steam en la versión pública. No se crearon perfiles ni opiniones ficticias.
