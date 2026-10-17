# Revisión de aplicación y evidencia — 17/10/2026

Se partió de `feature/IA`, commit `fafe5db`, igual a la rama remota al iniciar.
Se revisaron los cambios recientes y se conservaron el tipo permanente de cuenta,
sus permisos, los accesos por planes y los ajustes del índice de afinidad. No se
editó el Google Doc ni el LaTeX. No se implementaron pagos ni un chatbot generativo.

| Área | Ya existía | Cambio de esta revisión / cómo probar |
|---|---|---|
| Audiencias | No había un panel de segmentos integrado. | En un informe de juego o estudio → **Audiencias**. Muestra usuarios distintos con notas, reseñas, experiencias o listas públicas de esos juegos; preferencias manuales e inferidas se separan. Steam cuenta reseñas y votos por separado. Cada grupo informa criterio, tamaño, denominador y fuente. Grupos menores de cinco ocultan su tamaño. Sin datos suficientes se informa el faltante. El CSV incluye los agregados; no se publican identidades ni listas privadas. |
| Afinidad personal | Modelo integrado en GameTrackScore y recomendaciones; disponibilidad, aporte por resultado, validación y alternativas ya implementados. | Se verificaron artefacto y recorridos de cálculo existentes sin cambiar pesos ni habilitar cabezas. Abrir recomendaciones o un puntaje con cuenta de jugador y revisar el aporte indicado; disponer del modelo no implica que intervenga en cada juego. |
| Reseñas | Clasificador del voto del autor y seis temas integrado; reglas separadas para polaridad de cuatro aspectos; asistente con plantillas y evidencia. | El informe ahora publica `used` y `predicted`, y la interfaz distingue **disponible** de **utilizado en este informe**, incluso si se leyeron textos sin predicción. No se presenta tema como crítica ni como causa. |
| Evaluación | Arnés de variantes, rankings y hojas históricas; métricas unitarias y exportación CSV del informe. | Exportaciones de respuestas sin recalcular; paquetes locales con hashes, hoja ciega, juicios, errores y tiempos vacíos. Se preservan faltantes. El arnés antiguo no imputa ceros, no sobrescribe corridas y queda como ajuste exploratorio. Las capturas nuevas exigen auditoría externa de separación antes de publicar métricas. |
| Comprobaciones | Migraciones, pruebas PostgreSQL optativas, verificadores de artefactos y de afinidad en sólo lectura. | Se añadieron comprobación conjunta de modelos/audiencias, servidor local de carga de sólo lectura y procedimiento de respaldo/restauración entre dos bases de prueba. Se retiró el enlace “Elegir una cuenta de demostración”. |

## Datos reales y comprobaciones ejecutadas

- Artefactos auditados, sin reentrenar: reseñas `8af574d2…` (24.000 reseñas,
  2.687 juegos); afinidad `9d11c8ac…` (110.499 observaciones, 2.466 ejemplos de
  entrenamiento explícito, 50 juegos). La cabeza explícita de afinidad está
  habilitada; la de horas sigue **no habilitada**. Esos tamaños describen el
  corpus registrado, no participantes de una evaluación de uso.
- Base local existente, abierta con `mode=ro` y `query_only`: un perfil real
  comparable; 38/50 candidatos con aporte del modelo; 20 comprobaciones de
  conservación de historial propio; cero fallos de integridad. Clasificador
  utilizado en tres alcances: 245 reseñas muestreadas en total. Sin HTTP externo,
  escrituras, nuevas cuentas ni opiniones. Los tres alcances tenían cero usuarios
  GameTrack con interacciones y seis grupos publicables de reseñas Steam en total.
- Piloto privado: 50 reseñas reales guardadas, con 500 etiquetas humanas
  pendientes. Fuente SHA-256 `132b93df897435d7dd033a5147f7b4163171f819f716a7990fb8f6497c0716bf`.
  El evaluador devolvió `incomplete`, `judged=0`, `missing=500`, `metrics=null`.
  No es un holdout acreditado ni se usó para entrenar o ajustar.
