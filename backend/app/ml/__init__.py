"""Módulos de inteligencia artificial de GameTrack.

- ``recommender.py`` — motor híbrido: TF-IDF + similitud coseno (contenido),
  filtrado colaborativo ítem-ítem sobre la matriz usuario-ítem, combinación
  ponderada y fallback por popularidad para el arranque en frío.
- ``analytics.py`` — módulo NLP: clasificación de sentimiento
  (positivo / neutro / negativo) y análisis basado en aspectos sobre
  jugabilidad, gráficos, historia y optimización, más la agregación que
  consume el panel de desarrollador.
- ``lexicon.py`` — recursos lingüísticos en español (léxico de polaridad,
  modificadores, vocabularios de aspecto y palabras vacías).
"""
