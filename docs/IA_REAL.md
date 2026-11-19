# Modelo local de reseñas reales

El clasificador de reseñas se ejecuta dentro de la aplicación, en CPU y sin
servicios externos. Es distinto del recomendador de valoraciones demo retirado.
No es un chatbot ni un generador de texto. Su salida complementa el análisis
de reseñas; no se convierte en una valoración del usuario ni en un GameTrackScore.

En la aplicación se usa dentro de **Desarrollador**, en el informe del estudio
y en las métricas de un juego, incluida la comparación desde el catálogo.
El bloque «Lo que detecta la IA local» muestra temas y fragmentos reales. El
asistente puede explicar esa muestra. No hace falta elegir una estrategia de
recomendaciones ni activar el antiguo botón «IA local».

Las rutas protegidas por rol de desarrollador son `/api/v1/analytics/review-model`
(estado y procedencia), `/api/v1/analytics/games/{id}` y el informe de estudio
(incluyen `trained_ai`). El estado `ready` confirma carga; `sampled > 0`
confirma que además se ejecutó inferencia sobre reseñas del informe. Si no hay
textos en español, se indica la falta de muestra y no se generan resultados.

## Código y artefactos

- `backend/app/ml/trained_reviews.py`: carga los pesos y predice recepción y temas.
- `backend/scripts/train_review_model.py`: entrenamiento reproducible desde
  reseñas reales de Steam; no se ejecuta al arrancar el servidor ni al construir.
- `backend/models/review_model.npz`: vocabulario TF-IDF y pesos de regresión
  logística, sin pickle. Tamaño: 1.196.056 bytes.
- `backend/models/review_model.json`: metadatos del entrenamiento y límites.
  Tamaño: 5.049 bytes.
- `backend/scripts/verify_review_model.py`: verifica hashes, procedencia, igualdad
  de metadatos internos y externos, dimensiones y valores finitos antes del build.

`REVIEW_MODEL_PATH` apunta por defecto al artefacto incluido en `backend/models`.
En el contenedor se fija `/app/backend/models/review_model.npz`. No depende del
bucket `/models/versions/…/local_model.npz` del antiguo recomendador.

## Procedencia del entrenamiento existente

El artefacto fue entrenado el 5 de octubre de 2026 con 24.000 reseñas reales en
español de 2.687 juegos de Steam. Su origen registrado es `real_steam_reviews`.
Los textos normalizados se deduplicaron y los juegos se separaron entre
entrenamiento, validación y prueba. El reparto registrado es:

| Partición | Reseñas | Juegos |
|---|---:|---:|
| Entrenamiento | 19.044 | 2.142 |
| Validación | 2.425 | 273 |
| Prueba | 2.531 | 272 |

La recepción aprende la recomendación positiva/negativa declarada por el autor
en Steam. No representa el gusto del usuario actual ni el sentimiento de cada
frase. En la prueba histórica obtuvo exactitud balanceada 0,8396, frente a 0,5
de predecir siempre la clase mayoritaria. Es una evaluación histórica del
artefacto conservado, no una nueva evaluación realizada al publicarlo.

Los seis temas —jugabilidad, gráficos, historia, rendimiento, monetización y
dificultad— usan supervisión débil con vocabulario curado aplicado a textos
reales. Sus métricas miden acuerdo con esas etiquetas automáticas; no son
exactitud frente a anotaciones humanas. No se inventaron reseñas para entrenar.

Los archivos originales locales y su respaldo coinciden byte por byte con los
artefactos versionados. Esta comprobación de integridad y los metadatos no
sustituyen una auditoría independiente de cada texto del corpus. No se incluye
el corpus completo, identidades de autores ni bases de datos en la imagen.

## Integridad y publicación

| Archivo | SHA-256 |
|---|---|
| `review_model.npz` | `8af574d2fa306364f1689bafe30c257c53dfe8d6d35ad3b7143a851b991d9789` |
| `review_model.json` | `be03c3c241a8d214829578d4e43b4174371e5ae5e9fe259420c2f204b55276bd` |

Huella del dataset registrada:
`f8a1ee239c552cc78b93e9359244546702aa5bc0dfc0b737cbd1f1130e91246e`.

Docker comprueba estos hashes y detiene la construcción si falta un archivo,
cambia su contenido o no coincide la procedencia. `.gitattributes` conserva los
bytes del JSON al moverlo entre Windows y Linux. Git, Docker y Cloud Build
excluyen otros pesos NPZ, pickle y joblib. Además del par de reseñas, se permite
el JSON auditado de [la IA de afinidad](IA_RECOMMENDATION.md), con su propio
verificador y huella. Se mantienen excluidos `.env`, bases y datos generados.

Desde `backend` se puede verificar sin red ni acceso a una base:

```bash
python scripts/verify_review_model.py
```

Para actualizar el modelo se necesita entrenar exclusivamente con observaciones
reales, revisar procedencia, evaluación y límites, y actualizar los hashes
auditados. No se aprueba un artefacto cambiándole una etiqueta. Consultar también
[la política de datos reales](REAL_DATA.md).
