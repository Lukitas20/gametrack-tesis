/* Asistente "¿Qué jugamos hoy?"
 *
 * Tres preguntas rápidas que se traducen a filtros del catálogo. La API filtra
 * por una etiqueta a la vez, así que se consulta cada etiqueta candidata por
 * separado y se intersecan los resultados del lado del cliente; si la
 * intersección queda corta, se relajan los criterios en orden y se avisa cuál
 * se soltó, en lugar de devolver una lista vacía.
 */

import { api } from "../api.js";
import { navigate } from "../router.js";
import { loadCatalog, state } from "../store.js";
import {
  cauldronLoader,
  coverGradient,
  h,
  icon,
  initials,
  modalHead,
  openModal,
  toast,
} from "../ui.js";

const QUESTIONS = [
  {
    key: "tiempo",
    title: "¿Cuánto tiempo tenés?",
    note: "Filtra por la duración típica del juego.",
    options: [
      { emoji: "☕", title: "Una tarde", note: "Hasta 15 horas", maxPlaytime: 15 },
      { emoji: "🛋️", title: "Un fin de semana", note: "Hasta 40 horas", maxPlaytime: 40 },
      { emoji: "🏕️", title: "Sin apuro", note: "Sin límite", maxPlaytime: null },
    ],
  },
  {
    key: "animo",
    title: "¿Cómo venís de ánimo?",
    note: "Se traduce a géneros del catálogo.",
    // El catálogo real de Steam no tiene etiquetas de "ánimo" (narrativo,
    // relajante, etc.) — sus categorías son de plataforma (logros, mando,
    // nube), no de tono. El género es la señal real más parecida que hay.
    options: [
      { emoji: "🧠", title: "Quiero una historia", note: "RPG, aventura", genres: ["rol", "aventura"] },
      { emoji: "🔥", title: "Quiero desafío", note: "Acción, estrategia", genres: ["accion", "estrategia"] },
      { emoji: "🌿", title: "Quiero relajarme", note: "Casual, simuladores", genres: ["casual", "simuladores"] },
      { emoji: "⚔️", title: "Quiero competir", note: "Acción, deportes, carreras", genres: ["accion", "deportes", "carreras"] },
    ],
  },
  {
    key: "compania",
    title: "¿Solo o con gente?",
    note: "Última pista antes de sugerir.",
    options: [
      { emoji: "🎧", title: "Solo", note: "Un jugador", tags: ["un-jugador"] },
      { emoji: "👥", title: "Con amigos", note: "Cooperativo", tags: ["cooperativo", "pantalla-dividida"] },
      { emoji: "🌐", title: "En línea", note: "Multijugador", tags: ["multijugador", "competitivo"] },
    ],
  },
];

