/* Panel de analítica para el rol desarrollador. */

import { api } from "../api.js";
import { developerOnly, requiresLogin } from "../components.js";
import {
  barChart,
  chartCard,
  divergingBar,
  divergingStackedBar,
  sentimentLegend,
  tableView,
} from "../charts.js";
import { navigate } from "../router.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import {
  ASPECT_LABEL,
  SENTIMENT_LABEL,
  emptyState,
  h,
  icon,
  pct,
  signed,
  spinnerBlock,
  toast,
} from "../ui.js";

const ASPECT_ORDER = ["jugabilidad", "graficos", "historia", "optimizacion"];

function guard() {
  if (!isLoggedIn()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Analítica")),
      requiresLogin("El panel de analítica es del rol desarrollador."),
    );
  }
  if (!isDeveloper()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Analítica")),
      developerOnly(),
    );
  }
  return null;
}

/** Ordena los aspectos siempre igual, para que no bailen entre vistas. */
function sortAspects(aspects) {
  return [...aspects].sort(
    (a, b) => ASPECT_ORDER.indexOf(a.aspecto) - ASPECT_ORDER.indexOf(b.aspecto),
  );
}

function netTile(label, value, note) {
  return h(
    "div",
    { class: "stat" },
    h("span", { class: "stat-label" }, label),
    h(
      "span",
      {
        class: "stat-value",
        style: { color: value >= 0 ? "var(--positivo)" : "var(--negativo)" },
      },
      signed(value, 2),
    ),
    note && h("span", { class: "stat-note" }, note),
  );
}

/* ------------------------------------------------------------------ *
 * Panel de aspectos (ABSA)
 * ------------------------------------------------------------------ */

function aspectCharts(aspects, { comparison = null } = {}) {
  const ordered = sortAspects(aspects);
  if (!ordered.length) {
    return emptyState(
      "Sin aspectos analizados",
      "Ejecutá el procesamiento de reseñas para poblar el desglose ABSA.",
    );
  }

  const netRows = ordered.map((aspect) => ({
    label: ASPECT_LABEL[aspect.aspecto] || aspect.aspecto,
    value: aspect.sentimiento_neto,
    tip: [
      `Neto: ${signed(aspect.sentimiento_neto, 2)}`,
      `Menciones: ${aspect.menciones}`,
      `Positivas ${aspect.distribucion.positivo} · neutras ${aspect.distribucion.neutro} · negativas ${aspect.distribucion.negativo}`,
      comparison ? `Catálogo: ${signed(comparison[aspect.aspecto] ?? 0, 2)}` : null,
    ].filter(Boolean),
  }));

  const volumeRows = ordered.map((aspect) => ({
    label: ASPECT_LABEL[aspect.aspecto] || aspect.aspecto,
    value: aspect.menciones,
    tip: [`${aspect.menciones} menciones detectadas por el ABSA`],
  }));

  return h(
    "div",
    { class: "grid grid-2" },
    chartCard({
      title: "Sentimiento neto por aspecto",
      subtitle:
        "Proporción de menciones positivas menos negativas, de −1 a +1. Barra a la derecha del cero es recepción favorable.",
      chart: divergingBar(netRows, { labelWidth: 120, domain: 1 }),
      legendEntries: [
        { label: "Recepción favorable", color: "var(--positivo)" },
        { label: "Recepción desfavorable", color: "var(--negativo)" },
      ],
      table: tableView(
        ["Aspecto", "Neto", "Menciones", "Positivas", "Neutras", "Negativas", ...(comparison ? ["Catálogo"] : [])],
        ordered.map((aspect) => [
          ASPECT_LABEL[aspect.aspecto] || aspect.aspecto,
          signed(aspect.sentimiento_neto, 2),
          aspect.menciones,
          aspect.distribucion.positivo,
          aspect.distribucion.neutro,
          aspect.distribucion.negativo,
          ...(comparison ? [signed(comparison[aspect.aspecto] ?? 0, 2)] : []),
        ]),
      ),
    }),
    chartCard({
      title: "Volumen de menciones",
      subtitle: "Cuántas veces se opinó sobre cada aspecto. Un neto sobre pocas menciones pesa menos.",
      chart: barChart(volumeRows, { labelWidth: 120, formatValue: (value) => `${value}` }),
      table: tableView(
        ["Aspecto", "Menciones"],
        ordered.map((aspect) => [ASPECT_LABEL[aspect.aspecto] || aspect.aspecto, aspect.menciones]),
      ),
    }),
  );
}

