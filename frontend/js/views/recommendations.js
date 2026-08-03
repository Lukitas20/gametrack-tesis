/* Recomendaciones para el rol jugador.
 *
 * Además de la lista, permite forzar cada estrategia y compararlas lado a
 * lado sobre el mismo usuario: es la manera de mostrar que el enfoque híbrido
 * no es una caja negra sino la combinación de dos señales identificables.
 */

import { api } from "../api.js";
import { gameGrid, requiresLogin } from "../components.js";
import { navigate } from "../router.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import {
  SOURCE_LABEL,
  STRATEGY_LABEL,
  coverGradient,
  emptyState,
  h,
  icon,
  initials,
  spinnerBlock,
  toast,
} from "../ui.js";
import { coldStartNotice } from "./onboarding.js";

const STRATEGIES = ["auto", "hibrido", "contenido", "colaborativo", "popularidad"];
const COMPARABLE = ["hibrido", "contenido", "colaborativo", "popularidad"];

/** Explicación de cada estrategia, para que la demo se sostenga sola. */
const STRATEGY_NOTE = {
  auto: "Elige la estrategia según cuánto historial tenga el usuario.",
  hibrido:
    "Suma ponderada de contenido (40 %) y colaborativo (60 %), cada uno normalizado a [0,1].",
  contenido:
    "TF-IDF sobre géneros, etiquetas, desarrollador y descripción, con similitud coseno contra los juegos que valoraste alto.",
  colaborativo:
    "Filtrado ítem-ítem sobre la matriz usuario-ítem centrada por usuario. Necesita historial.",
  popularidad:
    "Media bayesiana: un 5,0 con dos votos no supera a un 4,6 con doscientos. Es el piso que responde siempre.",
};

