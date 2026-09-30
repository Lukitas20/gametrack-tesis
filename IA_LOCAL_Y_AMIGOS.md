# IA local y recomendaciones con amigos

## Qué está implementado

- Modelo de recomendación entrenado desde cero con NumPy, en CPU. El entrenamiento
  y las predicciones funcionan localmente, sin API de modelos ni claves de IA.
- Estrategia **IA local** en Recomendaciones, comparación con los otros métodos y
  aviso de procedencia y vigencia del entrenamiento.
- Solicitudes de amistad, aceptación, rechazo, cancelación y eliminación.
- Selección de hasta cuatro amigos aceptados en **¿Qué jugamos?**: cinco personas
  contando al jugador que inicia la búsqueda.
- Afinidad por participante y dos criterios grupales: equilibrado y promedio.

El modelo aprendido y la regla que combina gustos del grupo son partes distintas.
La regla grupal está implementada y funciona también con perfiles de contenido;
no necesita volver a entrenar por cada combinación de amigos.

## Qué aprende la IA

`backend/app/ml/local_model.py` implementa factorización matricial con sesgos:

`nota estimada = media + sesgo del usuario + sesgo del juego + factores_usuario · factores_juego`

El ajuste minimiza el error sobre valoraciones explícitas de 1 a 5, con SGD y
regularización. No convierte juegos sin valorar en opiniones negativas. Por
defecto utiliza 12 factores, 100 épocas y semilla 42. Son parámetros iniciales,
no valores que ya hayan demostrado ser óptimos para jugadores reales.

El archivo guarda factores y sesgos de los juegos, conteos y metadatos. No guarda
nombres, emails ni vectores de usuarios. Al recomendar, el perfil se calcula
localmente con las notas actuales del jugador mediante mínimos cuadrados
regularizados. Necesita al menos tres notas sobre juegos conocidos por el modelo;
los juegos necesitan dos valoraciones de entrenamiento para aportar esa señal.
Sin evidencia suficiente se utilizan contenido y popularidad. En grupos, un
participante sin historial ni preferencias recibe afinidad neutra, sin inventarle gustos.

El peso de la señal aprendida se reduce cuando hay pocas valoraciones. En modo
automático e híbrido sólo se incorpora si supera la validación descrita abajo.
La opción explícita **IA local** permite experimentar y comparar aun cuando no
supere esa condición; puede recurrir a contenido cuando falte cobertura.

## Datos para entrenarlo

La fuente actual es `Rating(user_id, game_id, score, created_at)` de jugadores
activos de GameTrack. Los géneros y etiquetas alimentan el canal de contenido,
útil para empezar sin historial. Las reseñas alimentan ABSA, no se transforman
automáticamente en notas de entrenamiento. Tampoco se usan horas jugadas como
si fueran una calificación.

Para la tesis, conviene recolectar valoraciones de participantes reales sobre
juegos que conozcan, incluyendo opiniones positivas y negativas y algunos juegos
en común entre participantes. Las preferencias por género ayudan al arranque.
Conviene registrar después si cada sugerencia fue útil y si el grupo la eligió:
esos juicios permiten evaluar el ranking con evidencia independiente. Ese feedback
de elección grupal todavía no se persiste como un nuevo conjunto de entrenamiento.

Mantener una base separada para datos reales: no mezclar el seed con la muestra
de participantes y luego etiquetar el conjunto como observado. El parámetro
`--data-label` documenta la procedencia declarada; no comprueba que sea real.
Los mínimos técnicos de 20 notas, tres usuarios y tres juegos sólo permiten
ejecutar el entrenamiento; no garantizan calidad ni validez estadística.

## Entrenamiento reproducible