export function openQuiz() {
  openModal((close) => {
    const container = h("div");
    const answers = {};
    let step = 0;

    function progress() {
      return h(
        "div",
        { class: "quiz-progress" },
        QUESTIONS.map((_question, index) => h("span", { dataset: { on: String(index <= step) } })),
      );
    }

    function renderQuestion() {
      const question = QUESTIONS[step];
      container.replaceChildren(
        modalHead("¿Qué jugamos hoy?", question.note, close),
        progress(),
        h("h3", { style: { marginBottom: "var(--s-4)" } }, question.title),
        h(
          "div",
          { class: "quiz-options" },
          question.options.map((option) =>
            h(
              "button",
              {
                class: "quiz-option",
                onClick: () => {
                  answers[question.key] = option;
                  if (step < QUESTIONS.length - 1) {
                    step += 1;
                    renderQuestion();
                  } else {
                    renderResults();
                  }
                },
              },
              h("div", { class: "quiz-option-emoji" }, option.emoji),
              h("div", { class: "quiz-option-title" }, option.title),
              h("div", { class: "quiz-option-note" }, option.note),
            ),
          ),
        ),
        step > 0
          ? h(
              "div",
              { class: "row", style: { marginTop: "var(--s-5)" } },
              h(
                "button",
                {
                  class: "btn btn-ghost btn-sm",
                  onClick: () => {
                    step -= 1;
                    renderQuestion();
                  },
                },
                icon("arrowLeft", 13),
                "Atrás",
              ),
            )
          : null,
      );
    }

    async function renderResults() {
      container.replaceChildren(
        modalHead("Buscando…", "Cruzando tus respuestas con el catálogo.", close),
        cauldronLoader("Eligiendo tres juegos…"),
      );

      try {
        await loadCatalog();
        const { games, relaxed } = await pickGames(answers);

        container.replaceChildren(
          modalHead(
            "Tres para hoy",
            [
              answers.animo.title.toLowerCase(),
              answers.compania.title.toLowerCase(),
              answers.tiempo.title.toLowerCase(),
            ].join(" · "),
            close,
          ),
          relaxed.length
            ? h(
                "div",
                { class: "notice notice-warn", style: { marginBottom: "var(--s-4)" } },
                icon("info", 15),
                h(
                  "div",
                  null,
                  "No había suficientes juegos con todos los criterios, así que se relajó: ",
                  h("strong", null, relaxed.join(", ")),
                  ".",
                ),
              )
            : null,
          games.length
            ? h(
                "div",
                { style: { display: "grid", gap: "var(--s-3)" } },
                games.map((game, index) =>
                  h(
                    "button",
                    {
                      class: "account-card",
                      onClick: () => {
                        close();
                        navigate(`/juego/${game.id}`);
                      },
                    },
                    h(
                      "span",
                      {
                        class: "avatar",
                        style: {
                          background: coverGradient(game.name),
                          color: "#fff",
                          width: "44px",
                          height: "44px",
                          borderRadius: "var(--r-sm)",
                        },
                      },
                      initials(game.name),
                    ),
                    h(
                      "span",
                      { style: { flex: "1", minWidth: "0" } },
                      h(
                        "span",
                        { class: "row", style: { gap: "var(--s-2)" } },
                        h("span", { class: "account-name" }, `${index + 1}. ${game.name}`),
                        game.avg_rating
                          ? h("span", { class: "chip" }, icon("star", 10), game.avg_rating.toFixed(2))
                          : null,
                      ),
                      h(
                        "span",
                        { class: "account-note", style: { display: "block" } },
                        [
                          game.playtime_hours ? `${game.playtime_hours} h` : null,
                          game.genres.map((genre) => genre.name).join(", "),
                        ]
                          .filter(Boolean)
                          .join(" · "),
                      ),
                    ),
                    icon("chevron", 16, "muted"),
                  ),
                ),
              )
            : h("p", { class: "muted" }, "No encontramos nada que encaje. Probá con otras respuestas."),
          h(
            "div",
            { class: "row", style: { marginTop: "var(--s-5)", justifyContent: "flex-end" } },
            h(
              "button",
              {
                class: "btn",
                onClick: () => {
                  step = 0;
                  renderQuestion();
                },
              },
              icon("refresh", 14),
              "Volver a empezar",
            ),
          ),
        );
      } catch (error) {
        toast(error.message, "error");
        close();
      }
    }

    renderQuestion();
    return container;
  });
}

/**
 * Cruza las respuestas contra el catálogo.
 *
 * Devuelve los tres mejores por nota y la lista de criterios que hubo que
 * relajar para llegar a tres.
 */
async function pickGames(answers) {
  const availableGenres = new Set(state.genres.map((genre) => genre.slug));
  const availableTags = new Set(state.tags.map((tag) => tag.slug));
  const moodGenres = answers.animo.genres.filter((slug) => availableGenres.has(slug));
  const companyTags = answers.compania.tags.filter((slug) => availableTags.has(slug));
  const maxPlaytime = answers.tiempo.maxPlaytime;

  async function fetchByFilter(param, values) {
    if (!values.length) return null;
    const pages = await Promise.all(
      values.map((value) => api.games({ [param]: value, limit: 100, sort: "rating" })),
    );
    const merged = new Map();
    for (const page of pages) {
      for (const game of page.items) merged.set(game.id, game);
    }
    return merged;
  }

  const [moodSet, companySet] = await Promise.all([
    fetchByFilter("genre", moodGenres),
    fetchByFilter("tag", companyTags),
  ]);

  const relaxed = [];
  const byPlaytime = (game) =>
    maxPlaytime === null || game.playtime_hours === null || game.playtime_hours <= maxPlaytime;

  // 1) Ánimo ∩ compañía, respetando la duración.
  let candidates = [];
  if (moodSet && companySet) {
    candidates = [...moodSet.values()].filter((game) => companySet.has(game.id) && byPlaytime(game));
  }

  // 2) Se suelta la duración.
  if (candidates.length < 3 && moodSet && companySet) {
    const wider = [...moodSet.values()].filter((game) => companySet.has(game.id));
    if (wider.length > candidates.length) {
      candidates = wider;
      relaxed.push("la duración");
    }
  }

  // 3) Se suelta la compañía.
  if (candidates.length < 3 && moodSet) {
    const wider = [...moodSet.values()].filter(byPlaytime);
    if (wider.length >= 3) {
      candidates = wider;
      if (!relaxed.includes("con quién jugás")) relaxed.push("con quién jugás");
    }
  }

  // 4) Último recurso: los mejor valorados del catálogo.
  if (candidates.length < 3) {
    const page = await api.games({ limit: 12, sort: "rating" });
    candidates = page.items;
    relaxed.push("el ánimo");
  }

  candidates.sort((a, b) => b.avg_rating - a.avg_rating);
  return { games: candidates.slice(0, 3), relaxed };
}
