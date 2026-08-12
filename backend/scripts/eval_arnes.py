#!/usr/bin/env python
"""Arnés de evaluación del asistente: pooling, anotación y tabla comparativa.

Es LA pieza que convierte "los resultados se ven bien" en números
defendibles: cada decisión de diseño del rediseño (etiquetas comunitarias,
ponderación por votos, géneros de respaldo, boost de ABSA, pesos de la
combinación) es una VARIANTE que corre el MISMO código de producción con
otra configuración, y termina siendo una fila de la tabla con
precision@3 / NDCG@3 / MAP.

Metodología (pooling estilo TREC):

1. ``--pool``: corre las 28 consultas representativas contra todas las
   variantes, junta la unión de los top-N de cada una, y emite un CSV de
   anotación POR ANOTADOR, con los juegos en orden aleatorio fijo y SIN
   decir qué variante propuso cada uno (anotación ciega: no se puede juzgar
   con cariño a la variante propia). Como el pool cubre la misma profundidad
   que las métricas, todo lo que cualquier variante rankea queda juzgado.
   Re-correrlo (para sumar una variante, por ejemplo) es seguro: los juicios
   ya cargados se conservan por (consulta, juego) y sólo quedan pendientes
   los juegos nuevos que hayan entrado al pool.
2. Cada integrante llena su CSV por separado (columna ``relevante``: 1/0).
   Criterio impreso en las instrucciones que genera ``--pool``.
3. ``--metrics A.csv B.csv``: computa el kappa de Cohen entre los dos,
   exporta los desacuerdos para adjudicar hablando, y (con
   ``--adjudicadas R.csv`` o si no hay desacuerdos) imprime la tabla
   variante x métrica en Markdown, lista para pegar en la tesis.

Uso:
    python scripts/eval_arnes.py --pool                       # genera anotacion_{a,b}.csv
    python scripts/eval_arnes.py --pool --sweep               # + barrido de pesos
    python scripts/eval_arnes.py --metrics eval/anotacion_a.csv eval/anotacion_b.csv
    python scripts/eval_arnes.py --metrics ... --adjudicadas eval/adjudicadas.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402

from app.api.v1.endpoints.quiz import QuizRequest, run_quiz  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.ml.evaluacion import (  # noqa: E402
    average_precision,
    cohen_kappa,
    ndcg_at_k,
    precision_at_k,
)
from app.ml.recommender import EngineConfig, RecommenderEngine  # noqa: E402
from app.models import Game, Tag, game_tags  # noqa: E402

EVAL_DIR = BACKEND_ROOT / "eval"
POOL_DEPTH = 10   # profundidad del pool y de AP; las métricas @k usan K=3
K = 3
RANDOM_SEED = 20260810  # orden de anotación reproducible entre los dos CSV

# ---------------------------------------------------------------------------
# Consultas representativas
# ---------------------------------------------------------------------------
#
# 28 consultas que cubren el espacio de respuestas de forma estratificada:
# cada ánimo aparece con cada compañía, las franjas de tiempo aparecen donde
# más discriminan (tarde/finde), y el aspecto prioritario aparece en las
# combinaciones donde hay evidencia ABSA que mover. No son las 144
# combinaciones: son las que un usuario real produce y donde las variantes
# pueden diferir; agregar consultas después es agregar filas acá (los IDs
# son estables a propósito).


@dataclass(frozen=True)
class Consulta:
    id: str
    descripcion: str
    mood: str
    company: str | None = None
    time: int | None = None
    aspect: str | None = None

    def payload(self) -> QuizRequest:
        return QuizRequest(
            mood=self.mood,
            company=self.company,
            max_playtime=self.time,
            priority_aspect=self.aspect,
        )


CONSULTAS = [
    # Ánimo x compañía, sin límite de tiempo
    Consulta("C01", "Competir, en línea", "competir", "en-linea"),
    Consulta("C02", "Competir, solo", "competir", "solo"),
    Consulta("C03", "Competir, con amigos", "competir", "amigos"),
    Consulta("C04", "Historia, solo", "historia", "solo"),
    Consulta("C05", "Historia, con amigos", "historia", "amigos"),
    Consulta("C06", "Historia, en línea", "historia", "en-linea"),
    Consulta("C07", "Relajarme, solo", "relajarme", "solo"),
    Consulta("C08", "Relajarme, con amigos", "relajarme", "amigos"),
    Consulta("C09", "Relajarme, en línea", "relajarme", "en-linea"),
    Consulta("C10", "Desafío, solo", "desafio", "solo"),
    Consulta("C11", "Desafío, con amigos", "desafio", "amigos"),
    Consulta("C12", "Desafío, en línea", "desafio", "en-linea"),
    # Sin compañía elegida (el filtro de compañía no aplica)
    Consulta("C13", "Competir, sin preferencia de compañía", "competir"),
    Consulta("C14", "Relajarme, sin preferencia de compañía", "relajarme"),
    # Franja "una tarde" (<=15 h): donde la exención de juegos-servicio actúa
    Consulta("C15", "Competir, en línea, una tarde", "competir", "en-linea", 15),
    Consulta("C16", "Historia, solo, una tarde", "historia", "solo", 15),
    Consulta("C17", "Relajarme, solo, una tarde", "relajarme", "solo", 15),
    Consulta("C18", "Desafío, solo, una tarde", "desafio", "solo", 15),
    Consulta("C19", "Relajarme, con amigos, una tarde", "relajarme", "amigos", 15),
    # Franja "un finde" (<=40 h)
    Consulta("C20", "Historia, solo, un finde", "historia", "solo", 40),
    Consulta("C21", "Desafío, solo, un finde", "desafio", "solo", 40),
    Consulta("C22", "Competir, con amigos, un finde", "competir", "amigos", 40),
    # Aspecto prioritario (ABSA)
    Consulta("C23", "Historia, solo, priorizando la historia", "historia", "solo", None, "historia"),
    Consulta("C24", "Historia, solo, priorizando que ande bien", "historia", "solo", None, "optimizacion"),
    Consulta("C25", "Desafío, solo, priorizando la jugabilidad", "desafio", "solo", None, "jugabilidad"),
    Consulta("C26", "Relajarme, solo, priorizando lo visual", "relajarme", "solo", None, "graficos"),
    Consulta("C27", "Competir, en línea, priorizando que ande bien", "competir", "en-linea", None, "optimizacion"),
    Consulta("C28", "Historia, solo, una tarde, priorizando la historia", "historia", "solo", 15, "historia"),
]

# ---------------------------------------------------------------------------
# Variantes: cada decisión de diseño del hilo, como fila de la tabla
# ---------------------------------------------------------------------------


# Mapeo del sistema ANTERIOR al rediseño (el que estaba en quiz.js antes del
# contrato por claves): géneros con peso uniforme como perfil y sólo
# categorías de plataforma como filtros. Se expresa con el contrato legado
# del endpoint — que existe justamente para esto — así la variante corre por
# el mismo camino de código que corría el sistema viejo.
_LEGACY_MOOD = {
    "historia": (["rol", "aventura"], []),
    "desafio": (["accion", "estrategia"], []),
    "relajarme": (["casual", "simuladores"], []),
    "competir": (["accion", "deportes", "carreras"], ["jcj", "jcj-en-linea"]),
}
_LEGACY_COMPANY = {
    None: [],
    "solo": ["un-jugador"],
    "amigos": [
        "cooperativo", "cooperativo-en-linea",
        "pantalla-partida-compartida", "coop-a-pantalla-com-partida",
    ],
    "en-linea": [
        "jcj-en-linea", "cooperativo-en-linea",
        "multijugador", "multijugador-multiplataforma",
    ],
}


def _legacy_payload(consulta: Consulta) -> QuizRequest:
    genres, mood_tags = _LEGACY_MOOD[consulta.mood]
    return QuizRequest(
        genres=genres,
        mood_tags=mood_tags,
        company_tags=_LEGACY_COMPANY[consulta.company],
        max_playtime=consulta.time,
        priority_aspect=None,  # el sistema anterior no tenía boost: reordenaba
    )


@dataclass(frozen=True)
class Variante:
    id: str
    descripcion: str
    engine: EngineConfig = field(default_factory=EngineConfig)
    # Cómo se traduce la consulta a payload: "nuevo" usa el vocabulario
    # ponderado del servidor; "legado" reproduce el sistema anterior.
    contrato: str = "nuevo"
    con_aspecto: bool = True  # False = ignora priority_aspect (sin boost ABSA)

    def payload(self, consulta: Consulta) -> QuizRequest:
        if self.contrato == "legado":
            return _legacy_payload(consulta)
        request = consulta.payload()
        if not self.con_aspecto:
            request = request.model_copy(update={"priority_aspect": None})
        return request


VARIANTES = [
    Variante(
        "V0-popularidad",
        "Piso: sin contenido, sólo calidad y alcance (filtros de producción)",
        EngineConfig(w_content=0.0, w_quality=0.4, w_reach=0.6),
    ),
    Variante(
        "V1-sistema-anterior",
        "Antes del hilo: géneros uniformes + filtros sólo de plataforma, sin ABSA",
        EngineConfig(use_community_tags=False),
        contrato="legado",
    ),
    Variante(
        "V2-comunitarias-sin-votos",
        "Etiquetas comunitarias binarias (membresía sin ponderar por votos)",
        EngineConfig(use_vote_weights=False),
    ),
    Variante(
        "V3-produccion",
        "Producción: comunitarias ponderadas por votos + géneros + boost ABSA",
    ),
    Variante(
        "V4-produccion-sin-absa",
        "Producción con el aspecto prioritario ignorado (aísla el boost ABSA)",
        con_aspecto=False,
    ),
    Variante(
        "V5-sin-generos",
        "Sólo comunitarias, sin el respaldo de géneros en el corpus",
        EngineConfig(genre_tf=0.0),
    ),
]


def _sweep_variantes() -> list[Variante]:
    """Barrido del peso del contenido, manteniendo calidad:alcance en 15:25."""
    out = []
    for w_content in (0.4, 0.5, 0.7, 0.8):
        rest = 1.0 - w_content
        out.append(
            Variante(
                f"S-contenido-{int(w_content * 100)}",
                f"Barrido: contenido {w_content:.1f}, calidad {rest * 0.375:.3f}, alcance {rest * 0.625:.3f}",
                EngineConfig(
                    w_content=w_content,
                    w_quality=rest * 0.375,
                    w_reach=rest * 0.625,
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Ejecución de variantes
# ---------------------------------------------------------------------------


def run_variant(db, variante: Variante) -> dict[str, list[str]]:
    """consulta_id -> ranking de slugs (profundidad POOL_DEPTH).

    Un solo motor por variante (construirlo recorre toda la base); el mismo
    camino de código que el endpoint, garantizado por el test de paridad.
    """
    engine = RecommenderEngine(db, config=variante.engine)
    rankings: dict[str, list[str]] = {}
    for consulta in CONSULTAS:
        outcome = run_quiz(db, variante.payload(consulta), engine=engine)
        slugs = [
            outcome.games[c.game_id].slug
            for c in outcome.ranked[:POOL_DEPTH]
            if c.game_id in outcome.games
        ]
        rankings[consulta.id] = slugs
    return rankings


def _game_context(db) -> dict[str, tuple[str, str, str]]:
    """slug -> (nombre, géneros, top etiquetas por votos) para anotar sin
    tener que abrir Steam por cada juego."""
    context: dict[str, tuple[str, str, str]] = {}
    top_tags: dict[int, list[tuple[int, str]]] = {}
    for game_id, slug, votes in db.execute(
        select(game_tags.c.game_id, Tag.slug, game_tags.c.votes)
        .join(Tag, Tag.id == game_tags.c.tag_id)
        .where(Tag.kind == "community")
    ):
        top_tags.setdefault(game_id, []).append((votes or 0, slug))
    for game in db.scalars(select(Game)):
        tags = ", ".join(
            slug for _, slug in sorted(top_tags.get(game.id, []), reverse=True)[:6]
        )
        genres = ", ".join(genre.name for genre in game.genres)
        context[game.slug] = (game.name, genres, tags)
    return context


# ---------------------------------------------------------------------------
# --pool
# ---------------------------------------------------------------------------

INSTRUCCIONES = """\
# Instrucciones de anotación

