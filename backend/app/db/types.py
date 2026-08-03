"""Tipos de columna reutilizables."""

from enum import Enum as PyEnum

from sqlalchemy import Enum as SAEnum


def enum_column(enum_cls: type[PyEnum], length: int = 32) -> SAEnum:
    """Columna de enumeración portable entre SQLite y PostgreSQL.

    Se persiste el *valor* del enum (en español) como VARCHAR en lugar de un
    tipo ENUM nativo, para que la misma definición funcione en ambos motores
    y los datos sean legibles al inspeccionar la base a mano.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        length=length,
        validate_strings=True,
    )
