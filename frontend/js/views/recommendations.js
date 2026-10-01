/* Recomendaciones para el rol jugador.
 *
 * Además de la lista, permite forzar cada estrategia y compararlas lado a
 * lado sobre el mismo usuario: es la manera de mostrar que el enfoque híbrido
 * no es una caja negra sino la combinación de dos señales identificables.
 */

import { api } from "../api.js";
import { gameCard, cover, saveToListButton, starRating } from "../components.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import {
  SOURCE_LABEL,
  STRATEGY_LABEL,
  coverGradient,
  emptyState,
  h,
  icon,
  initials,
  openModal,
  modalHead,
  spinnerBlock,
  toast,
} from "../ui.js";
import { coldStartNotice, openOnboarding } from "./onboarding.js";
import { startNewQuiz } from "./quiz.js";
import { discoverySection } from "./gametrack-score.js?v=score-explainer-1";

import { loadHomeCollections } from "./home-sections.js";

const STRATEGIES = ["auto", "ia_local", "hibrido", "contenido", "colaborativo", "popularidad"];
const COMPARABLE = ["ia_local", "hibrido", "contenido", "colaborativo", "popularidad"];

/** Explicación de cada estrategia, para que la demo se sostenga sola. */
const STRATEGY_NOTE = {
  auto: "Elige la estrategia según cuánto historial tenga el usuario.",
  ia_local: "Factores latentes aprendidos de valoraciones en esta PC. Usa tus notas actuales y se apoya en contenido cuando faltan datos.",
  hibrido:
    "Combina afinidad de contenido y patrones de valoraciones. El aporte colaborativo depende de la evidencia disponible.",
  contenido:
    "Compara géneros y etiquetas comunitarias ponderadas por votos. Aprende de tus valoraciones positivas y negativas.",
  colaborativo:
    "Filtrado ítem-ítem sobre la matriz usuario-ítem centrada por usuario. Necesita historial.",
  popularidad:
    "Valoración general con penalización por poca evidencia. Evita sobrevalorar juegos con muy pocas reseñas.",
};

const DISCOVERY = [
  ["familiar", "Ir a lo seguro", "Prioriza la afinidad con tu perfil."],
  ["balanced", "Un poco de todo", "Equilibra afinidad y variedad entre las sugerencias."],
  ["explore", "Más variedad", "Reduce la repetición de juegos parecidos dentro de la selección."],
];

function discoveryHero(guest = false) {
  return h("section", { class: "discovery-hero play-hero console-hero" },
    h("div", { class: "discovery-hero-copy" },
      h("p", { class: "eyebrow" }, "TU PRÓXIMA PARTIDA"),
      h("h1", null, "¿Qué jugamos ", h("br"), h("span", null, "hoy?")),
      h("p", { class: "discovery-lead" }, "Algo para vos. O para todo el grupo."),
      h("div", { class: "discovery-actions" },
        h("button", { class: "btn btn-primary", onClick: startNewQuiz }, "Encontrar un juego", icon("chevron", 17)),
        h("a", { class: "hero-catalog-link", href: "#/catalogo" }, "Explorar catálogo")),
      h("p", { class: "discovery-caption" }, "4 elecciones · A tu manera")),
    h("div", { class: "console-hero-art", "aria-label": "Juegos del catálogo" }));
}

function fillHero(hero, games) {
  const tiles = games.filter(game => game.background_image).slice(0, 3);
  const art = hero.querySelector(".console-hero-art");
  art.replaceChildren(...tiles.map(game => h("a", { class: "hero-game-tile", href: `#/juego/${game.id}`, "aria-label": `Ver ${game.name}`, title: game.name },
    cover(game), h("span", { class: "hero-game-name" }, game.name))));
  hero.classList.toggle("has-art", Boolean(tiles.length));
}

function personalCard(item, index, onRated) {
  return h("article", { class: "recommendation-card" },
    gameCard(item.game, { rank: index + 1, reason: item.reason, source: item.source }),
    item.signals?.length ? h("ul", { class: "recommendation-signals" },
      item.signals.slice(0, 3).map(signal => h("li", null, signal))) : null,
    h("div", { class: "recommendation-actions" },
      saveToListButton(item.game),
      h("button", { class: "btn btn-ghost btn-sm", onClick: () => openModal(close => h("div", null,
        modalHead(`¿Qué te pareció ${item.game.name}?`, "Tu valoración actualiza las próximas recomendaciones.", close),
        starRating(item.game.id, { onChange: () => { close(); onRated(); } }))) },
      icon("star", 14), "Ya lo jugué")));
}

