# Procedencia de los datos

El usuario prohíbe datos sintéticos desde el 14 de octubre de 2026.

- Los datos operativos, análisis y entrenamientos deben utilizar exclusivamente observaciones reales de Steam o de usuarios reales de GameTrack.
- No activar artefactos de demostración, generar reseñas/valoraciones/perfiles ficticios ni usar datos históricos demo para rellenar recomendaciones o métricas.
- No convertir un conjunto sintético en real cambiando su etiqueta a `observed`.
- Mantener los históricos demo excluidos; no borrar datos reales para realizar una limpieza.
- Las pruebas unitarias aisladas no son datasets operativos: sus fixtures y artefactos temporales nunca deben publicarse ni presentarse como entrenamiento con observaciones reales.
- El modelo de reseñas reales guardado localmente es distinto del recomendador demo retirado; no afirmar que está integrado en Cloud sin verificarlo.
