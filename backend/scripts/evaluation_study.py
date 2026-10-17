"""Paquetes locales para revisión humana. No entrena ni escribe en la base.

capture --input respuesta.json --output CARPETA_NUEVA
reviews --sqlite COPIA_LOCAL.db --output CARPETA_NUEVA --limit 50
metrics --packet CARPETA --annotations juicios.csv --audit separacion.json
Los paquetes contienen textos personales: no versionar, publicar ni subir a Cloud.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_packet(output, source, cases, *, kind, models=None):
    if len({case["id"] for case in cases})!=len(cases):
        raise ValueError("Casos duplicados; no se infla el tamaño de la muestra")
    output.mkdir(parents=True, exist_ok=False)
    (output / "response.json").write_bytes(source)
    (output / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = dict(schema="gametrack-human-study-v1", kind=kind, source_sha256=sha(source),
                    cases_sha256=sha((output / "cases.json").read_bytes()), models=models or {},
                    split_status="not_audited", annotation_status="unjudged", cases=len(cases))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with (output / "annotations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["item_id", "dimension", "label", "annotator", "elapsed_seconds", "error", "notes"])
        for case in cases:
            for dimension in case["dimensions"]:
                writer.writerow([case["id"], dimension, "", "", "", "", ""])
    # La hoja ciega no incluye las predicciones ni los votos guardados.
    (output / "blind.json").write_text(json.dumps([{k:v for k,v in case.items() if k != "prediction"}
                                                   for case in cases], ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def capture(path, output):
    source = path.read_bytes()
    envelope = json.loads(source)
    if envelope.get("schema") != "gametrack-evaluation-capture-v1":
        raise ValueError("Se requiere una exportación de la aplicación")
    response, kind = envelope["response"], envelope["kind"]
    if kind == "recommendations":
        cases = [dict(id=str(item["game"]["id"]), game=item["game"], context=envelope.get("context", {}),
                      dimensions=["relevance"], prediction=item) for item in response["items"]]
    elif kind == "reviews":
        cases = [dict(id="report", context=envelope.get("context", {}), dimensions=["report_supported"],
                      prediction=response, instruction="Contrastar este informe con las reseñas originales. No evalúa cada clasificador por separado.")]
    else:
        raise ValueError("Tipo de respuesta no admitido")
    if not cases:
        raise ValueError("La respuesta no contiene casos para juzgar")
    return write_packet(output, source, cases, kind=kind)


def export_reviews(sqlite, output, limit):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from scripts.copy_sqlite_to_postgres import readonly_sqlite_engine
    from app.db.real_data import real_review_filter
    from app.ml.analytics import analyze_text
    from app.ml.trained_reviews import load_model, artifact_path, TOPICS
    from app.models import Review
    model, status = load_model()
    engine = readonly_sqlite_engine(sqlite)
    try:
        with Session(engine, autoflush=False) as db:
            rows = db.execute(select(Review.id, Review.game_id, Review.content, Review.source, Review.is_recommended)
                              .where(real_review_filter(), Review.language == "es", Review.content != "")
                              .order_by(Review.id).limit(limit)).all()
            cases=[]
            for row in rows:
                prediction = model.predict([row.content])[0] if model else None
                cases.append(dict(id=str(row.id), game_id=row.game_id, text=row.content, source=row.source,
                                  dimensions=[*TOPICS, "aspect_jugabilidad", "aspect_graficos", "aspect_historia", "aspect_optimizacion"],
                                  prediction=dict(trained=prediction, rules=asdict(analyze_text(row.content)), author_vote=row.is_recommended)))
            db.rollback()
    finally:
        engine.dispose()
    if not cases:
        raise ValueError("No hay reseñas reales guardadas para exportar")
    source=json.dumps(cases, ensure_ascii=False, default=str).encode("utf-8")
    # Las enumeraciones del analizador heredan str y mantienen sus valores.
    return write_packet(output, source, cases, kind="review_classification",
                        models=dict(review_sha256=sha(artifact_path().read_bytes()) if model else None,
                                    review_status=status["status"], eligible_topics={key:value["eligible"] for key,value in status.get("topics",{}).items()}, rules="four_aspects"))


def combine(packets, output):
    """Combinar antes de anotar; conserva cada respuesta y su corrida."""
    sources=[];cases=[];kind=None;models=None;seen=set()
    for index,packet in enumerate(packets):
        manifest=json.loads((packet/"manifest.json").read_text(encoding="utf-8"))
        raw=(packet/"response.json").read_bytes();case_bytes=(packet/"cases.json").read_bytes()
        if sha(raw) in seen:
            raise ValueError("No se combina dos veces una misma captura")
        seen.add(sha(raw))
        if sha(raw)!=manifest["source_sha256"] or sha(case_bytes)!=manifest["cases_sha256"]:
            raise ValueError("Paquete modificado")
        if kind is not None and (kind!=manifest["kind"] or models!=manifest["models"]):
            raise ValueError("Combinar sólo paquetes del mismo tipo y modelos")
        kind=manifest["kind"];models=manifest["models"]
        sources.append(dict(source_sha256=sha(raw),response=raw.decode("utf-8")))
        for case in json.loads(case_bytes):
            cases.append(dict(case,id=f"{index}:{case['id']}",run_id=str(index)))
    return write_packet(output,json.dumps(sources,ensure_ascii=False).encode(),cases,kind=kind,models=models)


def usability(tasks, output):
    source=tasks.read_bytes();design=json.loads(source)
    if not isinstance(design,list) or not design or len({task["id"] for task in design})!=len(design):
        raise ValueError("Se requiere una lista de tareas con identificadores únicos")
    cases=[dict(id=task["id"],instruction=task["instruction"],dimensions=["task_complete"]) for task in design]
    return write_packet(output,source,cases,kind="usability")


def metrics(packet, annotations, audit):
    """No imputa ausencias. Requiere auditoría externa y cobertura completa."""
    manifest=json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    if sha((packet / "response.json").read_bytes()) != manifest["source_sha256"] or sha((packet / "cases.json").read_bytes()) != manifest["cases_sha256"]:
        raise ValueError("El paquete fue modificado después de la captura")
    cases=json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    expected={(case["id"],dimension) for case in cases for dimension in case["dimensions"]}
    with annotations.open(newline="", encoding="utf-8-sig") as stream:
        rows=list(csv.DictReader(stream))
    seen=set(); judged={}; durations=[]; errors=0
    for row in rows:
        key=(row["item_id"],row["dimension"])
        if key not in expected or key in seen:
            raise ValueError("Caso desconocido o juicio duplicado; no se sobrescriben juicios")
        seen.add(key)
        raw=row["label"].strip()
        if raw:
            allowed={"ausente","positivo","neutro","negativo"} if key[1].startswith("aspect_") else {"0","1"}
            if raw not in allowed or not row["annotator"].strip():
                raise ValueError("Etiqueta inválida o falta anotador")
            judged[key]=raw
        if row.get("elapsed_seconds", "").strip():
            import math
            seconds=float(row["elapsed_seconds"])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Tiempo inválido")
            durations.append(seconds)
        errors+=bool(row.get("error", "").strip())
    result=dict(status="incomplete", expected=len(expected), judged=len(judged), missing=len(expected-set(judged)),
                timed_judgments=len(durations), seconds=sum(durations) if durations else None, recorded_errors=errors, metrics=None)
    if result["missing"] or len(cases)<20:
        result["reason"]="Se requieren todos los juicios y al menos veinte casos distintos; no es un cálculo de potencia estadística."
        return result
    if audit is None:
        result.update(status="split_not_verified", reason="Falta auditoría de separación respecto de entrenamiento y ajuste.")
        return result
    proof=json.loads(audit.read_text(encoding="utf-8"))
    if (proof.get("source_sha256") != manifest["source_sha256"] or proof.get("purpose") != "evaluation"
            or proof.get("disjoint_train_tune_evaluation") is not True or not proof.get("reviewer")
            or not proof.get("training_index_sha256") or not proof.get("tuning_index_sha256")):
        raise ValueError("Auditoría de separación insuficiente")
    # No entrenar, elegir variantes ni modificar las predicciones al leer juicios.
    if manifest["kind"] == "recommendations":
        from app.ml.evaluacion import precision_at_k
        runs={}
        for case in cases: runs.setdefault(case.get("run_id","0"),[]).append(int(judged[(case["id"],"relevance")]))
        result["metrics"]={"mean_precision_at_3":sum(precision_at_k(labels,3) for labels in runs.values())/len(runs),"runs":len(runs),"judged_cases":len(cases)}
    elif manifest["kind"] == "review_classification":
        dimensions={}
        for dimension in cases[0]["dimensions"]:
            if not dimension.startswith("aspect_") and not manifest["models"].get("eligible_topics",{}).get(dimension):
                dimensions[dimension]=dict(compared=0,accuracy=None,reason="Modelo no habilitado para este tema")
                continue
            pairs=[]
            for case in cases:
                if dimension.startswith("aspect_"):
                    aspect=dimension.removeprefix("aspect_")
                    predicted=next((item["sentiment"] for item in case["prediction"]["rules"]["aspects"] if item["aspect"]==aspect),"ausente")
                else:
                    trained=case["prediction"]["trained"]
                    if trained is None or trained.get("reception") is None:
                        continue
                    predicted=str(int(any(item["topic"]==dimension for item in trained["topics"])))
                pairs.append((judged[(case["id"],dimension)],predicted))
            classes=sorted({value for pair in pairs for value in pair})
            expected_classes={"ausente","positivo","neutro","negativo"} if dimension.startswith("aspect_") else {"0","1"}
            enough=all(sum(a==label for a,_ in pairs)>=5 for label in expected_classes)
            dimensions[dimension]=dict(compared=len(pairs), accuracy=sum(a==b for a,b in pairs)/len(pairs) if pairs and enough else None,
                                       sufficient_class_coverage=enough,
                                       confusion={a:{b:sum(x==a and y==b for x,y in pairs) for b in classes} for a in classes})
        result["metrics"]=dimensions
        votes=[(bool(case["prediction"]["author_vote"]),case["prediction"]["trained"]["reception"]>=.5)
               for case in cases if case["prediction"]["trained"] is not None
               and case["prediction"]["trained"].get("reception") is not None
               and case["prediction"]["author_vote"] is not None]
        result["author_vote_comparison"]=dict(compared=len(votes),accuracy=None)
        if len(votes)>=20 and min(sum(a for a,_ in votes),sum(not a for a,_ in votes))>=5:
            result["author_vote_comparison"]["accuracy"]=sum(a==b for a,b in votes)/len(votes)
    elif manifest["kind"] in {"usability","reviews"}:
        dimension="task_complete" if manifest["kind"]=="usability" else "report_supported"
        result["metrics"]={"observed_positive_rate":sum(int(judged[(case["id"],dimension)]) for case in cases)/len(cases),"unit":dimension}
    result.update(status="evaluated", audit="externally_attested", reason="Métricas descriptivas de esta muestra anotada; no prueban satisfacción ni representatividad.")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    capture_parser=commands.add_parser("capture");capture_parser.add_argument("--input",type=Path,required=True);capture_parser.add_argument("--output",type=Path,required=True)
    reviews=commands.add_parser("reviews");reviews.add_argument("--sqlite",type=Path,required=True);reviews.add_argument("--output",type=Path,required=True);reviews.add_argument("--limit",type=int,choices=range(1,201),default=50)
    evaluate=commands.add_parser("metrics");evaluate.add_argument("--packet",type=Path,required=True);evaluate.add_argument("--annotations",type=Path,required=True);evaluate.add_argument("--audit",type=Path)
    merge=commands.add_parser("combine");merge.add_argument("--packets",type=Path,nargs="+",required=True);merge.add_argument("--output",type=Path,required=True)
    tasks=commands.add_parser("usability");tasks.add_argument("--tasks",type=Path,required=True);tasks.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="capture": result=capture(args.input,args.output)
    elif args.command=="reviews": result=export_reviews(args.sqlite,args.output,args.limit)
    elif args.command=="combine": result=combine(args.packets,args.output)
    elif args.command=="usability": result=usability(args.tasks,args.output)
    else: result=metrics(args.packet,args.annotations,args.audit)
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
