"""Configuración compartida de la suite de tests.

Se inyecta antes que cualquier otro import: el motor de recomendación baja un
modelo de embeddings de Hugging Face la primera vez que corre
(``app/ml/recommender.py``), y en Windows con inspección TLS corporativa el
bundle de certifi no conoce la CA local (mismo problema que las llamadas a
Steam y RAWG, ver ``app/main.py``).
"""

import truststore

truststore.inject_into_ssl()
