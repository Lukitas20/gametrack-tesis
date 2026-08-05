# GameTrack

**Plataforma inteligente de recomendación y análisis de videojuegos basada en
comportamiento de usuarios y reseñas.**

Proyecto Final de Ingeniería — Facultad de Ingeniería y Ciencias Exactas, UADE 2026
Ambrosini Marco · Gibellini Lucas

---

## Qué es

Prototipo funcional (MVP) con dos caras:

- **Jugador** — recomendaciones personalizadas mediante un motor híbrido que
  combina filtrado basado en contenido (TF-IDF + similitud coseno) y filtrado
  colaborativo (matriz de interacción usuario-ítem), con fallback por
  popularidad para usuarios nuevos (*cold start*).
- **Desarrollador** — analítica de reseñas con clasificación de sentimiento y
  análisis basado en aspectos (ABSA) sobre **jugabilidad, gráficos, historia y
  optimización**.

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.13 + FastAPI |
| ORM | SQLAlchemy 2.0 (estilo tipado) |
| Base de datos | SQLite (por defecto) · PostgreSQL opcional |
| IA | scikit-learn · NumPy · pandas |
| Frontend | HTML5 + CSS propio + JavaScript ES modules |
| Gráficos | SVG generado a mano (sin librería) |

SQLite es el default para que el prototipo arranque sin instalar ni levantar
nada. Para usar PostgreSQL alcanza con descomentar `psycopg2-binary` en
`requirements.txt` y definir `DATABASE_URL` en `backend/.env`; el resto del
código no cambia.

El frontend **no tiene paso de compilación**: son módulos ES nativos que sirve
la propia aplicación FastAPI. Un solo proceso levanta todo, no hay servidor de
desarrollo aparte, ni `node_modules`, ni CORS que mantener sincronizado. Los
gráficos son SVG propio en lugar de Chart.js, así que la demo no depende de
tener conexión ni de un CDN.

---

## Arranque

```powershell
cd backend

# 1. Entorno virtual e instalación
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Poblar la base con el dataset de demostración
.\.venv\Scripts\python.exe scripts\seed_data.py --reset

# 3. Analizar las reseñas con el módulo NLP
.\.venv\Scripts\python.exe scripts\analyze_reviews.py

# 4. Levantar la aplicación (API + frontend)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

En Linux/macOS es el mismo flujo con `python3 -m venv .venv` y
`source .venv/bin/activate`.

Abrir **`http://localhost:8000`** — la interfaz y la API salen del mismo
proceso. Documentación interactiva en `/docs`.

### Cuentas de demostración

Contraseña para todas: `demo1234`

| Usuario | Rol | Para qué sirve |
|---------|-----|----------------|
| `jugador.demo` | Jugador | Historial abundante (26 juegos valorados) |
| `nuevo.demo` | Jugador | **Sin ninguna interacción**: demuestra el arranque en frío |
| `dev.demo` | Desarrollador | Panel de analítica del estudio CD Projekt Red |

---

## Estructura