function evidenceSection(quotes) {
  const entries = Object.entries(quotes || {}).filter(([, list]) => list.length);
  if (!entries.length) return null;

  return h(
    "section",
    { class: "section" },
    h(
      "div",
      { class: "section-head" },
      h("h2", null, "Evidencia textual"),
      h(
        "span",
        { class: "muted", style: { fontSize: "var(--fs-sm)" } },
        "Fragmentos que el ABSA usó para clasificar cada aspecto como negativo",
      ),
    ),
    h(
      "div",
      { class: "grid grid-2" },
      entries.map(([aspect, list]) =>
        h(
          "section",
          { class: "card" },
          h(
            "div",
            { class: "row", style: { gap: "var(--s-2)", marginBottom: "var(--s-2)" } },
            icon("quote", 15, "muted"),
            h("h3", { class: "card-title" }, ASPECT_LABEL[aspect] || aspect),
            h("span", { class: "chip chip-sent", dataset: { s: "negativo" } }, h("span", { class: "dot" }), "Negativo"),
          ),
          list.map((quote) => h("blockquote", { class: "quote" }, `“${quote}”`)),
        ),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ *
 * Dashboard del estudio
 * ------------------------------------------------------------------ */

export async function developerView() {
  const blocked = guard();
  if (blocked) return blocked;

  const container = h("div", null, spinnerBlock("Cargando la analítica del estudio…"));

  let studio;
  let overview = null;
  try {
    studio = await api.studioAnalytics();
    overview = await api.overview().catch(() => null);
  } catch (error) {
    return h("div", null, emptyState("No se pudo cargar el panel", error.message));
  }

  const distribution = studio.resenas.distribucion;
  const analyzed = studio.resenas.analizadas;
  const aspects = sortAspects(studio.aspectos);
  const comparison = overview
    ? Object.fromEntries(overview.aspectos.map((item) => [item.aspecto, item.sentimiento_neto]))
    : null;

  const weakest = aspects.length
    ? aspects.reduce((worst, item) => (item.sentimiento_neto < worst.sentimiento_neto ? item : worst))
    : null;
  const strongest = aspects.length
    ? aspects.reduce((best, item) => (item.sentimiento_neto > best.sentimiento_neto ? item : best))
    : null;

  const processButton = h(
    "button",
    {
      class: "btn",
      onClick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const result = await api.processReviews(false);
          toast(result.mensaje);
          if (result.procesadas > 0) navigate("/dev");
        } catch (error) {
          toast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      },
    },
    icon("refresh", 15),
    "Procesar reseñas pendientes",
  );

  container.replaceChildren(
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, "Panel de desarrollador"),
        h("h1", null, studio.estudio),
        h(
          "p",
          null,
          "Recepción de los títulos del estudio según el análisis de sentimiento y el ABSA sobre las reseñas de la comunidad.",
        ),
      ),
      processButton,
    ),

    // --- Fila de indicadores. Una sola figura protagonista por vista. ---
    h(
      "div",
      { class: "grid grid-4" },
      h(
        "div",
        { class: "stat" },
        h("span", { class: "stat-label" }, "Sentimiento neto del estudio"),
        h(
          "span",
          {
            class: "hero-figure",
            style: { color: studio.resenas.sentimiento_neto >= 0 ? "var(--positivo)" : "var(--negativo)" },
          },
          signed(studio.resenas.sentimiento_neto, 2),
        ),
        h(
          "span",
          { class: "stat-note" },
          comparison
            ? `Catálogo completo: ${signed(overview.sentimiento_neto, 2)}`
            : "Sobre −1 a +1",
        ),
      ),
      h(
        "div",
        { class: "stat" },
        h("span", { class: "stat-label" }, "Reseñas analizadas"),
        h("span", { class: "stat-value" }, analyzed),
        h(
          "span",
          { class: "stat-note" },
          `${pct(distribution.positivo, analyzed)} positivas · ${pct(distribution.negativo, analyzed)} negativas`,
        ),
      ),
      strongest
        ? netTile(
            "Aspecto mejor recibido",
            strongest.sentimiento_neto,
            `${ASPECT_LABEL[strongest.aspecto]} · ${strongest.menciones} menciones`,
          )
        : null,
      weakest
        ? netTile(
            "Aspecto a atender",
            weakest.sentimiento_neto,
            `${ASPECT_LABEL[weakest.aspecto]} · ${weakest.menciones} menciones`,
          )
        : null,
    ),

    // --- Recepción por juego ---
    h(
      "section",
      { class: "section" },
      chartCard({
        title: "Recepción por título",
        subtitle:
          "Reparto de reseñas centrado en las neutras: la longitud hacia la izquierda son negativas y hacia la derecha positivas. El número de la derecha es el neto.",
        chart: studio.juegos.length
          ? divergingStackedBar(
              studio.juegos.map((game) => ({
                label: game.nombre,
                gameId: game.id,
                positivo: game.distribucion.positivo,
                neutro: game.distribucion.neutro,
                negativo: game.distribucion.negativo,
              })),
              {
                labelWidth: 170,
                onRowClick: (row) => navigate(`/dev/juego/${row.gameId}`),
              },
            )
          : emptyState("Sin datos", "El estudio no tiene juegos con reseñas analizadas."),
        legendEntries: sentimentLegend(distribution),
        table: tableView(
          ["Juego", "Nota", "Analizadas", "Positivas", "Neutras", "Negativas", "Neto"],
          studio.juegos.map((game) => [
            game.nombre,
            game.rating_promedio.toFixed(2),
            game.resenas_analizadas,
            game.distribucion.positivo,
            game.distribucion.neutro,
            game.distribucion.negativo,
            signed(game.sentimiento_neto, 2),
          ]),
        ),
        action: h(
          "span",
          { class: "muted", style: { fontSize: "var(--fs-xs)" } },
          "Clic en una fila para el detalle",
        ),
      }),
    ),

    // --- Panel ABSA ---
    h(
      "section",
      { class: "section" },
      h(
        "div",
        { class: "section-head" },
        h("h2", null, "Análisis por aspectos (ABSA)"),
        comparison
          ? h(
              "span",
              { class: "muted", style: { fontSize: "var(--fs-sm)" } },
              "Los tooltips comparan cada aspecto contra el promedio del catálogo",
            )
          : null,
      ),
      aspectCharts(aspects, { comparison }),
    ),

    evidenceSection(studio.citas_negativas),
  );

  return container;
}

