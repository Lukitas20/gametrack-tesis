"""Configuración compartida de la suite de tests.

Se inyecta antes que cualquier otro import: en Windows con inspección TLS
corporativa el bundle de certifi no conoce la CA local, y las llamadas a
Steam y RAWG durante los tests de integración fallarían con
``CERTIFICATE_VERIFY_FAILED`` (mismo problema que en ``app/main.py``).
"""

import truststore

truststore.inject_into_ssl()
