# Acceso a GameTrack

La entrada inicial (`http://localhost:8000/#/cuentas`) permite iniciar sesión
con usuario y contraseña, crear una cuenta de jugador o continuar con Steam.
Las cuentas demo siguen disponibles en el desplegable de la pantalla.

En esta instalación, desde la terminal de Visual Studio Code en la raíz:

```powershell
.\INICIAR_GAMETRACK.ps1
```

El lanzador respeta `DATABASE_URL` y `PUBLIC_BASE_URL` del entorno o de
`backend/.env`. Sin configuración usa `backend/gametrack.db`. Si una instalación
conserva sus datos en `gametrack.runtime.db`, debe indicar esa ruta explícitamente
en `DATABASE_URL`; no se elige una base sólo porque exista otro archivo.
Antes de actualizar una base existente, aplicar las migraciones según
[DESPLIEGUE.md](DESPLIEGUE.md). Si el servidor ya está abierto en el puerto 8000,
basta con abrir el enlace del login.

## Steam

Se utiliza [Steam OpenID 2.0](https://partner.steamgames.com/doc/features/auth#website).
El usuario escribe sus credenciales exclusivamente en Steam. GameTrack verifica
la respuesta por HTTPS contra Steam y obtiene el SteamID autenticado.
La primera entrada crea una cuenta; las siguientes recuperan esa misma cuenta.

No se necesita una Steam Web API key para iniciar sesión. `STEAM_API_KEY`
permite obtener nombre/avatar, biblioteca y amigos. Al entrar con Steam se abre
el perfil, que consulta automáticamente estos datos y muestra los disponibles.

Al iniciar sesión con una cuenta Steam verificada y una biblioteca accesible,
GameTrack asigna hasta cuatro gustos de género a partir de los veinte juegos
con más tiempo registrado. Usa los géneros del catálogo y completa unas pocas
fichas faltantes desde Steam; no considera juegos sin tiempo jugado, software
ni categorías comerciales como «Free to play». Si la biblioteca es privada o
Steam no responde, permite elegir los gustos desde el perfil sin impedir el login.

En **Mi perfil → Mis gustos**, **Modificar** guarda una selección personal que
las sincronizaciones futuras respetan. **Usar mis juegos de Steam** vuelve a la
asignación automática; los gustos automáticos se actualizan al sincronizar la
biblioteca. Las selecciones que ya existían se conservan como manuales.

Configuración por defecto para desarrollo:

```dotenv
PUBLIC_BASE_URL=http://localhost:8000
```

Al desplegar, definir el origen HTTPS externo (sin ruta ni barra final) y una
`SECRET_KEY` propia en `backend/.env`. Detrás de un proxy, configurar Uvicorn
para reconocer exclusivamente los encabezados reenviados por proxies confiables.
El origen usado en el navegador debe coincidir con `PUBLIC_BASE_URL`; el acceso
anónimo desde 127.0.0.1 se redirige al origen configurado.

Para vincular una cuenta existente, entrar primero con usuario/contraseña y
elegir **Perfil → Vincular con Steam**. Los SteamID cargados manualmente antes
de este cambio requieren **Verificar con Steam**: no prueban la propiedad de
una cuenta y nunca se convierten automáticamente en credenciales de acceso.
Si ese ID está asociado a otro usuario, no se fusionan cuentas automáticamente.

El endpoint anterior `POST /api/v1/steam/link` rechaza la vinculación manual.
Su reemplazo es `POST /api/v1/auth/steam/link`, autenticado, que inicia OpenID.

## Persistencia y seguridad del flujo

- `steam_identities` guarda únicamente asociaciones verificadas por Steam.
- `steam_auth_flows` guarda hashes de estados aleatorios, con vencimiento y
  consumo atómico; funciona también con varios procesos del servidor.
- La cookie de estado es HttpOnly, SameSite=Lax y Secure en HTTPS.
- Se verifican namespace, proveedor, identidad, retorno, campos firmados,
  antigüedad del nonce y firma mediante `check_authentication`.
- El token de sesión no se envía en la URL. El retorno entrega una cookie
  efímera que se canjea una sola vez; luego usa el mecanismo JWT existente.
- Las contraseñas nuevas respetan el límite de 72 bytes de bcrypt.

Las dos tablas nuevas se crean al arrancar el prototipo, sin borrar datos.
Para instalaciones administradas mediante Alembic, la revisión
`ab29c8d71e90` las incorpora con `alembic upgrade head`.

## Validación

`tests/test_steam_auth.py` prueba registro, login, acceso Steam repetido,
cancelación, vencimientos, firma falsa, retorno/proveedor alterados, replay,
cuentas desactivadas, conflictos y verificación de vínculos anteriores.
Las respuestas de Steam se simulan: el ingreso real requiere que el usuario
complete la autenticación en Steam desde el navegador.

## Perfil, biblioteca y amigos

El perfil muestra biblioteca, minutos convertidos a horas, juegos con tiempo
registrado, última partida y actividad de las últimas dos semanas cuando Steam
incluye esos campos. Permite buscar, ordenar y filtrar por jugados, sin jugar y
valorados. Las horas ocultas o desconocidas no se presentan como cero.

Las notas de 1 a 5 provienen de GameTrack. La sincronización no crea ratings,
no sobreescribe las horas ingresadas manualmente y no convierte tiempo en
puntuaciones. Las reseñas públicas escritas en Steam se muestran en Valoraciones
con su veredicto original «Recomendado / No recomendado»; no se importan como
notas de GameTrack.

## GameTrackScore 2.0

El índice usa el historial de la propia identidad verificada: reseñas de Steam,
notas de GameTrack y hasta 40 juegos con al menos dos horas registradas. Las
horas tienen menor peso y sólo indican interés; poseer un juego o tener cero
horas no se interpreta como una opinión. Una nota de GameTrack prevalece sobre
la reseña del mismo juego y evita duplicar esa evidencia.

Las comparaciones usan etiquetas comunitarias con sus votos e IDF. Los géneros
y modalidades amplias se atenúan; compartir sólo «Acción» no establece una
coincidencia fuerte. Sin historial comparable, la afinidad por géneros se limita
a 55/100 y se identifica como provisional. Los gustos manuales acompañan al historial con un 5%; los
inferidos de Steam no se cuentan dos veces. La afinidad aporta 85%, Metacritic
10% y recepción comunitaria 5%; si falta una fuente pública, su peso pasa a
afinidad. El volumen de reseñas aporta confianza al indicador comunitario,
pero no suma afinidad ni tiene un componente de popularidad independiente.
Son pesos iniciales de diseño, pendientes de evaluación con preferencias
reales; el número no es una probabilidad de satisfacción.

Los endpoints privados del puntaje preparan hasta tres páginas de reseñas
personales con la misma caché que el perfil, sin exigir abrir Valoraciones.
Sólo se usan juegos con rasgos disponibles en el catálogo y páginas públicas
importadas; las explicaciones indican cantidades y fuentes efectivamente
usadas. La biblioteca privada se excluye, pero las reseñas pueden ser públicas
independientemente. No se usan datos de otra cuenta ni se crean estrellas.

El catálogo necesita la ingesta de rasgos de SteamSpy (`scripts/steamspy_sync.py
--index` y `--enrich N`) además de las fichas oficiales de Steam: las categorías
de la tienda por sí solas no describen mecánicas o experiencia. Si faltan esas
etiquetas, se informa la limitación y no se inventan comparaciones. La evidencia
del perfil identifica Steam cuando se usa su historial, aunque no haya notas
de GameTrack. Descubrí excluye juegos ya poseídos o valorados y admite otros
géneros si sus rasgos encajan con el historial.

La biblioteca se consulta con
[GetOwnedGames](https://partner.steamgames.com/doc/webapi/IPlayerService#GetOwnedGames)
y los amigos con
[GetFriendList y GetPlayerSummaries](https://partner.steamgames.com/doc/webapi/ISteamUser).
Requieren una Web API key válida y dependen de la privacidad del usuario. OpenID
verifica identidad; no concede acceso especial a datos privados.

`steam_profile_cache` conserva una copia privada por identidad verificada,
con caché de cinco minutos y botón de sincronización (mínimo 30 segundos entre
intentos). Un fallo transitorio conserva los últimos datos, identificados como
anteriores. Una respuesta privada elimina los datos cacheados de esa sección.
Las consultas del perfil nunca aceptan un SteamID arbitrario del cliente.

Se cruzan los amigos con **identidades Steam verificadas** de GameTrack. Una
cuenta que existe sólo por usuario/contraseña no se puede reconocer por Steam
hasta que el dueño la vincule. La lista permite solicitar o aceptar amistad;
consultarla no concede permisos ni acepta amistades automáticamente.

## Invitaciones

El botón **Invitar a GameTrack** abre un enlace copiable, que dura 30 días. No
envía mensajes en Steam. El invitado ve quién lo invita, crea su cuenta o inicia
sesión (incluido Steam) y confirma una solicitud de amistad. El anfitrión debe
aceptarla, salvo que ya hubiera enviado una solicitud al invitado.

Los enlaces con `localhost` sirven sólo para pruebas en esta computadora. Para
invitaciones entre computadoras, publicar la app y configurar `PUBLIC_BASE_URL`
con su dominio HTTPS. La interfaz informa esta limitación mientras es local.

Tablas nuevas: `steam_profile_cache` y `friend_invites`. El prototipo las crea
al arrancar; Alembic las incorpora en la revisión `bc30e8f12a91`. Pruebas:
`tests/test_steam_profile.py`, junto con autenticación, amistades y migraciones.

La pantalla incluye diseño adaptable, navegación por teclado, labels,
autocompletado, validación de contraseñas, errores en vivo, estados de carga,
mostrar/ocultar contraseña y reducción de animaciones con
`prefers-reduced-motion`. Se conservan logo, mascota y colores de marca.
