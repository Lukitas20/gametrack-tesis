"""Carga secretos sin mostrarlos ni guardarlos en Terraform o archivos locales.

Requiere gcloud autenticado y la infraestructura creada. Ejecutar una vez;
las ejecuciones siguientes conservan los valores y no rotan contraseñas.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen


def gcloud(project: str, *args: str, data: str | None = None):
    executable = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not executable:
        raise RuntimeError("Falta gcloud; usar Google Cloud Shell o instalar Google Cloud CLI.")
    result = subprocess.run(
        [executable, *args, f"--project={project}", "--quiet"],
        input=data, text=True, encoding="utf-8", capture_output=True,
    )
    if result.returncode:
        # gcloud puede incluir datos suministrados en sus diagnósticos.
        raise RuntimeError("Google rechazó la operación. Revisá proyecto, sesión y permisos.")
    return result.stdout.strip()


def has_version(project: str, name: str) -> bool:
    values = json.loads(gcloud(project, "secrets", "versions", "list", name,
                               "--filter=state=ENABLED", "--format=json"))
    return bool(values)


def add_version(project: str, name: str, value: str) -> None:
    gcloud(project, "secrets", "versions", "add", name, "--data-file=-", data=value)


def sql_request(project: str, token: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project}/{path}",
        data=data, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        # No imprimir el cuerpo, que puede repetir campos enviados.
        raise RuntimeError(f"Cloud SQL rechazó la operación (HTTP {error.code}).") from None


def wait_operation(project: str, token: str, name: str) -> None:
    for _ in range(60):
        operation = sql_request(project, token, f"operations/{quote(name, safe='')}")
        if operation.get("status") == "DONE":
            if operation.get("error"):
                raise RuntimeError("Falló la creación del usuario de la base; revisar Cloud SQL.")
            return
        time.sleep(2)
    raise RuntimeError("La creación del usuario sigue pendiente; revisar Cloud SQL antes de continuar.")


def read_steam_key(path: Path | None) -> str:
    key = os.environ.get("STEAM_API_KEY", "").strip()
    if not key and path:
        # Steam usa una clave hexadecimal; no evaluar variables ni ejecutar .env.
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.fullmatch(r"\s*(?:export\s+)?STEAM_API_KEY\s*=\s*(.*?)\s*", line)
            if match:
                key = match.group(1).strip("\"'").strip()
    if not key:
        key = getpass.getpass("Clave Steam (entrada oculta): ").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", key):
        raise ValueError("Se necesita una clave Steam Web API de 32 caracteres hexadecimales.")
    return key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--connection-name", required=True)
    parser.add_argument("--steam-env-file", type=Path)
    parser.add_argument("--skip-steam", action="store_true",
                        help="Configurar sólo credenciales nuevas de la base y JWT, sin leer ni copiar la clave Steam")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", args.project):
        parser.error("ID de proyecto inválido.")
    if not re.fullmatch(re.escape(args.project) + r":[a-z0-9-]+:gametrack-postgres", args.connection_name):
        parser.error("Usar el connection_name informado por Terraform para este proyecto.")
    try:
        # Obtener la clave antes de cualquier escritura para evitar una
        # configuración parcial por falta de la integración de Steam.
        steam_needed = not args.skip_steam and not has_version(args.project, "gametrack-steam-key")
        steam_key = read_steam_key(args.steam_env_file) if steam_needed else None
        token = gcloud(args.project, "auth", "print-access-token")
        users = sql_request(args.project, token, "instances/gametrack-postgres/users").get("items", [])
        user_exists = any(user.get("name") == "gametrack" for user in users)
        database_url = None
        if not has_version(args.project, "gametrack-database-url"):
            if user_exists:
                raise RuntimeError("El usuario gametrack ya existe pero falta el secreto de conexión. Recuperar ese secreto antes de continuar; no se reemplazó la contraseña.")
            password = secrets.token_hex(32)
            # Guardar primero permite reintentar la creación SQL sin perder ni
            # rotar la contraseña cuando hay una interrupción de red.
            database_url = "postgresql+psycopg2://gametrack:" + password + "@/gametrack?" + urlencode({"host": "/cloudsql/" + args.connection_name})
            add_version(args.project, "gametrack-database-url", database_url)
        if not user_exists:
            if database_url is None:
                database_url = gcloud(args.project, "secrets", "versions", "access", "latest",
                                      "--secret=gametrack-database-url")
            parsed = urlsplit(database_url)
            if (parsed.scheme != "postgresql+psycopg2" or parsed.username != "gametrack"
                    or parsed.path != "/gametrack" or not parsed.password
                    or parse_qs(parsed.query).get("host") != ["/cloudsql/" + args.connection_name]):
                raise RuntimeError("El secreto existente no corresponde a esta base; no se modificó.")
            operation = sql_request(args.project, token, "instances/gametrack-postgres/users",
                                    {"name": "gametrack", "password": unquote(parsed.password), "type": "BUILT_IN"})
            wait_operation(args.project, token, operation["name"])
        if not has_version(args.project, "gametrack-jwt-key"):
            add_version(args.project, "gametrack-jwt-key", secrets.token_hex(48))
        if steam_needed:
            add_version(args.project, "gametrack-steam-key", steam_key)
        print("Secretos configurados. No se mostraron credenciales ni se rotaron valores existentes.")
        return 0
    except Exception as error:
        # Sólo mensajes controlados; las excepciones de red pueden incluir URLs.
        message = str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__
        print(f"No se completó la configuración: {message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