Para cada fila: ¿le sugerirías ESTE juego a alguien que respondió ESO en el
asistente? Poné 1 (sí) o 0 (no) en la columna `relevante`. Nada más.

Criterio:
- Juzgá la consulta COMPLETA (ánimo + compañía + tiempo), no sólo el ánimo.
  "Relajarme, con amigos" y el juego no tiene cooperativo => 0.
- "Una tarde" pregunta por la sesión: un juego-servicio de partidas cortas
  es un 1 aunque la gente le meta cientos de horas.
- Si no conocés el juego, mirá su ficha en Steam antes de juzgar (las
  columnas de géneros y etiquetas son un resumen para acelerar, no
  reemplazan conocer el juego).
- No hay "0.5": si dudás en serio, 0 (la sugerencia tibia no le sirve a
  nadie). Anotá el caso en `notas` para discutirlo en la adjudicación.
- Cada uno llena SU archivo sin mirar el del otro. Los desacuerdos se
  adjudican hablando, después de medir el kappa — no antes.

El orden de los juegos es aleatorio y ninguna fila dice qué variante lo
propuso: no se puede (ni se debe poder) anotar a favor de una variante.
"""


ANNOTATION_HEADER = [
    "consulta_id", "consulta", "juego_slug", "juego",
    "generos", "etiquetas_top", "relevante", "notas",
]


def _previous_judgments(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """Lo ya anotado en una corrida anterior, indexado por (consulta, juego).

    Existe porque re-correr ``--pool`` (para sumar una variante, por ejemplo)
    reescribe los CSV, y sin esto se llevaría puesto el trabajo de anotación
    — mil juicios por cabeza — sin avisar. El pool puede cambiar; el juicio
    "¿este juego le sirve a alguien que pidió esto?" no depende de qué
    variante lo propuso, así que sobrevive intacto y sólo hay que juzgar los
    juegos nuevos que entren.
    """
    if not path.exists():
        return {}
    previous: dict[tuple[str, str], tuple[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("consulta_id") or "", row.get("juego_slug") or "")
            previous[key] = (row.get("relevante") or "", row.get("notas") or "")
    return previous


def _write_annotation_csv(path: Path, pool_rows: list[list[str]]) -> tuple[int, int]:
    """Escribe el CSV de un anotador preservando lo que ya haya juzgado.

    Devuelve (juicios conservados, filas nuevas por juzgar).
    """
    previous = _previous_judgments(path)
    rows, kept = [], 0
    for consulta_id, descripcion, slug, name, genres, tags in pool_rows:
        relevante, notas = previous.get((consulta_id, slug), ("", ""))
        if relevante:
            kept += 1
        rows.append([consulta_id, descripcion, slug, name, genres, tags, relevante, notas])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(ANNOTATION_HEADER)
        writer.writerows(rows)
    return kept, len(rows) - kept


def cmd_pool(db, args) -> int:
    variantes = list(VARIANTES) + (_sweep_variantes() if args.sweep else [])
    print(f"Corriendo {len(variantes)} variantes x {len(CONSULTAS)} consultas...")

    pools: dict[str, set[str]] = {consulta.id: set() for consulta in CONSULTAS}
    all_rankings: dict[str, dict[str, list[str]]] = {}
    for variante in variantes:
        rankings = run_variant(db, variante)
        all_rankings[variante.id] = rankings
        for consulta_id, slugs in rankings.items():
            pools[consulta_id].update(slugs)
        print(f"  {variante.id}: ok")

    EVAL_DIR.mkdir(exist_ok=True)
    # Los rankings se guardan para --metrics: la anotación es sobre el pool
    # (sin variantes), las métricas necesitan saber qué rankeó cada una.
    with open(EVAL_DIR / "rankings.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["variante", "consulta_id", "posicion", "juego_slug"])
        for variante_id, rankings in all_rankings.items():
            for consulta_id, slugs in rankings.items():
                for position, slug in enumerate(slugs, start=1):
                    writer.writerow([variante_id, consulta_id, position, slug])

    context = _game_context(db)
    descripcion = {consulta.id: consulta.descripcion for consulta in CONSULTAS}
    rows = []
    for consulta_id in sorted(pools):
        slugs = sorted(pools[consulta_id])
        random.Random(f"{RANDOM_SEED}-{consulta_id}").shuffle(slugs)
        for slug in slugs:
            name, genres, tags = context.get(slug, (slug, "", ""))
            rows.append([consulta_id, descripcion[consulta_id], slug, name, genres, tags])

    (EVAL_DIR / "INSTRUCCIONES.md").write_text(INSTRUCCIONES, encoding="utf-8")

    total = len(rows)
    print()
    print(f"Pool generado: {total} juicios por anotador "
          f"({total / len(CONSULTAS):.0f} juegos promedio por consulta).")
    for annotator in ("a", "b"):
        path = EVAL_DIR / f"anotacion_{annotator}.csv"
        kept, pending = _write_annotation_csv(path, rows)
        detalle = f"{pending} por juzgar" + (f", {kept} conservados" if kept else "")
        print(f"  {path}  ({detalle})")
    print(f"  {EVAL_DIR / 'INSTRUCCIONES.md'}")
    print(f"  {EVAL_DIR / 'rankings.csv'}  (no tocar: lo lee --metrics)")
    return 0


# ---------------------------------------------------------------------------
# --metrics
# ---------------------------------------------------------------------------


def _read_annotations(path: str) -> dict[tuple[str, str], int]:
    judgments: dict[tuple[str, str], int] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("relevante") or "").strip()
            if raw in ("0", "1"):
                judgments[(row["consulta_id"], row["juego_slug"])] = int(raw)
    return judgments


def cmd_metrics(db, args) -> int:
    ann_a = _read_annotations(args.metrics[0])
    ann_b = _read_annotations(args.metrics[1])

    shared = sorted(set(ann_a) & set(ann_b))
    if not shared:
        print("No hay juicios en común entre los dos archivos: ¿están llenos?")
        return 1
    missing = (set(ann_a) | set(ann_b)) - set(shared)
    if missing:
        print(f"Aviso: {len(missing)} ítems juzgados por uno solo (se ignoran).")

    kappa = cohen_kappa([ann_a[key] for key in shared], [ann_b[key] for key in shared])
    agreement = sum(1 for key in shared if ann_a[key] == ann_b[key])
    print(f"Acuerdo inter-anotador: {agreement}/{len(shared)} "
          f"({100 * agreement / len(shared):.0f}%), kappa de Cohen = {kappa:.3f}")
    if kappa < 0.6:
        print("  kappa < 0.6: revisar el criterio ANTES de confiar en la tabla "
              "(¿'relevante' significa lo mismo para los dos?).")

    # Juicio final: acuerdo directo, o adjudicación explícita del resto.
    gold = {key: ann_a[key] for key in shared if ann_a[key] == ann_b[key]}
    disagreements = [key for key in shared if ann_a[key] != ann_b[key]]
    if args.adjudicadas:
        resolved = _read_annotations(args.adjudicadas)
        for key in disagreements:
            if key in resolved:
                gold[key] = resolved[key]
        unresolved = [key for key in disagreements if key not in resolved]
    else:
        unresolved = disagreements

    if unresolved:
        path = EVAL_DIR / "desacuerdos.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["consulta_id", "juego_slug", "anotador_a", "anotador_b", "relevante"])
            for consulta_id, slug in unresolved:
                writer.writerow([consulta_id, slug, ann_a[(consulta_id, slug)],
                                 ann_b[(consulta_id, slug)], ""])
        print(f"\n{len(unresolved)} desacuerdos sin adjudicar -> {path}")
        print("Complétenlo juntos (columna `relevante`) y vuelvan a correr con "
              "--adjudicadas eval/desacuerdos.csv")
        print("La tabla de abajo EXCLUYE esos ítems (cuentan como no juzgados).")

    # Rankings de la corrida de --pool
    rankings_path = EVAL_DIR / "rankings.csv"
    if not rankings_path.exists():
        print(f"\nFalta {rankings_path}: corré primero `--pool` (genera los "
              "rankings que estas anotaciones juzgan).")
        return 1
    rankings: dict[str, dict[str, list[str]]] = {}
    with open(rankings_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rankings.setdefault(row["variante"], {}).setdefault(
                row["consulta_id"], []
            ).append(row["juego_slug"])

    total_relevant = {
        consulta.id: sum(
            1 for (consulta_id, _slug), label in gold.items()
            if consulta_id == consulta.id and label == 1
        )
        for consulta in CONSULTAS
    }
    empty_queries = [cid for cid, count in total_relevant.items() if count == 0]
    if empty_queries:
        print(f"\nAviso: consultas sin NINGÚN relevante en el pool: {empty_queries}.")
        print("Se excluyen de los promedios (no hay ranking bueno posible ahí).")

    print("\n## Tabla comparativa (promedios sobre "
          f"{len(CONSULTAS) - len(empty_queries)} consultas)\n")
    print("| Variante | P@3 | NDCG@3 | MAP | sin juzgar |")
    print("|---|---|---|---|---|")
    per_query_rows = []
    for variante_id in rankings:
        p_values, ndcg_values, ap_values = [], [], []
        unjudged = 0
        for consulta in CONSULTAS:
            if consulta.id in empty_queries:
                continue
            ranking = rankings[variante_id].get(consulta.id, [])
            relevances = []
            for slug in ranking:
                label = gold.get((consulta.id, slug))
                if label is None:
                    unjudged += 1
                    label = 0  # supuesto de pooling, reportado aparte
                relevances.append(label)
            p_values.append(precision_at_k(relevances, K))
            ndcg_values.append(ndcg_at_k(relevances, total_relevant[consulta.id], K))
            ap_values.append(average_precision(relevances, total_relevant[consulta.id]))
            per_query_rows.append(
                [variante_id, consulta.id,
                 f"{p_values[-1]:.3f}", f"{ndcg_values[-1]:.3f}", f"{ap_values[-1]:.3f}"]
            )
        mean = lambda values: sum(values) / len(values) if values else 0.0  # noqa: E731
        print(f"| {variante_id} | {mean(p_values):.3f} | {mean(ndcg_values):.3f} "
              f"| {mean(ap_values):.3f} | {unjudged} |")

    with open(EVAL_DIR / "metricas_por_consulta.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["variante", "consulta_id", "p_at_3", "ndcg_at_3", "ap"])
        writer.writerows(per_query_rows)
    print(f"\nDetalle por consulta -> {EVAL_DIR / 'metricas_por_consulta.csv'}")
    print("(la columna `sin juzgar` debería ser 0: si no, la anotación está "
          "incompleta o --pool se corrió de nuevo con otras variantes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pool", action="store_true", help="generar pools y CSV de anotación")
    parser.add_argument("--sweep", action="store_true", help="incluir el barrido de pesos")
    parser.add_argument("--metrics", nargs=2, metavar=("A.csv", "B.csv"),
                        help="computar kappa y la tabla comparativa")
    parser.add_argument("--adjudicadas", default=None,
                        help="CSV de desacuerdos resueltos entre los dos")
    args = parser.parse_args()

    if not (args.pool or args.metrics):
        parser.error("indicá --pool o --metrics A.csv B.csv")

    db = SessionLocal()
    try:
        if args.pool:
            return cmd_pool(db, args)
        return cmd_metrics(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
