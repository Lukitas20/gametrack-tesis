# Recomendación como función principal

La implementación posterior de IA entrenable y recomendaciones con amigos está
documentada en [IA_LOCAL_Y_AMIGOS.md](IA_LOCAL_Y_AMIGOS.md), incluyendo resultados
medidos, límites y verificación actualizada.

## Qué cambió

- Portada dedicada al descubrimiento, accesible sin sesión, con entrada principal
  a «¿Qué jugamos hoy?» y acceso a personalización.
- Selector de variedad: afinidad, equilibrio o mayor diversidad. El motor aplica
  MMR sobre candidatos; no promete novedades fuera del catálogo ni afinidad garantizada.
- El perfil aprende de valoraciones positivas y negativas. Las explicaciones sólo
  citan como gustos los juegos que efectivamente recibieron una nota positiva.
- El canal colaborativo requiere evidencia compartida y reduce su peso cuando
  esa evidencia es escasa. Ya no necesita una matriz densa de similitud catálogo².
- Motivos visibles, aportes numéricos aditivos y comparación de estrategias
  disponible sin ocupar el centro de la experiencia del jugador.
- Guardado en listas y valoración desde las sugerencias, con recálculo posterior.
- Asistente con edición de respuestas, otras opciones sin repetir las ya vistas,
  modo estricto, coincidencias y excepciones por juego.
- El filtrado del asistente busca en el universo recomendable antes de ampliar
  condiciones, conservando primero los juegos que cumplen todos los filtros.
- Estados vacíos, selección de opciones y progreso más claros; diseño adaptable
  y controles utilizables con teclado.

## Cómo demostrarlo

1. Abrir la portada sin sesión y completar el asistente.
2. Editar una respuesta desde los resultados y activar el modo estricto.
3. Pedir otras opciones; los resultados anteriores quedan excluidos de esa tanda.
4. Entrar con un jugador y ajustar géneros. Comparar la selección antes y después
   de valorar positivamente un juego y negativamente otro.
5. Alternar «Ir a lo seguro» y «Más variedad». El primer juego puede coincidir:
   la diversidad modifica el conjunto, no añade aleatoriedad gratuita.
6. Abrir la comparación de estrategias y los aportes de cada sugerencia. Si falta
   evidencia colaborativa, explicar por qué se usa contenido o popularidad.

## Qué falta medir para la tesis

Validación realizada el 24/09/2026: suite general con **201 pruebas aprobadas y
13 omitidas**; tras los últimos ajustes, **113 pruebas dirigidas aprobadas y 13
omitidas**. Las omitidas son los casos opcionales `GOLDEN_LIVE`, que requieren un
catálogo local específico. También se verificó la sintaxis JavaScript y, con la
aplicación en marcha, login demo, recomendaciones, modo estricto y alternativas
sin repetir resultados. La revisión visual en navegador queda pendiente porque
no había una conexión de Browser disponible.

Los tests de comportamiento verifican propiedades del sistema. No demuestran por
sí solos mayor precisión ni satisfacción de usuarios. Antes de afirmar una mejora:

- Comparar versión anterior y nueva sobre la misma foto de datos y usuarios.
- Separar entrenamiento y evaluación por tiempo; no usar la valoración del juego
  objetivo para construir el perfil que intenta recomendarlo.
- Medir NDCG@K, Precision@K y Recall@K con juicios independientes, además de
  cobertura de catálogo, diversidad intralista y latencia.
- Reportar resultados por usuarios nuevos, historial escaso y perfiles activos.
- Para el asistente, reutilizar `backend/scripts/eval_arnes.py` y el proceso de
  anotación ciega. Ampliar las anotaciones cuando cambie el pool; no reutilizar
  métricas anteriores como si correspondieran a este motor.
- Evaluar los pesos y la penalización de diversidad con un conjunto de validación,
  reservando consultas/usuarios de prueba para la comparación final.

## Límites y siguientes funciones útiles

Las horas de reseñadores son tiempo acumulado, no duración de campaña ni sesión.
Las etiquetas y el análisis de sentimiento tienen errores; la evidencia textual
ayuda a inspeccionarlos. Una cantidad mínima de valoraciones no garantiza que
existan vecinos colaborativos útiles. La actualización posterior del catálogo
reemplazó la matriz densa por CSR/CSC; ver [CATALOGO_STEAM.md](CATALOGO_STEAM.md).
Conviene medir también la memoria de metadatos y la latencia del quiz antes de
aumentar mucho los usuarios y el catálogo.

Prioridades para una siguiente etapa: feedback persistente «no me interesa»
separado de una valoración, exclusión opcional de biblioteca Steam realmente
importada, filtros de plataforma/precio con datos actualizados y una evaluación
con jugadores. Requieren datos, persistencia y evaluación propios; no se simulan
en la interfaz ni se presentan como funciones terminadas.
