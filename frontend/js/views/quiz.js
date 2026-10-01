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
import { isLoggedIn, isDeveloper, state, displayName } from "../store.js";
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
let artworkPromise = null;

function catalogArtwork() {
  const art=h("div",{class:"play-catalog-art","aria-hidden":"true"});
  artworkPromise ||= api.home(4).then(data => data.destacados.filter(game => game.background_image).slice(0,3)).catch(() => []);
  artworkPromise.then(games => art.replaceChildren(...games.map(game => h("img",{src:game.background_image,alt:"",loading:"lazy",onError:event => event.target.remove()}))));
  return art;
}

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
    label: "Tiempo", symbol: "clock",
    title: "¿Cuánto querés dedicarle?",
    note: "Elegí el compromiso que buscás, desde algo breve hasta una aventura sin apuro.",
    help: "Las horas son una referencia basada en reseñadores, no la duración de una partida ni del juego completo.",
    options: [
      { emoji: "☕", title: "Algo corto", note: "Referencia de hasta 15 h", maxPlaytime: 15 },
      { emoji: "🛋️", title: "Varias sesiones", note: "Referencia de hasta 40 h", maxPlaytime: 40 },
      { emoji: "🏕️", title: "Sin apuro", note: "Sin límite", maxPlaytime: null },
    ],
  },
  {
    key: "animo",
    label: "Ánimo", symbol: "heart",
    title: "¿Qué tenés ganas de vivir?",
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
    label: "Compañía", symbol: "user",
    title: "¿Con quién jugamos?",
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
    label: "Prioridad", symbol: "sparkles",
    title: "¿Qué hace que un juego te encante?",
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
  return h("ol", { class: "play-stepper", "aria-label": "Pasos de tu recomendación" },
    QUESTIONS.map((question, index) => h("li", {
      class: index < step ? "complete" : index === step ? "current" : "",
      "aria-current": index === step ? "step" : null,
    }, h("span", { class: "play-step-number", "aria-hidden": "true" }, index < step ? icon("check", 14) : String(index + 1).padStart(2, "0")),
    h("span", null, question.label))));
}

function optionIcon(question, option, index) {
  const symbols = { tiempo: ["clock", "list", "sun"], animo: ["quote", "trending", "sun", "dice"], compania: ["user", "heart", "sparkles"], prioridad: ["quote", "dice", "star", "check", "sparkles"] };
  return icon(symbols[question.key][index], 25);
}

