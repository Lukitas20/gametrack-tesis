# GameTrack

Proyecto Final de Ingeniería · UADE · 2026

Marco Ambrosini y Lucas Gibellini

GameTrack recomienda videojuegos a jugadores y ofrece a los estudios análisis de reseñas y audiencias. La aplicación combina el catálogo de Steam, atributos de SteamSpy y las preferencias y experiencias registradas en GameTrack.

## Ejecutar en Windows

Con Python 3.13 y PowerShell, desde la raíz del repositorio:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd ..
.\INICIAR_GAMETRACK.ps1
```

Abrí [GameTrack](http://localhost:8000) para registrar una cuenta. FastAPI también sirve el frontend y la [documentación de la API](http://localhost:8000/docs). La configuración local predeterminada crea una base SQLite en `backend/gametrack.db`.

Una instalación nueva arranca con el catálogo vacío. Para importar una primera muestra desde Steam, con conexión a Internet, ejecutá desde `backend`:

```powershell
.\.venv\Scripts\python.exe scripts/seed_from_steam.py --source live --count 10 --snapshot-path data/generated/steam_catalog.json
```

Para habilitar la sincronización del índice y las funciones de perfil, copiá `backend/.env.example` a `backend/.env` en una instalación nueva y configurá `STEAM_API_KEY`. Consultá las guías del [catálogo](CATALOGO_STEAM.md) y de [acceso con Steam](ACCESO_STEAM.md).

## Datos y modelos

| Recurso | Contenido |
| --- | --- |
| [Procedencia](docs/REAL_DATA.md) | Criterios para usar observaciones reales de Steam y usuarios de GameTrack. |
| [Clasificador de reseñas](docs/IA_REAL.md) | Origen y particiones de las 24.000 reseñas en español. |
| [Modelo de afinidad](docs/IA_RECOMMENDATION.md) | Selección de los 110.499 votos, entrenamiento y evaluación. |
| [Catálogo de afinidad](backend/data/steam_affinity_catalog.json) | Atributos de los 50 juegos usados por el modelo. |
| [Recolección de votos](docs/STEAM_TRAINING_DATA.md) | Campos, deduplicación y tratamiento de los historiales. |
| [Audiencias](docs/AUDIENCIAS_HISTORIALES_20261017.md) | Análisis a partir de historiales públicos de Steam. |

## Pruebas y despliegue

Desde `backend`, ejecutá `.\.venv\Scripts\python.exe -m pytest -q`. Para verificar el frontend con Node.js, ejecutá `node frontend/scripts/check-syntax.mjs` y `node --test frontend/tests/*.test.mjs` desde la raíz.

Las guías describen el [despliegue con PostgreSQL y Docker Compose](DESPLIEGUE.md), el [worker programado con Neon](NEON_GRATIS.md), la [instalación en Google Cloud](deploy/google-cloud/README.md) y el [procedimiento de evaluación](docs/EVALUACION_REAL.md).
