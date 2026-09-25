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
import { cover, saveToListButton } from "../components.js";
import { navigate } from "../router.js";
import { isLoggedIn, isDeveloper, state } from "../store.js";
import { cauldronLoader, h, icon, mount, toast } from "../ui.js";

/**
 * Última tirada del asistente, para poder volver a ella desde la ficha de un
 * juego sin tener que responder todo de nuevo (ver el link "Volver a los
 * resultados" en views/game.js). Vive a nivel de módulo a propósito: es
 * estado de sesión, no de una instancia de la vista, que se recrea entera en
 * cada navegación.
 */
let lastRun = null;
let quizGeneration = 0;

/** Arranca el asistente de cero, descartando la tirada anterior. Es lo que
 * hace el botón del encabezado: entrar ahí es pedir una recomendación nueva,
 * no volver a ver la de antes. */
export function startNewQuiz() {
  quizGeneration += 1;
  lastRun = null;
  navigate("/que-jugamos");
}

const QUESTIONS = [
  {
    key: "tiempo",
    title: "¿Cuánto querés dedicarle?",
    note: "Las horas de los reseñadores orientan el compromiso; no miden cuánto dura una partida ni garantizan completar el juego.",
    options: [
      { emoji: "☕", title: "Algo corto", note: "Referencia de hasta 15 h", maxPlaytime: 15 },
      { emoji: "🛋️", title: "Varias sesiones", note: "Referencia de hasta 40 h", maxPlaytime: 40 },
      { emoji: "🏕️", title: "Sin apuro", note: "Sin límite", maxPlaytime: null },
    ],
  },
  {
    key: "animo",
    title: "¿Cómo venís de ánimo?",
    note: "Buscamos juegos que acompañen lo que tenés ganas de hacer hoy.",
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
    note: "Buscamos modos de juego que coincidan con tu plan.",
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
    note: "También tenemos en cuenta lo que cuentan las reseñas sobre ese aspecto.",
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
    { class: "quiz-progress", role: "progressbar", "aria-label": "Progreso del asistente", "aria-valuemin": "1", "aria-valuemax": String(QUESTIONS.length), "aria-valuenow": String(step + 1) },
    QUESTIONS.map((_question, index) => h("span", { dataset: { on: String(index <= step) }, "aria-hidden": "true" })),
  );
}

function resultCard(pick, index) {
  const game = pick.game;
  const relaxed = pick.relaxed_criteria || [];
  return h(
    "article",
    { class: "quiz-result-card" },
    h("a", { class: "quiz-result-cover", href: `#/juego/${game.id}?volver=que-jugamos`, "aria-label": `Ver ${game.name}` }, cover(game, { rank: index + 1 })),
    h(
      "div",
      { class: "quiz-result-body" },
      h("p", { class: "eyebrow" }, relaxed.length ? "Una alternativa" : "Para tu plan"),
      h(
        "div",
        { class: "row", style: { gap: "var(--s-2)" } },
        h("h2", null, h("a", { href: `#/juego/${game.id}?volver=que-jugamos` }, game.name)),
        game.avg_rating
          ? h("span", { class: "chip" }, icon("star", 10), game.avg_rating.toFixed(2))
          : null,
      ),
      h(
        "p",
        { class: "account-note", style: { display: "block" } },
        game.genres.map((genre) => genre.name).join(" · "),
      ),
      h(
        "p",
        { class: "row", style: { gap: "var(--s-2)", marginTop: "4px" } },
        icon("sparkles", 11),
        h("span", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, pick.reason),
      ),
      pick.matched_criteria?.length ? h("div", { class: "row quiz-match-chips" },
        pick.matched_criteria.map(criterion => h("span", { class: "chip" }, icon("check", 11), criterion))) : null,
      relaxed.length ? h("p", { class: "quiz-exception" }, icon("info", 13), `Se amplió: ${relaxed.join(", ")}.`) : null,
      pick.time_note ? h("p", { class: "quiz-time-note" }, icon("clock", 13), pick.time_note) : null,
      pick.group_fit ? h("section", { class: "group-fit", "aria-label": "Afinidad de los participantes" },
        h("h3", null, "Cómo encaja con el grupo"),
        pick.group_fit.participants.map(member => h("div", { class: "group-fit-row" },
          h("span", null, member.username),
          h("meter", { min: "0", max: "1", value: member.score, "aria-label": `Afinidad estimada de ${member.username}` }),
          h("span", { class: "muted" }, member.basis === "sin_datos" ? "Sin perfil aún"
            : member.score >= 0.7 ? "Alta" : member.score >= 0.4 ? "Media" : "Baja"))),
        h("p", { class: "discovery-control-note" }, "Afinidad estimada según los perfiles disponibles; no garantiza que les guste.")) : null,
      pick.aspect_evidence
        ? h(
            "blockquote",
            { class: "quote", style: { marginLeft: "0", marginRight: "0" } },
            h("p", null, `“${pick.aspect_evidence}”`),
            h("cite", null, `Fragmento de una reseña del catálogo${pick.aspect_mentions ? ` · Balance sobre ${pick.aspect_mentions} menciones del aspecto` : ""}`),
          )
        : null,
      h("div", { class: "discovery-actions" },
        h("a", { class: "btn btn-primary btn-sm", href: `#/juego/${game.id}?volver=que-jugamos` }, "Ver juego", icon("chevron", 13)),
        isLoggedIn() && !isDeveloper() ? saveToListButton(game) : null),
    ),
  );
}

