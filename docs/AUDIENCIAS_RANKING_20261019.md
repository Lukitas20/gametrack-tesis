# Audiencias: ranking integrado — 19 de octubre de 2026

La interfaz presenta hasta cinco categorías de Steam ordenadas por usuarios observados, con empates compartidos, porcentajes sobre los historiales públicos consultados y una lectura descriptiva basada en esos mismos datos. No mezcla identidades ni tamaños de las cohortes Steam y GameTrack. Mantiene el mínimo de cinco personas y no rellena categorías faltantes.

Se retiran de la vista los bloques Cobertura, Usuarios de GameTrack y Procedencia y alcance, junto con los contadores de avance y las fracciones de autores. La procedencia detallada sigue disponible en el informe del servidor y su exportación existente. Una nota breve aclara que los grupos pueden superponerse y no representan todo el mercado.

Durante la colección, los lotes se conservan en la caché del servidor pero no se publican tarjetas parciales. El ranking y su reflexión se muestran juntos después de una respuesta ready sin pendientes. Errores, consultas ocupadas y el límite de 64 lotes muestran una acción de reintento, sin presentar un resultado incompleto como definitivo. Al salir de la pantalla se descartan las respuestas en vuelo.

No cambian muestreo, API, persistencia, modelos ni datos. La reflexión es descriptiva, no una respuesta generativa ni una predicción de ventas. La revisión local usa exclusivamente el informe agregado real guardado en docs/evidence/audience-report-cloud-20261017.json. Los fixtures de las pruebas son aislados y están excluidos de la imagen publicada.

Validación: 40 pruebas de frontend, sintaxis de los dos módulos y revisión visual local del componente con el informe real. Se cubren ordenamiento, empates, privacidad, ausencia de mutación, carga entre lotes, errores/reintentos y desconexión. El recorrido autenticado en producción requiere una sesión real de desarrollador; no se crean perfiles de prueba ni se cambian permisos.


Actualización: las tarjetas muestran size / denominator × 100, con hasta un decimal, en lugar de conteos absolutos. Las barras usan esa misma proporción; no se normalizan por el líder ni por la suma del top cinco. La base proviene del grupo real del servidor. Una base ausente, no entera, cero o menor que el grupo produce un porcentaje no disponible; nunca se inventa el denominador. El texto visible aclara que los grupos se superponen y no suman necesariamente 100%. Pasaron 42 pruebas del frontend.