Desde la raíz del proyecto, con las dependencias del backend instaladas:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/train_local_model.py --data-label demo
```

Para una base que contenga exclusivamente valoraciones reales, configurar su
`DATABASE_URL` y ejecutar el mismo comando con `--data-label observed`.

Salidas por defecto, ignoradas por Git:

- `backend/data/generated/local_model.npz`: modelo local.
- `backend/data/generated/local_model.json`: parámetros, procedencia y evaluación.

`LOCAL_MODEL_PATH` cambia la ubicación que carga el servidor. `--output` cambia la
salida del entrenador; para servir esa salida también debe coincidir la configuración.
Los parámetros opcionales son `--factors` y `--epochs`. El motor detecta un nuevo
artefacto sin requerir entrenamiento durante cada solicitud. Los cambios de notas
se usan al calcular el perfil, pero actualizar factores generales requiere reentrenar.

El archivo se vincula a su base de datos y a los identificadores/slugs del catálogo:
no se aplica silenciosamente a otra base con IDs coincidentes. Un modelo ausente,
incompatible o ilegible no interrumpe las recomendaciones convencionales.

## Resultado medido y condición de activación

Entrenamiento local del 24/09/2026: **1.166 valoraciones sintéticas, 79 jugadores y
45 juegos**. Se reservó la última nota creada de cada usuario con al menos cinco
notas: 79 casos, todos cubiertos. Cada perfil de validación conserva sólo sus
notas de entrenamiento; el juego objetivo no forma parte de ese perfil.

| Método | RMSE ↓ | MAE ↓ |
|---|---:|---:|
| Modelo de factores | 0,7247 | 0,5619 |
| Referencia: media + sesgo de juego aprendido | 0,6195 | 0,4916 |

**El modelo no superó la referencia en esta muestra.** `automatic_eligible` queda
en `false`. Está disponible para comparación explícita, sin incorporarse al modo
automático ni a los perfiles grupales. Esta referencia reutiliza los sesgos
aprendidos; no es una evaluación de todo el motor híbrido ni de su ranking.

La activación exige al menos 20 casos reservados, cobertura completa, RMSE menor
y MAE no mayor que la referencia. Es una condición operativa inicial, no una
prueba de significancia. La partición es temporal **por usuario**, no un corte
cronológico global. Como se usa para decidir activación, es validación; hace falta
otra muestra independiente para informar resultados finales de tesis. Después
de evaluar se reentrena el artefacto servido con todas las notas disponibles.

Para demostrar utilidad, medir además NDCG@K/Precision@K, cobertura, diversidad,
satisfacción individual y acuerdo del grupo. Comparar sobre los mismos candidatos
y separar usuarios nuevos de usuarios con historial. No afirmar una mejora de
precisión a partir de los tests de software ni de esta muestra sintética.

## Cómo funciona la recomendación cruzada

1. En **Amigos**, enviar una solicitud usando el nombre de usuario exacto.
2. El destinatario debe aceptarla. Una solicitud pendiente no habilita su perfil.
3. Abrir **¿Qué jugamos?**, elegir compañía cooperativa u online y seleccionar
   amigos en el paso que aparece antes de la última pregunta.
4. Elegir **Equilibrado** o **Promedio**, completar el asistente y revisar la
   afinidad estimada de cada participante en las sugerencias.

El servidor comprueba autenticación y amistad aceptada en cada consulta. No
acepta IDs arbitrarios, amigos desactivados ni combinaciones incompatibles con
la modalidad. Los cambios de cuenta descartan selecciones y resultados anteriores.
La aceptación explica que los gustos podrán formar parte de recomendaciones
grupales; no se publica el historial completo ni el email de los participantes.

Con afinidades normalizadas entre 0 y 1:

- **Promedio:** media de las afinidades individuales.
- **Equilibrado:** `0,6 × media + 0,4 × afinidad mínima`. Si alguien calificó el
  juego con una o dos estrellas, ese índice se reduce a la mitad.
- El ranking combina `0,6 × afinidad grupal + 0,4 × ajuste al momento`; después
  se aplican las señales de aspectos y variedad del asistente.

Los juegos ya valorados pueden reaparecer: volver a jugarlos con amigos tiene
sentido. La modalidad grupal es obligatoria incluso si se amplían otros filtros:
sólo se incluyen juegos con etiquetas compatibles. Una etiqueta genérica de
multijugador no basta para afirmar que tiene modalidad online. La cantidad máxima
de jugadores y los requisitos de conexión/plataforma **no están verificados** en
el catálogo actual, y la interfaz lo advierte. Los índices son afinidades
estimadas, no probabilidades de que el juego guste.

## API y verificación

- `GET /api/v1/friends`: amigos y solicitudes recibidas/enviadas.
- `POST /api/v1/friends/requests`: `{ "username": "usuario" }`.
- `POST /api/v1/friends/requests/{id}/accept`: aceptar.
- `DELETE /api/v1/friends/requests/{id}`: rechazar o cancelar, según participante.
- `DELETE /api/v1/friends/{friend_id}`: quitar amistad.
- El asistente acepta `friend_ids` y `group_strategy` (`balanced` o `average`).
  Devuelve `group` y `group_fit` por sugerencia. Ver `/docs` para el esquema completo.

La nueva tabla se crea en el arranque local habitual. Para instalaciones
versionadas con Alembic se incluye la revisión `b621f7389c02`, posterior a
`f4b82d1e6a07`.

Validación: **287 pruebas aprobadas y 13 omitidas** en la suite completa; luego
**35 pruebas del modelo y recomendador aprobadas** tras agregar la condición de
activación. Las omitidas son casos opcionales `GOLDEN_LIVE`. Los tests cubren
consentimiento, acceso, afinidades opuestas, arranque en frío, modalidades,
revocación de amistad, actualización de notas y artefactos inválidos.
La revisión final del módulo local pasó **18 pruebas**, incluyendo siete variantes
de archivos inválidos y la inclusión/exclusión de IA según la validación, tanto
en recomendaciones automáticas como en perfiles grupales.

Comprobación HTTP sobre el servidor local: login demo, listado de amigos, diez
recomendaciones automáticas sin incorporar el modelo no validado, diez sugerencias
en IA local con aporte aprendido, asistente anónimo con tres resultados y rechazo
403 al intentar incluir un usuario sin amistad. Sintaxis JavaScript verificada.

Referencias metodológicas: [Koren, Bell y Volinsky, factorización matricial](https://doi.org/10.1109/MC.2009.263)
y [Fair Sequential Group Recommendations](https://homepages.tuni.fi/konstantinos.stefanidis/docs/sac20.pdf).
Los pesos concretos de GameTrack son decisiones iniciales de implementación y
necesitan validación propia.
