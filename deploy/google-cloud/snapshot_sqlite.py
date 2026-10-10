"""Copia consistente de SQLite; no modifica el origen ni pisa un backup."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def create_snapshot(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Crear con exclusión y permisos privados antes de abrir SQLite.
    with destination.open("xb"):
        pass
    destination.chmod(0o600)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin:
            with closing(sqlite3.connect(destination)) as backup:
                origin.backup(backup)
                # El snapshot debe ser un único archivo portable: el bucket
                # de origen se monta de sólo lectura y no admite crear WAL/SHM.
                backup.execute("PRAGMA journal_mode=DELETE")
                result = backup.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError("El backup no superó la comprobación de integridad.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    create_snapshot(args.source, args.output)
    print("Backup consistente creado; conservá el origen hasta verificar el despliegue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