export async function recommendationsView({ query } = { query: new URLSearchParams() }) {
  if (!isLoggedIn()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Recomendaciones")),
      requiresLogin("Las recomendaciones son personalizadas."),
    );
  }

  if (isDeveloper()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Recomendaciones")),
      emptyState(
        "Esta vista es para el rol jugador",
        "La cuenta actual es de desarrollador. Su lugar es el panel de analítica.",
        h("a", { class: "btn btn-primary", href: "#/dev" }, "Ir al panel"),
      ),
    );
  }

  // La estrategia y el modo comparación se leen de la URL, así que se puede
  // entrar directo a `#/recomendaciones?comparar=1` o `?estrategia=colaborativo`.
  const requested = query.get("estrategia");
  let strategy = STRATEGIES.includes(requested) ? requested : "auto";
  let comparing = query.get("comparar") === "1";

  const body = h("div");
  const meta = h("div", { class: "row", style: { gap: "var(--s-2)" } });
  const note = h("p", { class: "muted", style: { fontSize: "var(--fs-sm)", marginTop: "var(--s-3)" } });

  const segmented = h(
    "div",
    { class: "segmented", role: "group", "aria-label": "Estrategia" },
    STRATEGIES.map((key) =>
      h(
        "button",
        {
          "aria-pressed": String(strategy === key),
          onClick: () => {
            strategy = key;
            comparing = false;
            syncControls();
            load();
          },
        },
        STRATEGY_LABEL[key],
      ),
    ),
  );

  const compareButton = h(
    "button",
    {
      class: "btn",
      "aria-pressed": "false",
      onClick: () => {
        comparing = !comparing;
        syncControls();
        if (comparing) loadComparison();
        else load();
      },
    },
    icon("chart", 15),
    "Comparar las cuatro",
  );

  function syncControls() {
    [...segmented.children].forEach((button, index) => {
      button.setAttribute("aria-pressed", String(!comparing && STRATEGIES[index] === strategy));
    });
    compareButton.setAttribute("aria-pressed", String(comparing));
    note.textContent = comparing
      ? "Mismo usuario, mismo momento, cuatro estrategias. Las diferencias entre columnas son el aporte de cada señal."
      : STRATEGY_NOTE[strategy];
  }

  async function load() {
    body.replaceChildren(spinnerBlock("Calculando recomendaciones…"));
    meta.replaceChildren();
    try {
      const response = await api.recommendations(strategy, 12);
      const parts = [
        h(
          "span",
          { class: "chip chip-accent" },
          icon("sparkles", 11),
          SOURCE_LABEL[response.items[0]?.source] || "—",
        ),
        h("span", { class: "chip" }, `${response.history_size} juegos valorados`),
      ];
      if (response.cold_start) {
        parts.push(h("span", { class: "chip", style: { borderColor: "var(--serious)" } }, "Arranque en frío"));
      }
      meta.replaceChildren(...parts);

      const blocks = [];
      if (response.cold_start) blocks.push(coldStartNotice(response));

      if (!response.items.length) {
        blocks.push(
          emptyState(
            "Sin resultados",
            "No quedan juegos por recomendar con esta estrategia.",
          ),
        );
      } else {
        blocks.push(
          gameGrid(
            response.items.map((item) => item.game),
            (_game, index) => ({
              rank: index + 1,
              reason: response.items[index].reason,
              source: response.items[index].source,
            }),
          ),
        );
        blocks.push(componentsTable(response));
      }
      body.replaceChildren(...blocks);
    } catch (error) {
      toast(error.message, "error");
      body.replaceChildren(emptyState("No se pudo calcular", error.message));
    }
  }

  async function loadComparison() {
    body.replaceChildren(spinnerBlock("Calculando las cuatro estrategias…"));
    meta.replaceChildren();
    try {
      const responses = await Promise.all(
        COMPARABLE.map((key) => api.recommendations(key, 6)),
      );

      const columns = COMPARABLE.map((key, index) => {
        const response = responses[index];
        const effective = response.items[0]?.source;
        return h(
          "section",
          { class: "card", style: { minWidth: "0" } },
          h(
            "div",
            { class: "card-head", style: { marginBottom: "var(--s-3)" } },
            h(
              "div",
              null,
              h("h3", { class: "card-title" }, STRATEGY_LABEL[key]),
              h(
                "p",
                { class: "card-sub" },
                effective && effective !== key
                  ? `degradó a ${SOURCE_LABEL[effective]}`
                  : SOURCE_LABEL[key] || "—",
              ),
            ),
          ),
          h(
            "ol",
            { style: { listStyle: "none", padding: "0", display: "grid", gap: "var(--s-2)" } },
            response.items.map((item, position) =>
              h(
                "li",
                {
                  class: "row",
                  style: { gap: "var(--s-3)", cursor: "pointer", alignItems: "center" },
                  onClick: () => navigate(`/juego/${item.game.id}`),
                },
                h(
                  "span",
                  {
                    class: "avatar",
                    style: {
                      background: coverGradient(item.game.name),
                      color: "#fff",
                      width: "30px",
                      height: "30px",
                      borderRadius: "var(--r-sm)",
                      fontSize: "10px",
                    },
                  },
                  initials(item.game.name),
                ),
                h(
                  "span",
                  { style: { flex: "1", minWidth: "0" } },
                  h(
                    "span",
                    {
                      style: {
                        display: "block",
                        fontSize: "var(--fs-sm)",
                        fontWeight: "600",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      },
                    },
                    `${position + 1}. ${item.game.name}`,
                  ),
                  h(
                    "span",
                    { class: "muted tnum", style: { fontSize: "var(--fs-xs)" } },
                    `score ${item.score.toFixed(3)}`,
                  ),
                ),
              ),
            ),
          ),
        );
      });

      // Cuántos títulos comparte cada par de estrategias: cuantifica el solape.
      const sets = responses.map((response) => new Set(response.items.map((item) => item.game.id)));
      const overlapRows = [];
      for (let i = 0; i < COMPARABLE.length; i += 1) {
        for (let j = i + 1; j < COMPARABLE.length; j += 1) {
          const shared = [...sets[i]].filter((id) => sets[j].has(id)).length;
          overlapRows.push([
            `${STRATEGY_LABEL[COMPARABLE[i]]} vs ${STRATEGY_LABEL[COMPARABLE[j]]}`,
            `${shared} de 6`,
          ]);
        }
      }

      body.replaceChildren(
        h("div", { class: "grid grid-4" }, columns),
        h(
          "section",
          { class: "card", style: { marginTop: "var(--s-5)" } },
          h("h3", { class: "card-title" }, "Solape entre estrategias"),
          h(
            "p",
            { class: "card-sub", style: { marginBottom: "var(--s-3)" } },
            "Títulos en común dentro del top 6. Cuanto menor el solape, más aporta cada señal por su cuenta.",
          ),
          h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              null,
              h("thead", null, h("tr", null, h("th", null, "Par"), h("th", { class: "num" }, "En común"))),
              h(
                "tbody",
                null,
                overlapRows.map(([label, value]) =>
                  h("tr", null, h("td", null, label), h("td", { class: "num" }, value)),
                ),
              ),
            ),
          ),
        ),
      );
    } catch (error) {
      toast(error.message, "error");
      body.replaceChildren(emptyState("No se pudo comparar", error.message));
    }
  }

  syncControls();
  if (comparing) loadComparison();
  else load();

  return h(
    "div",
    null,
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, "Para vos"),
        h("h1", null, "Recomendaciones"),
        note,
      ),
      meta,
    ),
    h(
      "div",
      { class: "filter-bar" },
      segmented,
      h("span", { class: "spacer" }),
      compareButton,
    ),
    body,
  );
}

/** Aporte numérico de cada estrategia por juego: hace auditable la mezcla. */
function componentsTable(response) {
  const keys = [...new Set(response.items.flatMap((item) => Object.keys(item.components)))];
  if (!keys.length) return null;

  return h(
    "details",
    { class: "table-view", style: { marginTop: "var(--s-6)" } },
    h("summary", null, "Ver el aporte de cada estrategia por juego"),
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        null,
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "Juego"),
            h("th", { class: "num" }, "Score final"),
            keys.map((key) => h("th", { class: "num" }, key)),
          ),
        ),
        h(
          "tbody",
          null,
          response.items.map((item) =>
            h(
              "tr",
              null,
              h("td", null, item.game.name),
              h("td", { class: "num" }, item.score.toFixed(3)),
              keys.map((key) =>
                h(
                  "td",
                  { class: "num" },
                  item.components[key] !== undefined ? item.components[key].toFixed(3) : "—",
                ),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}
