# GameTrack — Plataforma de recomendación de videojuegos

PFI - Facultad de Ingeniería y Ciencias Exactas - UADE 2026  
Ambrosini Marco · Gibellini Lucas

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.12 + FastAPI |
| Base de datos | PostgreSQL 16 |
| Caché / sesiones | Redis 7 |
| Frontend | React 18 + Vite + TypeScript |
| IA | scikit-learn + PyTorch |
| Contenedores | Docker Compose |

## Arrancar en local

```bash
# 1. Clonar el repo
git clone <url>
cd gametrack

# 2. Configurar variables de entorno
cp backend/.env.example backend/.env
# Editar backend/.env con tus valores

# 3. Levantar servicios
docker compose up --build

# 4. Verificar que todo funciona
curl http://localhost:8000/health
```

La API queda disponible en `http://localhost:8000`  
Documentación interactiva: `http://localhost:8000/docs`

## Estructura

```
gametrack/
├── backend/          # FastAPI
│   ├── app/
│   │   ├── api/      # Endpoints REST
│   │   ├── core/     # Config, seguridad
│   │   ├── db/       # Conexión a BD
│   │   ├── models/   # Modelos SQLAlchemy
│   │   ├── schemas/  # Schemas Pydantic
│   │   └── services/ # Lógica de negocio
│   └── tests/
├── frontend/         # React + Vite
├── ml/               # Modelos de IA
└── docker-compose.yml
```