export async function quizView() {
  const generation = ++quizGeneration;
  const container = h("div", { class: "quiz-shell" });
  const answers = {};
  let step = 0;
  let editing = false;
  let allowRelaxation = true;
  let excluded = [];
  let friendIds = [];
  let groupStrategy = "balanced";
  let friends = [];
  let friendsError = null;
  let requestRevision = 0;
  const ownerId = state.user?.id ?? null;
  const isCurrent = () => generation === quizGeneration && (state.user?.id ?? null) === ownerId;
  if (lastRun && lastRun.ownerId !== ownerId) lastRun = null;
  if (isLoggedIn() && !isDeveloper()) {
    try { friends = (await api.friends()).friends; }
    catch (error) { friendsError = error.message; }
  }
  if (!isCurrent()) return container;
  // Revalidar las amistades al volver, incluso si quedaron resultados en memoria.
  if (lastRun?.friendIds?.some(id => !friends.some(friend => friend.id === id))) lastRun = null;

  function renderGroupPicker(proceed) {
    requestRevision += 1;
    const count = h("p", { class: "discovery-control-note", role: "status" });
    const options = h("div", { class: "friend-picker" });
    function update() {
      count.textContent = `${friendIds.length} de 4 amigos seleccionados · Vos también participás`;
      options.querySelectorAll("input").forEach(input => { input.disabled = !input.checked && friendIds.length >= 4; });
    }
    friends.forEach(friend => options.append(h("label", { class: "friend-picker-option" },
      h("input", { type: "checkbox", checked: friendIds.includes(friend.id), onChange: event => {
        friendIds = event.target.checked ? [...new Set([...friendIds, friend.id])] : friendIds.filter(id => id !== friend.id);
        update();
      } }), h("span", null, h("strong", null, friend.full_name || friend.username), h("small", null, `@${friend.username}`)))));
    const strategySelect = h("select", { class: "select", id: "group-strategy", onChange: event => { groupStrategy = event.target.value; } },
      h("option", { value: "balanced", selected: groupStrategy === "balanced" }, "Equilibrar los gustos de todos"),
      h("option", { value: "average", selected: groupStrategy === "average" }, "Priorizar la afinidad promedio"));
    update();
    mount(container,
      head("Recomendación cruzada", "¿Quiénes se suman?", "Elegí amigos para tener en cuenta sus gustos. Podés seguir sin seleccionar a nadie."),
      friendsError ? h("p", { class: "notice notice-warn" }, `No pudimos cargar tus amigos: ${friendsError}`) : null,
      count, options,
      !friends.length ? h("p", { class: "muted" }, "Primero necesitás una amistad aceptada para cruzar perfiles.") : null,
      h("div", { class: "group-strategy-control" }, h("label", { for: "group-strategy" }, "Cómo buscamos un acuerdo"), strategySelect),
      h("p", { class: "discovery-control-note" }, "Equilibrar reduce el peso de opciones que encajan con algunos pero muy poco con otros. Siempre se conserva un modo de juego compartido."),
      h("div", { class: "discovery-actions" },
        h("button", { class: "btn btn-primary", onClick: proceed }, "Continuar", icon("chevron", 14)),
        h("a", { class: "btn", href: "#/amigos" }, "Gestionar amigos")));
  }

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
    requestRevision += 1;
    const question = QUESTIONS[step];
    mount(
      container,
      head(`Tu plan · Paso ${step + 1} de ${QUESTIONS.length}`, "¿Qué jugamos hoy?", question.note),
      progress(step),
      h("h2", { class: "quiz-question-title", tabindex: "-1" }, question.title),
      h(
        "div",
        { class: "quiz-options" },
        question.options.map((option) =>
          h(
            "button",
            {
              class: "quiz-option",
              "aria-pressed": String(answers[question.key]?.title === option.title),
              onClick: () => {
                answers[question.key] = option;
                if (question.key === "compania" && option.company === "solo") friendIds = [];
                const proceed = () => {
                  if (editing) {
                    editing = false;
                    excluded = [];
                    renderResults();
                  } else if (step < QUESTIONS.length - 1) {
                    step += 1;
                    renderQuestion();
                  } else renderResults();
                };
                if (question.key === "compania" && option.company !== "solo" && isLoggedIn() && !isDeveloper()) {
                  renderGroupPicker(proceed);
                } else proceed();
              },
            },
            h("div", { class: "quiz-option-emoji" }, option.emoji),
            h("div", { class: "quiz-option-title" }, option.title),
            h("div", { class: "quiz-option-note" }, option.note),
          ),
        ),
      ),
      step > 0 && !editing
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
    if (container.isConnected) container.querySelector(".quiz-question-title")?.focus();
  }

  async function renderResults() {
    const revision = ++requestRevision;
    const isCurrentRequest = () => isCurrent() && container.isConnected && revision === requestRevision;
    mount(container, head("Asistente", "¿Qué jugamos hoy?"), cauldronLoader());

    try {
      const response = await api.quizSuggest({
        mood: answers.animo.mood,
        company: answers.compania.company,
        max_playtime: answers.tiempo.maxPlaytime,
        priority_aspect: answers.prioridad.aspect,
        allow_relaxation: allowRelaxation,
        exclude_game_ids: excluded,
        friend_ids: friendIds,
        group_strategy: groupStrategy,
      });
      if (!isCurrentRequest()) return;
      lastRun = { answers: { ...answers }, response, allowRelaxation, excluded: [...excluded],
        ownerId, friendIds: [...friendIds], groupStrategy };
      showResults(lastRun);
    } catch (error) {
      if (!isCurrentRequest()) return;
      toast(error.message, "error");
      step = QUESTIONS.length - 1;
      renderQuestion();
    }
  }

  function showResults({ answers: given, response }) {
    Object.assign(answers, given);
    mount(
      container,
      head(
        "Tu selección para hoy",
        "Tu plan, tus próximos juegos.",
        "Tocá una respuesta para ajustarla o pedí otras opciones con el mismo plan.",
      ),
      h("div", { class: "quiz-answer-summary", "aria-label": "Editar tus respuestas" },
        QUESTIONS.map((question, index) => h("button", { class: "chip", onClick: () => {
          step = index;
          editing = true;
          renderQuestion();
        } }, given[question.key].emoji, given[question.key].title, icon("chevronDown", 11)))),
      response.group ? h("section", { class: "group-summary card" },
        h("h2", null, "Una partida para todos"),
        h("p", null, response.group.members.map(member => member.username).join(" + ")),
        h("p", { class: "discovery-control-note" }, response.group.notice)) : null,
      isLoggedIn() && !isDeveloper() && given.compania.company !== "solo" ?
        h("button", { class: "btn btn-sm", onClick: () => renderGroupPicker(() => { excluded = []; renderResults(); }) },
          icon("user", 14), friendIds.length ? "Cambiar el grupo" : "Incluir amigos en la recomendación") : null,
      h("label", { class: "quiz-strict-control" },
        h("input", { type: "checkbox", checked: !allowRelaxation, onChange: event => {
          allowRelaxation = !event.target.checked;
          excluded = [];
          renderResults();
        } }), "Respetar todos mis criterios, aunque haya menos opciones"),
      response.exact_matches !== undefined ? h("p", { class: "discovery-control-note", role: "status" },
        `${response.exact_matches} opciones cumplen todos tus filtros · ${response.evaluated_candidates} juegos evaluados`) : null,
      response.relaxed.length
        ? h(
            "div",
            { class: "notice notice-warn", style: { marginBottom: "var(--s-4)" } },
            icon("info", 15),
            h(
              "div",
              null,
              "Algunas alternativas amplían estos criterios: ",
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
            disabled: !response.picks.length || excluded.length >= 195,
            onClick: () => {
              excluded = [...new Set([...excluded, ...response.picks.map(pick => pick.game.id)])].slice(0, 200);
              renderResults();
            },
          },
          icon("refresh", 14),
          "Ver otras opciones",
        ),
        excluded.length ? h("button", { class: "btn", onClick: () => { excluded = []; renderResults(); } }, "Volver a las primeras") : null,
        h(
          "button",
          {
            class: "btn btn-ghost",
            onClick: () => {
              lastRun = null;
              excluded = [];
              editing = false;
              allowRelaxation = true;
              friendIds = [];
              groupStrategy = "balanced";
              Object.keys(answers).forEach(key => delete answers[key]);
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
  if (lastRun) {
    allowRelaxation = lastRun.allowRelaxation;
    excluded = [...lastRun.excluded];
    friendIds = [...(lastRun.friendIds || [])];
    groupStrategy = lastRun.groupStrategy || "balanced";
    showResults(lastRun);
  }
  else renderQuestion();
  return container;
}