function resultCard(pick, index, memberName) {
  const game = pick.game;
  const relaxed = pick.relaxed_criteria || [];
  return h(
    "article",
    { class: `quiz-result-card${index === 0 ? " quiz-result-featured" : ""}` },
    h("a", { class: "quiz-result-cover", href: `#/juego/${game.id}?volver=que-jugamos`, "aria-label": `Ver ${game.name}` }, cover(game, { rank: index + 1 })),
    h(
      "div",
      { class: "quiz-result-body" },
      h("p", { class: "eyebrow quiz-result-kicker" }, icon(index === 0 ? "sparkles" : "dice", 14), `${String(index + 1).padStart(2, "0")} / ${relaxed.length ? "Para explorar" : index === 0 ? "Tu primera propuesta" : "También para vos"}`),
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
      h("details",{class:"quiz-fit-details"},h("summary",null,icon("sparkles",13),"Por qué encaja con tu plan"),
        h("p",{class:"muted"},pick.reason),
        pick.matched_criteria?.length ? h("div", { class: "row quiz-match-chips" },
          pick.matched_criteria.map(criterion => h("span", { class: "chip" }, icon("check", 11), criterion))) : null),
      relaxed.length ? h("p", { class: "quiz-exception" }, icon("info", 13), `Se amplió: ${relaxed.join(", ")}.`) : null,
      pick.time_note ? h("p", { class: "quiz-time-note" }, icon("clock", 13), pick.time_note) : null,
      pick.group_fit ? h("section", { class: "group-fit", "aria-label": "Afinidad de los participantes" },
        h("h3", null, "Cómo encaja con el grupo"),
        pick.group_fit.participants.map(member => h("div", { class: "group-fit-row" },
          h("span", null, memberName(member)),
          h("meter", { min: "0", max: "1", value: member.score, "aria-label": `Afinidad estimada de ${memberName(member)}` }),
          h("span", { class: "muted" }, member.basis === "sin_datos" ? "Sin perfil aún"
            : member.score >= 0.7 ? "Alta" : member.score >= 0.4 ? "Media" : "Baja"))),
        h("p", { class: "discovery-control-note" }, "Afinidad estimada según los perfiles disponibles; no garantiza que les guste.")) : null,
      pick.aspect_evidence
        ? h("details",{class:"quiz-fit-details"},h("summary",null,icon("quote",13),"Lo que dicen las reseñas"),h(
            "blockquote",
            { class: "quote", style: { marginLeft: "0", marginRight: "0" } },
            h("p", null, `“${pick.aspect_evidence}”`),
            h("cite", null, `Fragmento de una reseña del catálogo${pick.aspect_mentions ? ` · Balance sobre ${pick.aspect_mentions} menciones del aspecto` : ""}`),
          ))
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

  function planSummary() {
    return h("aside", { class: "play-plan", "aria-label": "Resumen de tu plan" },
      catalogArtwork(),
      h("div",{class:"play-plan-heading"},h("span",{class:"play-plan-symbol"},icon("gamepad",21)),h("div",null,h("p",{class:"eyebrow"},"A TU MANERA"),h("h2", null, "Tu plan de juego"))),
      h("ul", null, QUESTIONS.map(question => h("li", { class: answers[question.key] ? "chosen" : "" },
        icon(question.symbol, 17), h("div", null, h("small", null, question.label), h("strong", null, answers[question.key]?.title || "Todavía por elegir")),
        answers[question.key] && icon("check", 14)))),
      friendIds.length > 0 && h("p", { class: "play-plan-group" }, icon("user", 15), `Vos + ${friendIds.length} ${friendIds.length === 1 ? "amigo" : "amigos"}`));
  }

  function renderGroupPicker(proceed, fromResults = false) {
    requestRevision += 1;
    const previousFriends = [...friendIds], previousStrategy = groupStrategy;
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
      } }), h("span", null, h("strong", null, displayName(friend))))));
    const strategySelect = h("select", { class: "select", id: "group-strategy", onChange: event => { groupStrategy = event.target.value; } },
      h("option", { value: "balanced", selected: groupStrategy === "balanced" }, "Equilibrar los gustos de todos"),
      h("option", { value: "average", selected: groupStrategy === "average" }, "Priorizar la afinidad promedio"));
    update();
    mount(container, head("MEJOR EN COMPAÑÍA", "¿Qué jugamos hoy?", "Una buena partida empieza con un buen grupo."), progress(2),
      h("div", { class: "play-workspace" }, h("section", { class: "play-question-panel" },
        h("p", { class: "eyebrow" }, "ARMÁ TU GRUPO"), h("h2", { class: "quiz-question-title", tabindex: "-1" }, "¿Quiénes se suman?"),
        h("p", { class: "play-question-note" }, "Elegí amigos de GameTrack para combinar sus gustos. También podés seguir por tu cuenta."),
        friendsError && h("p", { class: "notice notice-warn" }, `No pudimos cargar tus amigos: ${friendsError}`),
        count, options,
        !friends.length && h("div", { class: "play-empty-group" }, icon("user", 24),
          h("p", null, "Cuando aceptes amigos en GameTrack, vas a poder invitarlos a esta selección."), h("a", { href: "#/amigos" }, "Ver mis amigos y Steam →")),
        friends.length > 0 && h("div", { class: "group-strategy-control" }, h("label", { for: "group-strategy" }, "Cómo combinamos los gustos"), strategySelect),
        h("div", { class: "play-question-actions" },
          h("button", { class: "btn btn-ghost", onClick: () => {
            if (fromResults && lastRun) { friendIds = previousFriends; groupStrategy = previousStrategy; showResults(lastRun); }
            else renderQuestion();
          } }, icon("arrowLeft", 14), fromResults ? "Cancelar" : "Atrás"),
          h("button", { class: "btn btn-primary", onClick: proceed }, "Continuar", icon("chevron", 16)))), planSummary()));
    container.querySelector(".quiz-question-title")?.focus();
  }

  function head(eyebrow, title, note) {
    return h("header", { class: "play-banner" },
      h("div",{class:"play-banner-copy"},h("p",{class:"play-eyebrow"},icon("gamepad",16),eyebrow),h("h1", null, title), note && h("p", { class: "play-banner-note" }, note)),
      h("div",{class:"play-banner-emblem","aria-hidden":"true"},icon("gamepad",44)));
  }

  function renderQuestion() {
    requestRevision += 1;
    const question = QUESTIONS[step];
    const summary = h("div", { class: "play-plan-slot" }, planSummary());
    const options = h("div", { class: "quiz-options", role: "group", "aria-labelledby": "play-question-title" });
    const proceed = () => {
      if (editing) { editing = false; excluded = []; renderResults(); }
      else if (step < QUESTIONS.length - 1) { step += 1; renderQuestion(); }
      else renderResults();
    };
    const next = h("button", { class: "btn btn-primary play-next", disabled: !answers[question.key], onClick: () => {
      if (!answers[question.key]) return;
      if (question.key === "compania" && answers.compania.company !== "solo" && isLoggedIn() && !isDeveloper()) renderGroupPicker(proceed);
      else proceed();
    } }, editing ? "Actualizar mi selección" : step === QUESTIONS.length - 1 ? "Descubrir mis juegos" : "Continuar", icon(step === QUESTIONS.length - 1 ? "sparkles" : "chevron", 17));
    question.options.forEach((option, index) => {
      const button = h("button", { type: "button", class: "quiz-option", "aria-pressed": String(answers[question.key]?.title === option.title), onClick: () => {
        answers[question.key] = option;
        if (question.key === "compania" && option.company === "solo") friendIds = [];
        for (const child of options.children) child.setAttribute("aria-pressed", String(child === button));
        next.disabled = false;
        mount(summary, planSummary());
      } }, h("span", { class: `play-option-icon tone-${index % 4}` }, optionIcon(question, option, index)),
        h("span", { class: "play-option-copy" }, h("span", { class: "quiz-option-title" }, option.title), h("span", { class: "quiz-option-note" }, option.note)),
        h("span", { class: "play-option-check", "aria-hidden": "true" }, icon("check", 12)));
      options.append(button);
    });
    mount(container, head("DESCUBRÍ TU PRÓXIMA PARTIDA", "¿Qué jugamos hoy?", "Elegí tu momento. Nosotros buscamos el juego."), progress(step),
      h("div", { class: "play-workspace" }, h("section", { class: "play-question-panel" },
        h("p", { class: "eyebrow" }, `PASO ${String(step + 1).padStart(2, "0")} / ${question.label.toLocaleUpperCase()}`),
        h("h2", { class: "quiz-question-title", id: "play-question-title", tabindex: "-1" }, question.title),
        h("p", { class: "play-question-note" }, question.note), options,
        question.help && h("details",{class:"play-method-details"},h("summary",null,icon("info",13),"Cómo usamos el tiempo"),h("p", { class: "play-method-note" }, question.help)),
        h("div", { class: "play-question-actions" },
          h("button", { class: "btn btn-ghost", onClick: () => {
            if (editing && lastRun) { editing = false; friendIds = [...lastRun.friendIds]; groupStrategy = lastRun.groupStrategy; Object.assign(answers, lastRun.answers); showResults(lastRun); }
            else if (step > 0) { step -= 1; renderQuestion(); }
            else navigate("/recomendaciones");
          } }, icon("arrowLeft", 14), editing ? "Cancelar" : step ? "Atrás" : "Volver al inicio"), next)), summary));
    if (container.isConnected) container.querySelector(".quiz-question-title")?.focus();
  }

  async function renderResults() {
    const revision = ++requestRevision;
    const isCurrentRequest = () => isCurrent() && container.isConnected && revision === requestRevision;
    mount(container, head("PREPARANDO TU SELECCIÓN", "Buscando tu próxima partida…", "Estamos combinando tus elecciones con los juegos del catálogo."), h("div", { class: "play-loading", role: "status" }, cauldronLoader(), h("p", null, "Revisamos afinidad, modos de juego y tus prioridades.")));

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
    const memberName = member => displayName(member.id === ownerId ? state.user : friends.find(friend => friend.id === member.id) || member);
    mount(
      container,
      head(
        "Tu selección para hoy",
        response.picks.length ? "Tu próxima partida está acá." : "Ajustemos el plan.",
        "Tocá una respuesta para ajustarla o pedí otras opciones con el mismo plan.",
      ),
      h("div", { class: "quiz-answer-summary", "aria-label": "Editar tus respuestas" },
        QUESTIONS.map((question, index) => h("button", { class: "chip", onClick: () => {
          step = index;
          editing = true;
          renderQuestion();
        } }, icon(question.symbol, 14), given[question.key].title, icon("chevronDown", 11)))),
      response.group ? h("section", { class: "group-summary card" },
        h("h2", null, "Una partida para todos"),
        h("p", null, response.group.members.map(memberName).join(" + ")),
        h("details", { class: "play-group-details" }, h("summary", null, "Cómo elegimos para el grupo"), h("p", { class: "discovery-control-note" }, response.group.notice))) : null,
      isLoggedIn() && !isDeveloper() && given.compania.company !== "solo" ?
        h("button", { class: "btn btn-sm", onClick: () => renderGroupPicker(() => { excluded = []; renderResults(); }, true) },
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
            { class: "play-results-grid" },
            response.picks.map((pick, index) => resultCard(pick, index, memberName)),
          )
        : h("div", { class: "play-empty-results" }, icon("search", 30), h("h2", null, "Todavía no encontramos esa combinación"), h("p", null, "Probá cambiar una respuesta arriba o empezar con otro plan.")),
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
