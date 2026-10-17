"""Carga HTTP local y restauración entre dos bases PostgreSQL de prueba.

load --url http://127.0.0.1:8000 --requests 50 --concurrency 5
restore --backup ../deploy/backups/test.dump [--execute]
Restaurar exige GAMETRACK_TEST_SOURCE_URL y GAMETRACK_TEST_RESTORE_URL distintos,
ambos con nombre gametrack_test*. Nunca usa DATABASE_URL ni crea/borra bases.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlparse


def test_database_url(value):
    from sqlalchemy.engine import make_url
    url=make_url(value)
    if url.get_backend_name() != "postgresql" or not (url.database or "").startswith("gametrack_test"):
        raise ValueError("Se requiere una base dedicada cuyo nombre empiece por gametrack_test")
    return url


def pg_environment(url):
    env=os.environ.copy()
    for key in ("PGHOST","PGPORT","PGDATABASE","PGUSER","PGPASSWORD","PGSERVICE","PGOPTIONS"):
        env.pop(key,None)
    for key,value in (("PGHOST",url.host),("PGPORT",url.port),("PGDATABASE",url.database),("PGUSER",url.username),("PGPASSWORD",url.password)):
        if value is not None: env[key]=str(value)
    if url.query.get("sslmode"): env["PGSSLMODE"]=url.query["sslmode"]
    return env


def table_counts(engine):
    from sqlalchemy import MetaData, func, select
    metadata=MetaData();metadata.reflect(bind=engine,schema="public")
    with engine.connect() as connection:
        return {table.name:connection.scalar(select(func.count()).select_from(table)) for table in metadata.sorted_tables}


def restore(backup, execute=False):
    from sqlalchemy import create_engine, inspect
    source=test_database_url(os.environ["GAMETRACK_TEST_SOURCE_URL"])
    target=test_database_url(os.environ["GAMETRACK_TEST_RESTORE_URL"])
    if (source.host,source.port,source.database)==(target.host,target.port,target.database):
        raise ValueError("Origen y destino deben ser bases distintas")
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        raise ValueError("Faltan pg_dump y pg_restore de una versión compatible")
    if backup.exists(): raise ValueError("No se sobrescribe un respaldo existente")
    engines=[create_engine(url,echo=False,hide_parameters=True) for url in (source,target)]
    try:
        if inspect(engines[1]).get_table_names(schema="public"):
            raise ValueError("El destino debe estar vacío; no se limpian ni reemplazan tablas")
        counts=table_counts(engines[0])
        if not execute: return dict(status="prepared",source_tables=len(counts),execute=False)
        backup.parent.mkdir(parents=True,exist_ok=True)
        start=time.perf_counter()
        for command,url in ((["pg_dump","--format=custom","--no-owner","--no-privileges","--schema=public","--file",str(backup)],source),
                            (["pg_restore","--exit-on-error","--single-transaction","--no-owner","--no-privileges","--dbname",target.database,str(backup)],target)):
            result=subprocess.run(command,env=pg_environment(url),capture_output=True)
            if result.returncode: raise ValueError("Falló una herramienta PostgreSQL; diagnóstico privado retenido fuera del informe")
        restored=table_counts(engines[1])
        if counts != restored: raise ValueError("Los conteos restaurados no coinciden")
        return dict(status="restored",tables=len(counts),rows=sum(counts.values()),counts_equal=True,
                    backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),seconds=round(time.perf_counter()-start,3))
    finally:
        for engine in engines: engine.dispose()


def load(url, requests, concurrency):
    parsed=urlparse(url)
    if parsed.scheme not in {"http","https"} or parsed.hostname not in {"localhost","127.0.0.1","::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/"}:
        raise ValueError("La carga sólo admite un origen local de pruebas sin credenciales")
    import httpx
    def request(index):
        start=time.perf_counter()
        try:
            response=httpx.get(url.rstrip('/')+("/api/v1/health" if index%2 else "/api/v1/games?limit=2"),timeout=15,follow_redirects=False)
            return time.perf_counter()-start,response.status_code
        except httpx.HTTPError:
            return time.perf_counter()-start,0
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        observations=list(pool.map(request,range(requests)))
    elapsed=time.perf_counter()-start
    times=sorted(seconds for seconds,_ in observations)
    return dict(environment="local_test_only",requests=requests,concurrency=concurrency,seconds=round(elapsed,3),
                errors=sum(status!=200 for _,status in observations),p50_ms=round(times[math.ceil(len(times)*.5)-1]*1000,2),
                p95_ms=round(times[math.ceil(len(times)*.95)-1]*1000,2),requests_per_second=round(requests/elapsed,2),
                limitations="Carga acotada de salud y catálogo; no mide capacidad de producción ni tareas humanas.")


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest="command",required=True)
    load_parser=commands.add_parser("load");load_parser.add_argument("--url",required=True)
    load_parser.add_argument("--requests",type=int,choices=range(1,201),default=50);load_parser.add_argument("--concurrency",type=int,choices=range(1,17),default=5)
    restore_parser=commands.add_parser("restore");restore_parser.add_argument("--backup",type=Path,required=True);restore_parser.add_argument("--execute",action="store_true")
    args=parser.parse_args()
    try:
        result=load(args.url,args.requests,args.concurrency) if args.command=="load" else restore(args.backup,args.execute)
    except Exception as exc:
        # No imprimir errores de conexión que puedan incluir secretos.
        print(json.dumps(dict(status="not_verified",error_type=type(exc).__name__)));return 2
    print(json.dumps(result));return 0


if __name__=="__main__": raise SystemExit(main())
