# Catálogo compartido sin tarjeta: Neon + GitHub Actions

La base vive en Neon Free y GitHub Actions ejecuta una tanda cada seis horas.
Al terminar, el proceso cierra sus conexiones y Neon puede suspender el cómputo.
La API puede seguir corriendo en esta PC y usar esa base compartida; publicar la
web y su API en internet es un despliegue separado.

## 1. Crear la base gratuita

Crear un proyecto **Free** en <https://console.neon.tech>, sin agregar tarjeta ni
activar un plan pago. En **Connect**, desactivar **Connection pooling** y copiar
la URL PostgreSQL con `sslmode=require`. El hostname no debe contener `-pooler`:
el sincronizador usa un bloqueo de sesión que requiere una conexión directa.
Conservar los parámetros adicionales que entregue Neon.

Guardar la URL sólo en `backend/.env` (ignorado por Git) y en el secreto de GitHub
del paso 3. No pegarla en conversaciones, commits, capturas ni comandos que
queden en el historial. La clave de Steam existente tampoco va en el código.

## 2. Conservar los datos actuales antes de activar las tandas

Detener la API y el importador locales. Conservar `backend/gametrack.db` como
origen: la herramienta de copia lo abre en modo lectura y no lo reemplaza.
Instalar desde `backend`, usando el Python del entorno virtual:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-postgres.txt
```

Editar `backend/.env`, conservando las demás variables:

```dotenv
# Pegar aquí la URL DIRECTA de Neon, sin publicarla.
DATABASE_URL=
DB_AUTO_CREATE=false
STEAM_CATALOG_WORKER_MODE=scheduled
STEAM_CATALOG_WORKER_ENABLED=true
STEAM_CATALOG_INTERVAL_MINUTES=360
STEAM_CATALOG_REQUESTED_ONLY=true
STEAM_REVIEWS_IMPORT_LIMIT=10
```

No iniciar la aplicación con `DATABASE_URL` vacío. Una vez completada la URL:

```powershell
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe scripts/copy_sqlite_to_postgres.py --source gametrack.db
..\.venv\Scripts\python.exe scripts/copy_sqlite_to_postgres.py --source gametrack.db --execute
```

La copia exige un destino vacío y conserva usuarios, relaciones, juegos y
checkpoint. Revisar primero el resultado de validación. Si el destino ya tiene
datos, no borrarlos para forzar la copia. El tamaño en PostgreSQL puede diferir
del archivo SQLite; comprobar el consumo en Neon tras la transferencia.
Esta copia inicial no está protegida por el límite del sincronizador.

Para comenzar con una base vacía, omitir los dos comandos de copia. Se importará
el índice gradualmente, pero no aparecerán los usuarios ni valoraciones locales.
Después de una transferencia, volver a entrenar el artefacto de IA con su
procedencia correcta (ver DESPLIEGUE.md). El workflow sólo sincroniza Steam.

## 3. Configurar GitHub sin medios de pago

En el repositorio, **Settings → Secrets and variables → Actions**:

| Tipo | Nombre | Valor |
|---|---|---|
| Repository secret | `NEON_DATABASE_URL` | URL directa del proyecto Neon |
| Repository secret | `STEAM_API_KEY` | Clave de Steam existente |
| Repository variable | `STEAM_CATALOG_ENABLED` | `true`, sólo después de preparar/copiar la base |

Sin la variable de activación, el job queda omitido. La clave de Steam se usa
únicamente en el runner; no se guarda en el repositorio ni se publica en los logs.
El workflow no necesita permisos para escribir código, crear commits o agregar
colaboradores.

**GitHub sólo ejecuta `schedule` desde la rama predeterminada (`main` actualmente).**
El archivo `.github/workflows/steam-catalog.yml` queda en `feature/IA` para revisión.
Para activar el cron hay que integrarlo en la rama predeterminada; esto no cambia
ni integra ramas automáticamente. `workflow_dispatch` también requiere que el
workflow exista en la rama predeterminada. Una vez allí, **Actions → Catálogo
Steam (Neon Free) → Run workflow** permite elegir `feature/IA` para una prueba.
Antes de esa integración, un push a `feature/IA` que cambie el workflow o el
sincronizador también puede probarlo, siempre que ya esté habilitada la variable.

Para probar desde esta PC con la misma base y ajustes del workflow:

```powershell
$env:STEAM_CATALOG_PAGE_SIZE = '1000'
..\.venv\Scripts\python.exe -m app.workers.steam_catalog_batch --seconds 360 --max-cycles 60 --max-db-mib 400
```

## Límites y comportamiento

- Cuatro ejecuciones al día: `17 */6 * * *` en UTC. GitHub puede retrasarlas;
  no es una actualización en tiempo real. En repositorios públicos, GitHub
  desactiva horarios tras 60 días sin actividad; revisar Actions si se detienen.
- Cada job tiene un máximo de 12 minutos, incluida la instalación. En un mes
  de 31 días, cuatro jobs diarios consumirían como máximo 1.488 minutos, más
  ejecuciones manuales, pushes y otros workflows. GitHub Free incluye 2.000
  minutos mensuales para repositorios privados con runners estándar Linux.
  Sin medio de pago, agotar la cuota bloquea las ejecuciones adicionales.
- La tanda pide detenerse tras 360 segundos y procesa como máximo 60 ciclos.
  Deja terminar la solicitud/página en curso para confirmar el checkpoint;
  el timeout del job es el límite externo. No duerme hasta el próximo horario.
- El índice se importa por páginas de 1.000 entradas. Las fichas se completan
  sólo si fueron solicitadas por usuarios (`priority > 0`), con hasta 10 reseñas
  importadas por juego. Este ajuste no borra reseñas anteriores ni de usuarios.
- Neon Free ofrece 0,5 GB de almacenamiento por proyecto. Antes de cada ciclo
  se mide `pg_database_size`; desde 400 MiB se pausa la importación y el job
  informa error, conservando los datos. El margen absorbe crecimiento de un
  lote; **no es una cuota global**: escrituras de usuarios, importaciones manuales
  o copias también consumen espacio. Revisar el panel de Neon y el estado del job.
  No se promete que todas las fichas y reseñas de Steam entren en el plan gratuito.
- Ante fallos de Steam o de PostgreSQL, el job informa error sin imprimir
  credenciales; la siguiente ejecución retoma el checkpoint confirmado.
- El catálogo distingue una pausa normal entre tandas de un worker continuo
  caído. La última tanda y los errores quedan visibles desde la API compartida.

Desactivar `STEAM_CATALOG_ENABLED` detiene futuras tandas. No borra juegos.
No agregar tarjetas ni habilitar planes pagos para superar una cuota.
La aplicación local debe usar `scheduled` para no arrancar otro importador.

Fuentes: [Neon sin tarjeta](https://neon.com/faster),
[planes de Neon](https://neon.com/docs/introduction/plans),
[conexiones de Neon](https://neon.com/docs/connect/connect-from-any-app),
[horarios de GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[cuotas de Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