```
gametrack/
├── backend/
│   ├── app/
│   │   ├── main.py              # Aplicación FastAPI
│   │   ├── api/
│   │   │   ├── deps.py          # Dependencias (usuario actual, rol)
│   │   │   └── v1/endpoints/    # Endpoints REST por recurso
│   │   ├── core/
│   │   │   ├── config.py        # Configuración (pydantic-settings)
│   │   │   └── security.py      # bcrypt + JWT
│   │   ├── db/
│   │   │   ├── database.py      # Engine, sesión, Base declarativa
│   │   │   ├── base.py          # Metadata completo + init_db / drop_db
│   │   │   └── types.py         # Columnas Enum portables
│   │   ├── models/              # Entidades SQLAlchemy
│   │   ├── schemas/             # Schemas Pydantic
│   │   ├── services/            # Lógica de negocio
│   │   └── ml/
│   │       ├── recommender.py   # Motor híbrido de recomendación
│   │       ├── analytics.py     # NLP: sentimiento + ABSA + agregación
│   │       └── lexicon.py       # Recursos lingüísticos en español
│   ├── data/
│   │   ├── games_seed.json      # Catálogo curado de 45 juegos reales
│   │   └── generated/           # Salidas del seed (ignorado por git)
│   ├── scripts/
│   │   ├── seed_data.py         # Generador del dataset de demostración
│   │   ├── review_corpus.py     # Frases en español para las reseñas
│   │   ├── analyze_reviews.py   # Corre y evalúa el módulo NLP
│   │   └── validate_palette.py  # Valida la paleta de los gráficos
│   └── tests/
└── frontend/
    ├── index.html
    ├── css/
    │   ├── tokens.css           # Paleta, tipografía, espaciado (claro/oscuro)
    │   ├── base.css             # Reset y esqueleto
    │   └── components.css       # Tarjetas, gráficos, modales, formularios
    └── js/
        ├── app.js               # Cabecera, navegación y rutas
        ├── api.js               # Cliente de la API
        ├── store.js             # Sesión y cachés
        ├── router.js            # Router por hash
        ├── charts.js            # Primitivas de gráficos SVG
        ├── components.js        # Piezas compartidas entre vistas
        ├── ui.js               # Construcción de DOM, íconos, avisos
        └── views/               # Una vista por pantalla
```

## Modelo de datos

```
User ──< UserPreference >── Genre ──< Game >── Tag
  │                                    │
  ├──< Rating >─────────────────────────┤
  ├──< Review >────────────────────────┤
  │      └──< ReviewAspect                │
  └──< GameList ──< GameListItem >────────┘
```

Notas de diseño:

- **`Rating`** guarda el puntaje explícito (1–5) *y* señales implícitas
  (`hours_played`, `status`). Es la fila que alimenta la matriz usuario-ítem
  del filtrado colaborativo.
- **`Review`** nace con `is_analyzed = False` y los campos de sentimiento en
  nulo. Los completa el módulo NLP; el flag distingue "todavía sin procesar"
  de "procesada y resultó neutra".
- **`ReviewAspect`** guarda una fila por aspecto **efectivamente mencionado**
  en el texto (entre 0 y 4 por reseña), con el fragmento que justifica la
  clasificación en `evidence` para dar explicabilidad.
- **`Game`** desnormaliza `avg_rating`, `ratings_count` y `reviews_count`
  porque son los campos que consulta el fallback por popularidad, que debe
  responder sin recalcular agregados en cada request.
- Los enums se persisten como texto en español (`jugador`, `optimizacion`),
  legibles al inspeccionar la base y portables entre SQLite y PostgreSQL.

---

## El dataset de demostración

`scripts/seed_data.py` genera datos **determinísticos** (semilla fija, `--seed`
para variarla):

| Entidad | Cantidad |
|---------|---------:|
| Juegos | 45 |
| Géneros / etiquetas | 14 / 84 |
| Jugadores | 80 |
| Desarrolladores | 6 |
| Ratings | ~1.170 |
| Reseñas en español | ~415 |
| Listas | ~260 |

```powershell
python scripts\seed_data.py --reset            # regenera desde cero
python scripts\seed_data.py --players 150      # más usuarios
python scripts\seed_data.py --seed 7           # otra muestra aleatoria
python scripts\seed_data.py --source rawg      # catálogo real desde RAWG
```

### Cómo se generan los datos (y por qué)

Los datos son sintéticos pero **no aleatorios**: están construidos para que los
modelos tengan estructura real que descubrir.

- **Perfiles de gusto.** Cada jugador recibe uno de 8 arquetipos (*RPG
  narrativo*, *Shooter competitivo*, *Estrategia y gestión*, …) con una
  variación individual. Eso crea grupos de usuarios con comportamiento
  parecido, que es exactamente la estructura latente que el filtrado
  colaborativo debe encontrar.