/* ------------------------------------------------------------------ *
 * Analítica de un juego
 * ------------------------------------------------------------------ */

export async function developerGameView({ params }) {
  const blocked = guard();
  if (blocked) return blocked;

  let data;
  try {
    data = await api.gameAnalytics(Number(params.id));
  } catch (error) {
    return h("div", null, emptyState("No se pudo cargar la analítica", error.message));
  }

  const { juego, resenas, aspectos } = data;
  const analyzed = resenas.analizadas;
  const aspects = sortAspects(aspectos);

  return h(
    "div",
    null,
    h(
      "div",
      { class: "row", style: { marginBottom: "var(--s-4)" } },
      h("a", { class: "btn btn-sm btn-ghost", href: "#/dev" }, icon("arrowLeft", 13), "Volver al panel"),
    ),

    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, juego.desarrollador || "Analítica"),
        h("h1", null, juego.nombre),
        h(
          "p",
          null,
          `${juego.cantidad_ratings} valoraciones · nota ${juego.rating_promedio.toFixed(2)} · ${analyzed} reseñas analizadas`,
          resenas.pendientes ? ` · ${resenas.pendientes} pendientes de análisis` : "",
        ),
      ),
      h("a", { class: "btn", href: `#/juego/${juego.id}` }, "Ver ficha pública"),
    ),

    h(
      "div",
      { class: "grid grid-4" },
      h(
        "div",
        { class: "stat" },
        h("span", { class: "stat-label" }, "Sentimiento neto"),
        h(
          "span",
          {
            class: "hero-figure",
            style: { color: resenas.sentimiento_neto >= 0 ? "var(--positivo)" : "var(--negativo)" },
          },
          signed(resenas.sentimiento_neto, 2),
        ),
        h("span", { class: "stat-note" }, `sobre ${analyzed} reseñas`),
      ),
      h(
        "div",
        { class: "stat" },
        h("span", { class: "stat-label" }, "Reparto"),
        h(
          "span",
          { class: "stat-value" },
          pct(resenas.distribucion.positivo, analyzed),
        ),
        h("span", { class: "stat-note" }, "de reseñas positivas"),
      ),
      data.punto_fuerte
        ? h(
            "div",
            { class: "stat" },
            h("span", { class: "stat-label" }, "Punto fuerte"),
            h(
              "span",
              { class: "stat-value", style: { textTransform: "capitalize" } },
              ASPECT_LABEL[data.punto_fuerte] || data.punto_fuerte,
            ),
            h(
              "span",
              { class: "stat-note" },
              // El backend ordena el desglose por neto ascendente.
              `neto ${signed(aspectos[aspectos.length - 1]?.sentimiento_neto ?? 0, 2)}`,
            ),
          )
        : null,
      data.punto_debil
        ? h(
            "div",
            { class: "stat" },
            h("span", { class: "stat-label" }, "Punto débil"),
            h(
              "span",
              { class: "stat-value", style: { textTransform: "capitalize", color: "var(--negativo)" } },
              ASPECT_LABEL[data.punto_debil] || data.punto_debil,
            ),
            h("span", { class: "stat-note" }, `neto ${signed(aspectos[0]?.sentimiento_neto ?? 0, 2)}`),
          )
        : null,
    ),

    h(
      "section",
      { class: "section" },
      chartCard({
        title: "Polaridad global de las reseñas",
        subtitle:
          "Escala ordenada centrada en las neutras. Se usa barra apilada divergente en lugar de una torta porque permite comparar la longitud de cada lado.",
        chart: divergingStackedBar(
          [
            {
              label: juego.nombre,
              positivo: resenas.distribucion.positivo,
              neutro: resenas.distribucion.neutro,
              negativo: resenas.distribucion.negativo,
            },
          ],
          { labelWidth: 170 },
        ),
        legendEntries: sentimentLegend(resenas.distribucion),
        table: tableView(
          ["Clase", "Reseñas", "Porcentaje"],
          ["positivo", "neutro", "negativo"].map((key) => [
            SENTIMENT_LABEL[key],
            resenas.distribucion[key],
            pct(resenas.distribucion[key], analyzed),
          ]),
        ),
      }),
    ),

    h(
      "section",
      { class: "section" },
      h("div", { class: "section-head" }, h("h2", null, "Análisis por aspectos (ABSA)")),
      aspectCharts(aspects),
    ),

    evidenceSection(data.citas_negativas),
  );
}