export async function recommendationsView({ query } = { query: new URLSearchParams() }) {
  if (!isLoggedIn()) {
    const hero = discoveryHero(true);
    const sections = h("div", { class: "console-home-rows" });
    const intro = h("div", {class:"home-intro"});
    loadHomeSections(sections, hero, intro);
    return h("div", { class: "console-home" }, hero, intro, sections);
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
  let discovery = DISCOVERY.some(([key]) => key === query.get("variedad")) ? query.get("variedad") : "balanced";
  let requestVersion = 0;

  const hero = discoveryHero();
  const body = h("div");
  const meta = h("div", { class: "row", style: { gap: "var(--s-2)" } });
  const note = h("p", { class: "muted", style: { fontSize: "var(--fs-sm)", marginTop: "var(--s-3)" } });
  const profileHint = h("p", { class: "discovery-profile-hint" });
  const modelNote = h("p", { class: "local-model-note" });
  function describeModel(model = {}) {
    if (["ready", "stale"].includes(model.status)) {
      modelNote.textContent = `IA local · Entrenada con ${model.ratings} valoraciones de ${model.users} jugadores y ${model.games} juegos. `
        + (model.data_label === "demo" ? "Datos sintéticos de demostración; todavía no miden gustos de jugadores reales. " : "Datos declarados como observados. ")
        + (!model.automatic_eligible ? "Disponible para comparar en IA local; aún no habilitado en automático porque no superó la validación frente a la referencia. " : "")
        + (model.status === "stale" ? "Tus notas actuales ya se usan; hay cambios pendientes de incorporar al entrenamiento general." : "");
    } else {
      modelNote.textContent = "El modelo local todavía no está disponible para estos datos. Podés seguir descubriendo juegos por tus gustos, contenido y popularidad.";
    }
  }
  const varietyNote = h("p", { class: "discovery-control-note" });
  const variety = h("div", { class: "segmented", role: "group", "aria-label": "Variedad de recomendaciones" },
    DISCOVERY.map(([key, label]) => h("button", { onClick: () => {
      discovery = key;
      syncControls();
      if (comparing) loadComparison(); else load();
    } }, label)));

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
    "Comparar estrategias",
  );

  function syncControls() {
    [...variety.children].forEach((button, index) => button.setAttribute("aria-pressed", String(DISCOVERY[index][0] === discovery)));
    varietyNote.textContent = DISCOVERY.find(([key]) => key === discovery)[2];
    [...segmented.children].forEach((button, index) => {
      button.setAttribute("aria-pressed", String(!comparing && STRATEGIES[index] === strategy));
    });
    compareButton.setAttribute("aria-pressed", String(comparing));
    note.textContent = comparing
      ? "Mismo perfil y mismo momento: compará el modelo local y las otras estrategias."
      : STRATEGY_NOTE[strategy];
    const params = new URLSearchParams();
    if (strategy !== "auto") params.set("estrategia", strategy);
    if (comparing) params.set("comparar", "1");
    if (discovery !== "balanced") params.set("variedad", discovery);
    window.history.replaceState(null, "", `#/recomendaciones${params.size ? `?${params}` : ""}`);
  }

  async function load() {
    const version = ++requestVersion;
    body.replaceChildren(spinnerBlock("Calculando recomendaciones…"));
    meta.replaceChildren();
    try {
      const response = await api.recommendations(strategy, 12, discovery);
      if (version !== requestVersion) return;
      describeModel(response.local_model);
      profileHint.textContent = response.profile_hint || "Una selección a partir de tus gustos y valoraciones.";
      const parts = [
        h(
          "span",
          { class: "chip chip-accent" },
          icon("sparkles", 11),
          SOURCE_LABEL[response.effective_strategy || response.items[0]?.source] || "—",
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
        blocks.push(h("div", { class: "recommendation-grid" }, response.items.map((item, index) =>
          personalCard(item, index, () => { if (comparing) loadComparison(); else load(); }))));
        blocks.push(componentsTable(response));
      }
      body.replaceChildren(...blocks);
    } catch (error) {
      if (version !== requestVersion) return;
      toast(error.message, "error");
      body.replaceChildren(emptyState("No se pudo calcular", error.message));
    }
  }

  async function loadComparison() {
    const version = ++requestVersion;
    body.replaceChildren(spinnerBlock("Comparando estrategias…"));
    meta.replaceChildren();
    try {
      const responses = await Promise.all(
        COMPARABLE.map((key) => api.recommendations(key, 6, discovery)),
      );
      if (version !== requestVersion) return;
      describeModel(responses[0]?.local_model);
      profileHint.textContent = "Explorá cómo cambia la selección con cada estrategia. Todas usan el mismo perfil y ajuste de variedad.";

      const columns = COMPARABLE.map((key, index) => {
        const response = responses[index];
        const effective = response.effective_strategy || response.items[0]?.source;
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
                  ? `Usó ${SOURCE_LABEL[effective]} por la evidencia disponible`
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
                  style: { gap: "var(--s-3)", alignItems: "center" },
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
                    "a",
                    {
                      href: `#/juego/${item.game.id}`,
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
                    `Índice ${item.score.toFixed(3)}`,
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
            `${shared} · selecciones de ${sets[i].size} y ${sets[j].size}`,
          ]);
        }
      }

      body.replaceChildren(
        h("div", { class: "strategy-comparison-grid" }, columns),
        h(
          "section",
          { class: "card", style: { marginTop: "var(--s-5)" } },
          h("h3", { class: "card-title" }, "Solape entre estrategias"),
          h(
            "p",
            { class: "card-sub", style: { marginBottom: "var(--s-3)" } },
            "Títulos en común entre selecciones de hasta 6 juegos. Un menor solape indica selecciones diferentes; no demuestra mayor calidad.",
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
      if (version !== requestVersion) return;
      toast(error.message, "error");
      body.replaceChildren(emptyState("No se pudo comparar", error.message));
    }
  }

  syncControls();
  if (comparing) loadComparison();
  else load();

  const sections = h("div", { class: "console-home-rows" });
  const intro = h("div", {class:"home-intro"});
  loadHomeSections(sections, hero, intro);

  return h(
    "div",
    { class: "console-home" },
    hero,
    intro,
    discoverySection(),
    h("details", { class: "gts-advanced" }, h("summary", null, "Más formas de recomendar"),
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("h2", null, "Para vos"),
      ),
    ),
    h(
      "div",
      { class: "discovery-controls" },
      h("div", null, variety),
      h("a", { class: "btn btn-ghost", href: "#/catalogo" }, icon("star", 14), "Valorar más juegos"),
    ),
    h("details", { class: "recommendation-lab", open: comparing || strategy !== "auto" },
      h("summary", null, icon("chart", 15), "Ajustes de recomendaciones"),
      meta, profileHint, modelNote, varietyNote, h("div", { class: "filter-bar" }, segmented, compareButton), note),
    body),
    sections,
  );
}

/** Colecciones públicas y agenda; GameTrackScore conserva su feed propio. */
function loadHomeSections(container, hero, intro) {
  loadHomeCollections(container, intro, home => {
    fillHero(hero, [...(home.destacados || []), ...(home.populares || [])]
      .filter((game, index, all) => all.findIndex(item => item.id === game.id) === index));
  });
}

/** Aporte numérico de cada estrategia por juego: hace auditable la mezcla. */
function componentsTable(response) {
  const keys = [...new Set(response.items.flatMap((item) => Object.keys(item.components)))];
  if (!keys.length) return null;

  return h(
    "details",
    { class: "table-view", style: { marginTop: "var(--s-6)" } },
    h("summary", null, "Ver el aporte de cada estrategia por juego"),
    h("p", { class: "card-sub" }, "Los aportes suman el índice de afinidad. No es una probabilidad de que te guste. La variedad también influye en el orden de la selección."),
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
            h("th", { class: "num" }, "Índice de afinidad"),
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