- **Puntajes.** Combinan la afinidad del usuario con el juego y la calidad
  objetiva del título (Metacritic reescalado a [0,1], porque el rango real
  50–95 es demasiado angosto para diferenciar), más ruido gaussiano. El
  resultado reproduce el sesgo positivo típico de datos reales de rating, con
  Fallout 76 y Battlefield 2042 al fondo de la tabla.
- **Reseñas.** Cada juego del dataset curado tiene un `aspect_profile` con la
  percepción de la comunidad por aspecto. Las reseñas se componen a partir de
  ese perfil, del puntaje del autor y de un "temperamento" propio del
  reseñador, de modo que aparecen minorías que discrepan del consenso. El
  resultado es señal ABSA verificable: Cyberpunk 2077 sale con optimización
  netamente negativa mientras su historia y sus gráficos son positivos.

### Ground truth para evaluar el ABSA

Las reseñas se insertan **sin analizar** (`is_analyzed = False`), y el seed
guarda aparte las etiquetas con las que fueron generadas:

```
backend/data/generated/reviews_ground_truth.json
```

Sirve para medir precisión y recall del módulo NLP contra un etiquetado
conocido, sin tener que anotar reseñas a mano. Guarda dos etiquetas globales
distintas por reseña —`sentimiento_texto` y `sentimiento_por_puntaje`— por los
motivos que se explican en [Texto contra puntaje](#texto-contra-puntaje).

### Limitaciones conocidas

- La matriz usuario-ítem tiene ~33 % de densidad, muy por encima de un sistema
  real (típicamente <1 %). Es una consecuencia de tener 45 juegos y 80
  usuarios, y favorece al filtrado colaborativo. Para evaluar en condiciones
  más realistas conviene ampliar el catálogo con `--source rawg`.
- Metacritic, fechas y horas de juego del dataset local son aproximados y
  sirven como datos de demostración. La fuente autoritativa es el importador
  de RAWG.
- El dataset local no trae imágenes (`background_image` queda nulo); las trae
  RAWG cuando se importa desde ahí.

### Importar juegos reales desde RAWG

Requiere una clave gratuita de [rawg.io/apidocs](https://rawg.io/apidocs) en
`backend/.env`:

```
RAWG_API_KEY=tu-clave
```

```powershell
python scripts\seed_data.py --reset --source rawg --rawg-pages 5
```

RAWG no expone valoraciones por aspecto, así que para los juegos importados el
perfil por aspecto se deriva de Metacritic: sirven para poblar el catálogo,
no para evaluar el ABSA.

---

---

## Motor de recomendación

`app/ml/recommender.py` combina tres estrategias, elegidas según cuánto se
sepa del usuario, de modo que el arranque en frío degrada de forma gradual en
lugar de fallar:

| Estrategia | Cómo funciona | Cuándo se usa |
|-----------|---------------|---------------|
| **Contenido** | TF-IDF (unigramas + bigramas) sobre géneros, etiquetas, desarrollador y descripción; similitud coseno | Desde la 1.ª valoración, o con sólo las preferencias del onboarding |
| **Colaborativo** | Filtrado ítem-ítem sobre la matriz usuario-ítem centrada por usuario, 20 vecinos | Con historial suficiente (≥ 3 valoraciones) |
| **Híbrido** | Suma ponderada de ambas, normalizadas a [0,1] (40 % contenido / 60 % colaborativo, configurable) | Caso general |
| **Popularidad** | Media bayesiana, para que un 5,0 con dos votos no supere a un 4,6 con doscientos | Piso: responde siempre |

Decisiones que vale la pena justificar:

- **Ítem-ítem y no usuario-usuario.** Las similitudes entre juegos son mucho
  más estables que entre personas cuando el catálogo es chico y los usuarios
  entran y salen.
- **Centrado por usuario.** Neutraliza que unos puntúen alto y otros bajo: lo
  que importa es cuánto se aparta cada nota de la media de quien la puso.
- **Normalización antes de combinar.** Sumar una similitud coseno (0 a 1) con
  una predicción de rating (1 a 5) no tendría sentido sin llevar ambas a la
  misma escala.
- **Toda recomendación explica su motivo** ("Se parece a God of War, que
  valoraste bien") y expone el aporte de cada estrategia, así que la
  combinación es auditable y no una caja negra.

El motor se entrena una vez y se cachea; se reconstruye solo cuando cambian
los datos.

```powershell
# Comparar las estrategias entre sí sobre el mismo usuario
curl "localhost:8000/api/v1/recommendations?strategy=contenido" -H "Authorization: Bearer <token>"
curl "localhost:8000/api/v1/recommendations?strategy=colaborativo" -H ...
```

---

## Módulo NLP y ABSA

`app/ml/analytics.py` clasifica el sentimiento global de cada reseña y su
sentimiento hacia cuatro aspectos: **jugabilidad, gráficos, historia y
optimización**.

El enfoque es **basado en léxico y reglas**, no supervisado. Se eligió así
porque la plataforma no tiene reseñas etiquetadas por humanos, y porque cada
decisión queda respaldada por la cláusula concreta que la produjo — esa
evidencia se guarda en `ReviewAspect.evidence` y es lo que alimenta las citas
del panel de desarrollador.

El pipeline por reseña:

1. **Segmentación** en oraciones y, dentro de ellas, en cláusulas separadas
   por conectores adversativos. Así *"los gráficos son lindos pero corre mal"*
   no promedia las dos opiniones en una sola.
2. **Detección de aspectos** por cláusula, con vocabularios ponderados. Los
   términos *débiles* (`corre`, `funciona`) sólo cuentan si la cláusula no
   menciona ningún término fuerte, lo que evita que *"las mecánicas funcionan
   bien"* se clasifique como optimización. La coincidencia más larga
   desambigua *"misiones secundarias"* (historia) de *"diseño de misiones"*
   (jugabilidad).
3. **Polaridad** con negación (`no encontré un solo bug` → positivo),
   intensificadores y atenuadores. Los modificadores se buscan **a ambos
   lados** porque en español el adjetivo va detrás del sustantivo
   (*"decepción enorme"*).
4. **Agregación** por aspecto. Una cláusula sin aspecto propio se atribuye a
   la anterior de la misma oración, porque la opinión sigue sobre el mismo
   tema.

Se emite una opinión **sólo si hay carga sentimental**: mencionar un aspecto
no es opinar sobre él.

### Resultados medidos

Contra las 416 reseñas del seed y su ground truth
(`scripts/analyze_reviews.py --evaluate`):

| Métrica | Valor |
|---------|------:|
| Sentimiento global — exactitud | **91,6 %** |
| Sentimiento global — F1 macro | **0,844** |
| ABSA — precisión / recall / F1 de detección | **1,000 / 1,000 / 1,000** |
| ABSA — exactitud de polaridad | **92,2 %** |

Por aspecto, la polaridad acierta entre 84 % (gráficos) y 99 % (historia y
optimización). La clase más difícil del sentimiento global es la neutra
(F1 0,73): separar "apenas pasable" de "bueno" es genuinamente ambiguo, y
términos como *correcto* o *cumple* están justo en el límite.

> **Cómo leer el F1 = 1,000.** Las reseñas del seed se componen a partir de un
> corpus cerrado de frases (`scripts/review_corpus.py`), así que el vocabulario
> de aspectos está completamente cubierto. Sobre reseñas reales el recall
> bajaría: la métrica confirma que las reglas de desambiguación funcionan y que
> no hay falsos positivos, **no** que el módulo generalice a texto abierto. El
> paso natural para eso es reemplazar el léxico por un modelo entrenado en
> español (BETO, RoBERTuito); la interfaz de `analyze_text` no cambiaría.

### Texto contra puntaje

El evaluador también reporta que **el texto de una reseña coincide con la banda
de su puntaje sólo en el 67,8 % de los casos**: hay gente que puntúa 3 y
escribe elogios, y gente que puntúa alto y sólo se queja. Es una propiedad de
los datos, no un error del módulo, y es la razón por la que el ground truth
guarda las dos etiquetas por separado (`sentimiento_texto` y
`sentimiento_por_puntaje`). Medir el clasificador contra la banda del puntaje
lo penalizaría por algo que no puede leer.

### Probar el NLP en vivo

```powershell
curl -X POST localhost:8000/api/v1/reviews/analyze `
  -H "Content-Type: application/json" `
  -d '{"content":"La historia es magnifica pero el rendimiento es un desastre: crashea cada media hora."}'
```

Devuelve historia → positivo y optimización → negativo, cada uno con la frase
que lo justifica.

---

## Endpoints

| Método | Ruta | Rol | Descripción |
|--------|------|-----|-------------|
| POST | `/auth/register`, `/auth/login` | — | Alta y login (JWT) |
| GET | `/auth/me` | autenticado | Perfil actual |
| PUT | `/auth/me/preferences` | autenticado | Géneros del onboarding |
| GET | `/genres` | — | Géneros del catálogo |
| GET | `/games` | — | Listado con búsqueda, filtros, orden y paginación |
| GET | `/games/{id}` | — | Detalle |
| GET | `/games/{id}/similar` | — | Similares por contenido |
| GET | `/games/{id}/reviews` | — | Reseñas con su análisis |
| GET | `/recommendations` | jugador | Recomendaciones (`?strategy=`) |
| GET | `/me/ratings` | autenticado | Historial propio |
| POST | `/ratings` | autenticado | Valorar (recalcula recomendaciones) |
| POST | `/reviews` | autenticado | Publicar reseña (se analiza al instante) |
| POST | `/reviews/analyze` | — | Analizar texto suelto sin guardarlo |
| GET | `/analytics/overview` | **desarrollador** | Referencia global del catálogo |
| GET | `/analytics/studio` | **desarrollador** | Agregado del estudio |
| GET | `/analytics/games/{id}` | **desarrollador** | Sentimiento y aspectos de un juego |
| POST | `/analytics/process` | **desarrollador** | Correr el NLP sobre las pendientes |
| POST | `/steam/import/{appid}` | autenticado | Importar un juego desde Steam |
| POST | `/steam/link` | autenticado | Vincular una cuenta de Steam |
| GET | `/steam/owned/{steam_id}` | autenticado | Biblioteca de una cuenta de Steam |

Documentación interactiva completa en `/docs`.

---

## Integración con Steam

Permite ampliar el catálogo más allá del dataset curado y vincular la cuenta de
un jugador.

Importar la ficha de un juego **no requiere clave**: sale de la API pública de
la tienda. Consultar la biblioteca de un usuario o su perfil sí necesita
`STEAM_API_KEY` en `backend/.env`; sin ella esos endpoints devuelven vacío en
lugar de fallar, para que la falta de configuración no rompa la interfaz.

```powershell
# Half-Life 2 (AppID 220) al catálogo
curl -X POST localhost:8000/api/v1/steam/import/220 -H "Authorization: Bearer <token>"
```

De la ficha de Steam se toma la descripción **corta**: la larga viene con HTML
que ensuciaría tanto la vista como el corpus TF-IDF del recomendador. Los
géneros se traducen al español para que convivan con los del catálogo, y las
categorías de Steam ("Un jugador", "Cooperativo") se mapean a etiquetas. Al
importar se invalida el modelo del recomendador, así que el juego nuevo entra
en las recomendaciones sin reiniciar nada.

### Sincronización en tiempo real

Un juego importado de Steam no queda congelado: al visitar su ficha, sus
similares, sus reseñas o su analítica de desarrollador, `steam_service.
maybe_refresh` chequea cuánto hace que no se sincroniza (`STEAM_SYNC_TTL_MINUTES`,
6 horas por defecto) y si corresponde vuelve a pedir la ficha y trae las
reseñas nuevas que hayan aparecido en Steam, sin duplicar las que ya estaban
(se identifican por `steam_review_id`, el `recommendationid` de Steam). Si
Steam no responde, la página se sirve igual con los datos que ya había.

No hay un proceso aparte sondeando todo el catálogo: el refresco es perezoso
y sólo alcanza a los juegos que alguien efectivamente está mirando.

### Catálogo compartido por el equipo

El listado de "más vendidos" de Steam depende de cuándo se lo pida, y la base
de datos (`*.db`) está en `.gitignore` — cada instalación local es su propia
foto. Sin un paso extra, dos personas del equipo que importan juegos de Steam
por su cuenta terminan viendo catálogos distintos.

`scripts/seed_from_steam.py` resuelve esto con un snapshot versionado en el
repo, `backend/data/steam_catalog.json` (mismo criterio que `games_seed.json`
para el dataset curado):

```powershell
# Uso normal: siembra desde el snapshot que ya está en el repo, sin red.
python scripts\seed_from_steam.py --reset

# Sólo lo corre quien quiera ampliar o refrescar el catálogo compartido:
# pega contra Steam de verdad y ACTUALIZA data/steam_catalog.json.
python scripts\seed_from_steam.py --source live --count 150
```

Después de un `--source live`, el JSON generado se commitea; el resto del
equipo lo obtiene con un `git pull` normal y siembra su base local con
`--source snapshot` (el default), sin necesitar `STEAM_API_KEY` propia ni
esperar a Steam. Tanto el pull de Steam como la siembra en la base son
incrementales: correrlo de nuevo sólo agrega juegos y reseñas nuevas, no
duplica lo que ya está.

"Todos" los juegos de Steam (cientos de miles, en su mayoría irrelevantes)
no es un objetivo realista: Steam no expone un endpoint así. El snapshot
cubre los más vendidos y más reseñados, ajustable con `--count`.

---

## Migraciones

El prototipo crea el esquema con `create_all` al arrancar, que alcanza para la
demo. Alembic está configurado para cuando haga falta versionar cambios de
esquema o desplegar sobre PostgreSQL, donde `create_all` no alcanza.

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head           # aplicar
.\.venv\Scripts\alembic.exe revision --autogenerate -m "descripción"
```

La revisión `002fdea0cfa6` es una **línea base limpia**: reemplaza a las dos
migraciones anteriores del repositorio, que describían un esquema previo a la
reestructuración y habrían dejado una base incompatible con el ORM. Siguen
disponibles en el historial (`git show 4ec4e80:backend/alembic/versions/`).

Tres pruebas comparan el esquema que producen las migraciones contra el que
produce `create_all`, tabla por tabla, columna por columna e índice por índice.
Una migración desincronizada del ORM es peor que no tener migraciones: da una
falsa sensación de control y falla recién en el despliegue.

---

## Frontend

Aplicación de una sola página, sin dependencias ni paso de compilación. Se
sirve desde la propia FastAPI, así que `uvicorn app.main:app` levanta la demo
completa.

### Vista de jugador

| Pantalla | Qué muestra |
|----------|-------------|
| **Para vos** | Recomendaciones con la justificación en lenguaje natural en cada tarjeta, selector de estrategia y comparador de las cuatro lado a lado |
| **Catálogo** | Búsqueda por nombre o desarrollador, filtros por género y etiqueta, cinco criterios de orden y paginación |
| **Detalle de juego** | Ficha, valoración con estrellas, guardar en lista, publicar reseña y juegos parecidos |
| **Mis listas** | Favoritos, Jugando y Pendientes más las listas propias |

Dos piezas hechas para la defensa:

- **Comparador de estrategias.** El mismo usuario, en el mismo momento, con las
  cuatro estrategias en columnas, más una tabla de solape que cuenta cuántos
  títulos comparte cada par. Es la forma directa de mostrar que cada señal
  aporta algo distinto y que el híbrido no es una caja negra. Una tabla
  desplegable expone además el aporte numérico de cada estrategia por juego.
  Enlazable: `#/recomendaciones?comparar=1`, o
  `?estrategia=colaborativo` para entrar con una estrategia forzada.

  > **Un hallazgo del propio comparador.** Con `jugador.demo`, el top 6 del
  > híbrido coincide en **6 de 6** con el del colaborativo, mientras que con el
  > de contenido comparte sólo 3. Con los pesos actuales (40 % contenido / 60 %
  > colaborativo) y una matriz tan densa como la del seed, la señal colaborativa
  > domina el orden final. Es un resultado defendible, pero conviene decirlo en
  > la tesis en lugar de presentar el híbrido como un balance parejo. Los pesos
  > se ajustan sin tocar código, con `REC_CONTENT_WEIGHT` y `REC_COLLAB_WEIGHT`
  > en `backend/.env`.
- **Análisis en vivo al escribir la reseña.** El textarea consulta
  `/reviews/analyze` mientras se tipea — ese endpoint no persiste nada — así que
  el ABSA se ve funcionando sobre texto escrito a mano en el momento, con la
  frase que justifica cada aspecto.

Con la cuenta `nuevo.demo` la vista de recomendaciones muestra el aviso de
arranque en frío con el umbral explícito (0 de 3 valoraciones necesarias para el
filtrado colaborativo) y un botón para elegir géneros, que es lo que hace pasar
al usuario de popularidad a contenido.

### Vista de desarrollador

Panel del estudio con la recepción de cada título, el desglose ABSA de los
cuatro aspectos, la comparación contra el promedio del catálogo y las citas
textuales que respaldan cada valoración negativa. Con `dev.demo`, Cyberpunk 2077
aparece con **historia +1,00 y optimización −0,38**, respaldado por la cita
"Crashea cada media hora y perdí progreso más de una vez".

### Asistente "¿Qué jugamos hoy?"

Tres preguntas (tiempo disponible, ánimo, con quién) que se traducen a filtros
del catálogo y devuelven tres títulos. La API filtra por una etiqueta a la vez,
así que se consulta cada etiqueta candidata por separado y se intersecan los
resultados; si la intersección queda corta, se relajan los criterios en orden y
**se avisa cuál se soltó**, en lugar de devolver una lista vacía o fingir que el
filtro se aplicó.

### Decisiones de visualización

- **Barra apilada divergente en lugar de torta** para la polaridad. El
  sentimiento es una escala ordenada (negativo → neutro → positivo), y centrando
  las neutras en el cero se pueden comparar las longitudes de cada lado entre
  títulos. Una torta de tres porciones con valores cercanos no permite eso.
- **Azul ↔ rojo con gris neutro**, no verde ↔ rojo. Verde y rojo es la peor
  combinación posible para las dicromacias más frecuentes.
- **La paleta está validada, no elegida a ojo.**
  `scripts/validate_palette.py` mide contraste WCAG contra cada superficie y
  separación perceptual en OKLab simulando protanopia, deuteranopia y
  tritanopia con el modelo de Viénot, Brettel & Mollon. Encontró un problema
  real: con el mismo gris neutro en los dos temas, el par neutro/negativo caía a
  ΔE 4,2 bajo protanopia en modo oscuro (el umbral es 8). El gris del modo
  oscuro es más oscuro por eso, y el par quedó en 11,7.
- **El color nunca es el único portador de significado**: cada gráfico lleva
  leyenda, etiquetas directas y una vista de tabla equivalente, y los chips de
  sentimiento combinan punto de color con texto.

---

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```

75 pruebas sobre el modelo de datos, el módulo NLP (negación, desambiguación de
aspectos, herencia de cláusulas), el recomendador (cada estrategia, el arranque
en frío y la degradación entre ellas), la API (listas, filtros, permisos por rol
y coherencia de los datos que consume el panel), la integración con Steam (sin
tocar la red) y la correspondencia entre las migraciones y el ORM.

Además, `scripts/validate_palette.py` valida la paleta de los gráficos y
`scripts/analyze_reviews.py --evaluate` mide el módulo NLP contra el ground
truth.

---

## Estado

- [x] **Paso 1** — Estructura, modelo de datos y seed
- [x] **Paso 2** — Motor de recomendación híbrido y módulo NLP/ABSA
- [x] **Paso 3** — Frontend con las vistas de jugador y desarrollador