- Carga local corregida con catálogo real, sólo GET de salud/listado: 50
  solicitudes, concurrencia 5, cero errores, 1,663 s; p50 199,47 ms y p95 556,81 ms.
  Se validaron HTTP y contenido (incluida conexión a base), y no aparecieron
  excepciones en el registro. Se corrigió el soporte de hilos del servidor de
  prueba manteniendo `mode=ro`/`query_only`; la primera corrida que sólo contaba
  estados HTTP se descartó como evidencia de ausencia de errores internos.
  Son observaciones de esta corrida sobre SQLite local, no capacidad de Cloud Run.
- Alembic: una cabeza `d8a104f3b927`; toda la cadena compiló SQL offline para
  PostgreSQL (17.692 caracteres). Estos cambios no necesitan nueva migración.
  Compilar SQL no equivale a ejecutar una migración o una restauración.
- PostgreSQL desplegado: la ejecución de sólo lectura `gametrack-train-xb2ht`
  verificó dos perfiles reales, 100 candidatos y 68 aportes del modelo; conservó
  dos notas explícitas y 40 señales de historial propio, sin fallos de integridad.
  El clasificador se utilizó sobre 246 reseñas de tres alcances, con seis grupos
  Steam publicables. Los usuarios GameTrack por alcance fueron 1, 0 y 0; esos
  grupos pequeños no se publicaron. Ambos artefactos coincidieron con los hashes
  aprobados y sólo la cabeza explícita de afinidad estaba habilitada.
  [Salida agregada, sin perfiles ni textos](evidence/application-runtime-20261017.json).
  La primera ejecución se conserva por separado como evidencia previa. La imagen
  final activa es `d701914b…`, revisión `gametrack-00023-g5w`, código `4dc73b4`.
- Pruebas automatizadas: 992 casos distintos de backend aprobados entre la
  suite general y las repeticiones de los cambios finales; 16 casos condicionales
  omitidos (3 PostgreSQL de prueba y 13 golden live de catálogo local). Pasaron
  24 pruebas de frontend. El único fallo inicial fue una comprobación de valores
  por defecto afectada por variables de entorno de la corrida; pasó al repetirla
  con los valores por defecto y una base efímera. La corrección del instrumento
  de carga también pasó 39 casos de transferencia/evaluación; esos casos ya están
  incluidos en el total, no se suman otra vez.

Las pruebas, publicación y verificación Cloud se registran al finalizar en
[DEPLOYMENT.md](../deploy/google-cloud/DEPLOYMENT.md).

## Lo que todavía no puede atribuirse a la tesis

No se realizaron pruebas con personas ni se midieron satisfacción, relevancia
personal o exactitud humana nueva. Los índices originales de pertenencia al corpus
de entrenamiento/ajuste no vienen en los artefactos; hace falta recuperarlos y
auditar intersecciones antes de considerar independiente una nueva muestra.
Se verificó en código la separación por juegos/textos del clasificador y por
autores/cronología de afinidad, así como la selección sólo con validación; no se
reentrenó el corpus original. Se intentó reconstruirlo con el selector original
sobre la base local actual: se obtuvieron 24.000 ejemplos, pero su huella **no
coincidió** con la publicada. Por eso esa base no acredita las particiones del
artefacto ni la independencia de las 50 reseñas piloto. Falta el corpus/snapshot
original de entrenamiento y sus índices, no cambiar etiquetas de procedencia.
No se recorrió de principio a fin una sesión real autenticada en la interfaz
publicada: se verificaron contratos HTTP en bases efímeras, el componente visual
con agregados reales y los servicios de inferencia sobre datos reales en Cloud,
sin crear cuentas ni saltar autenticación. Ese recorrido con una persona sigue
incluido en el procedimiento de evaluación.

No hay URL de PostgreSQL de pruebas ni herramientas `pg_dump`/`pg_restore`
disponibles en este equipo. Faltan ejecutar las pruebas PostgreSQL optativas y
restaurar un respaldo en un destino de pruebas vacío, comprobar datos, constraints,
secuencias y recorridos posteriores. No se restauró ni sobrescribió producción.
Tampoco se realizó carga con autenticación o inferencia concurrente en PostgreSQL.

El [procedimiento reproducible](EVALUACION_REAL.md) detalla cuentas reales,
instrumentos, anotación, auditoría de separación, comandos y accesos faltantes.
Las pruebas automatizadas usan fixtures efímeros que nunca se incorporan a los
datos operativos, al entrenamiento ni a la publicación; no sustituyen resultados
con personas.
