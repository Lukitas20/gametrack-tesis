/* Asistente "¿Qué jugamos hoy?"
 *
 * Vista de pantalla completa, no modal: es la feature principal de la
 * página, se merece su propio lugar. Las respuestas arman un perfil que
 * entra al motor de recomendación real (contenido + popularidad) en vez de
 * un cruce de etiquetas armado a mano, y si se elige un aspecto prioritario,
 * el orden final sale de sentimiento real de reseñas (ABSA) — "quiero una
 * buena historia" termina siendo "juegos donde reseñas reales dicen que la
 * historia es buena", no sólo "género = Rol".
 */

import { api } from "../api.js";
import { cover } from "../components.js";
import { navigate } from "../router.js";
import { cauldronLoader, h, icon, mount, toast } from "../ui.js";

/**
 * Última tirada del asistente, para poder volver a ella desde la ficha de un
 * juego sin tener que responder todo de nuevo (ver el link "Volver a los
 * resultados" en views/game.js). Vive a nivel de módulo a propósito: es
 * estado de sesión, no de una instancia de la vista, que se recrea entera en
 * cada navegación.
 */
let lastRun = null;

/** Arranca el asistente de cero, descartando la tirada anterior. Es lo que
 * hace el botón del encabezado: entrar ahí es pedir una recomendación nueva,
 * no volver a ver la de antes. */
export function startNewQuiz() {
  lastRun = null;
  navigate("/que-jugamos");
}

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
    note: "Arma un perfil de contenido real, no un filtro exacto.",
    // El frontend manda la CLAVE del ánimo; qué significa (etiquetas
    // comunitarias votadas, géneros de respaldo, requisitos duros) lo
    // resuelve el backend en app/ml/quiz_vocab.py, donde es testeable y
    // versionable. Acá sólo queda la presentación.
    options: [
      { emoji: "🧠", title: "Quiero una historia", note: "Narrativa, decisiones", mood: "historia" },
      { emoji: "🔥", title: "Quiero desafío", note: "Exigente, souls, roguelike", mood: "desafio" },
      { emoji: "🌿", title: "Quiero relajarme", note: "Tranquilo, sin presión", mood: "relajarme" },
      { emoji: "⚔️", title: "Quiero competir", note: "Jugador contra jugador", mood: "competir" },
    ],
  },
  {
    key: "compania",
    title: "¿Solo o con gente?",
    note: "Última pista antes de sugerir.",
    // También por clave: el backend une las categorías de la tienda (en
    // español) con las etiquetas comunitarias equivalentes de SteamSpy (en
    // inglés), y esa unión evoluciona sin tocar el frontend.
    options: [
      { emoji: "🎧", title: "Solo", note: "Un jugador", company: "solo" },
      { emoji: "👥", title: "Con amigos", note: "Cooperativo o pantalla partida", company: "amigos" },
      { emoji: "🌐", title: "En línea", note: "Multijugador por internet", company: "en-linea" },
    ],
  },
  {
    key: "prioridad",
    title: "¿Qué es lo que más te importa?",
    note: "Ordena por lo que dicen reseñas reales de ese aspecto puntual.",
    options: [
      { emoji: "📖", title: "La historia", note: "Guion, personajes, ritmo", aspect: "historia" },
      { emoji: "🎮", title: "Que se sienta bien", note: "Controles, mecánicas", aspect: "jugabilidad" },
      { emoji: "🎨", title: "Que sea lindo", note: "Arte, gráficos", aspect: "graficos" },
      { emoji: "⚙️", title: "Que ande bien", note: "Sin bugs, buen rendimiento", aspect: "optimizacion" },
      { emoji: "🤷", title: "No tengo preferencia", note: "Que decida el resto", aspect: null },
    ],
  },
];

function progress(step) {
  return h(
    "div",
    { class: "quiz-progress" },
    QUESTIONS.map((_question, index) => h("span", { dataset: { on: String(index <= step) } })),
  );
}

function resultCard(pick, index) {
  const game = pick.game;
  return h(
    "button",
    {
      class: "account-card",
      style: { alignItems: "flex-start", textAlign: "left", height: "auto" },
      // `volver` deja rastro de dónde vino para que la ficha del juego pueda
      // ofrecer el camino de regreso a esta misma lista.
      onClick: () => navigate(`/juego/${game.id}?volver=que-jugamos`),
    },
    h("div", { style: { width: "64px", flexShrink: "0" } }, cover(game)),
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
        game.genres.map((genre) => genre.name).join(", "),
      ),
      h(
        "span",
        { class: "row", style: { gap: "var(--s-2)", marginTop: "4px" } },
        icon("sparkles", 11),
        h("span", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, pick.reason),
      ),
      pick.aspect_evidence
        ? h(
            "blockquote",
            { class: "quote quote-positive", style: { marginLeft: "0", marginRight: "0" } },
            `"${pick.aspect_evidence}"`,
          )
        : null,
    ),
    icon("chevron", 16, "muted"),
  );
}

export async function quizView() {
  const container = h("div");
  const answers = {};
  let step = 0;

  function head(eyebrow, title, note) {
    return h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, eyebrow),
        h("h1", null, title),
        note && h("p", null, note),
      ),
    );
  }

  function renderQuestion() {
    const question = QUESTIONS[step];
    mount(
      container,
      head("Asistente", "¿Qué jugamos hoy?", question.note),
      progress(step),
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
    mount(container, head("Asistente", "¿Qué jugamos hoy?"), cauldronLoader());

    try {
      const response = await api.quizSuggest({
        mood: answers.animo.mood,
        company: answers.compania.company,
        max_playtime: answers.tiempo.maxPlaytime,
        priority_aspect: answers.prioridad.aspect,
      });
      lastRun = { answers: { ...answers }, response };
      showResults(lastRun);
    } catch (error) {
      toast(error.message, "error");
      step = QUESTIONS.length - 1;
      renderQuestion();
    }
  }

  function showResults({ answers: given, response }) {
    mount(
      container,
      head(
        "Tres para hoy",
        [given.animo.title, given.compania.title, given.tiempo.title].join(" · "),
      ),
      response.relaxed.length
        ? h(
            "div",
            { class: "notice notice-warn", style: { marginBottom: "var(--s-4)" } },
            icon("info", 15),
            h(
              "div",
              null,
              "No había suficientes juegos con todos los criterios, así que se relajó: ",
              h("strong", null, response.relaxed.join(", ")),
              ".",
            ),
          )
        : null,
      response.picks.length
        ? h(
            "div",
            { style: { display: "grid", gap: "var(--s-3)" } },
            response.picks.map((pick, index) => resultCard(pick, index)),
          )
        : h("p", { class: "muted" }, "No encontramos nada que encaje. Probá con otras respuestas."),
      h(
        "div",
        { class: "row", style: { marginTop: "var(--s-6)" } },
        h(
          "button",
          {
            class: "btn btn-primary",
            onClick: () => {
              lastRun = null;
              step = 0;
              renderQuestion();
            },
          },
          icon("refresh", 14),
          "Volver a empezar",
        ),
      ),
    );
  }

  // Volver desde la ficha de un juego cae acá: si hay una tirada guardada se
  // muestra tal cual, sin rehacer las preguntas ni volver a pedirle nada al
  // motor.
  if (lastRun) showResults(lastRun);
  else renderQuestion();
  return container;
}
