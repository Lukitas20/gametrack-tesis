import { api } from "../api.js";
import { gameComparePanel } from "./game-comparison.js";
import { state, isLoggedIn, isDeveloper } from "../store.js";
import { h, icon, infoDetails, mount, openModal, modalHead } from "../ui.js";

/** Sólo el cálculo del servidor puede confirmar si el modelo aportó al puntaje. */
export function affinityLearningNote(learning) {
  if (!learning || typeof learning !== "object") return null;
  const weight = Number(learning.weight);
  const used = learning.used === true && Number.isFinite(weight) && weight > 0 && weight <= 1;
  const percent = used ? new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 }).format(weight * 100) : null;
  const trainingCount = Number(learning.training_observations);
  const trainingExamples = Number(learning.training_examples);
  const historyCount = Number(learning.history_examples);
  const details = [];
  if (learning.source === "steam_public_review_api" && Number.isFinite(trainingCount) && trainingCount > 0) {
    details.push(`Corpus de ${new Intl.NumberFormat("es-AR").format(trainingCount)} opiniones reales de Steam; sólo se entrenan casos con historial anterior comparable.`);
    if (Number.isFinite(trainingExamples) && trainingExamples > 0) {
      details.push(`${new Intl.NumberFormat("es-AR").format(trainingExamples)} casos utilizados para entrenar.`);
    }
  }
  if (Number.isFinite(historyCount) && historyCount > 0) {
    details.push(`${new Intl.NumberFormat("es-AR").format(historyCount)} señales personales reconocidas para esta estimación.`);
  }
  return h("details", { class: "affinity-learning", dataset: { used: String(used) } },
    h("summary", null, icon("sparkles", 13), used ? `IA de afinidad · aporte del ${percent}%` : "IA de afinidad · sin aporte a este puntaje"),
    h("p", null, learning.message || (used
      ? "El aprendizaje con opiniones reales complementa al motor según tu historial."
      : "Este puntaje se calcula con el motor y los datos disponibles; el modelo no aporta una predicción personal.")),
    used && h("p", null, `El ${percent}% corresponde al índice base del GameTrackScore. No es una probabilidad de que te guste.`),
    details.length ? h("p", { class: "affinity-learning-source" }, details.join(" ")) : null,
    h("p", { class: "affinity-learning-source" }, "Modelo de recomendación distinto del que analiza reseñas en el panel de desarrollador. Las horas indican interés; no se convierten en opiniones positivas."));
}

function scoreExplanation(score) {
  const labels = { affinity: "Afinidad con tus gustos", local_model: "IA de afinidad", steam_affinity: "IA de afinidad", metacritic: "Metacritic", community: "Reseñas de Steam", reach: "Respaldo público", own_rating: "Tu valoración" };
  const compatibility = score.calibration === "compatibility" && Number.isFinite(score.base_value) && Number.isFinite(score.value);
  const breakdown = Object.entries(score.weights || {}).map(([key, weight]) => {
    const value = Math.round((score.components?.[key] ?? 0) * 100);
    return h("div", { class: "gts-component" },
      h("div", null, h("span", null, labels[key] || key), h("span", null, `${value}/100 · peso ${Math.round(weight * 100)}%`)),
      h("div", { class: "gts-component-track", "aria-hidden": "true" }, h("span", { style: { width: `${value}%` } })));
  });
  return infoDetails("Desglose del puntaje",
    h("p", null, score.explanation),
    compatibility && h("p", null, `Índice base ${score.base_value.toLocaleString("es-AR", { maximumFractionDigits: 1 })}/100 → GameTrackScore ${score.value}/100. Es una escala de compatibilidad, no una probabilidad.`),
    breakdown.length ? h("div", { class: "gts-breakdown" }, breakdown) : null,
    h("ul", null, (score.reasons || []).map(reason => h("li", null, reason))));
}

function referenceLink(ref, compact = false) {
  const external = ref.url.startsWith("https://");
  return h("a", { href: ref.url, target: external ? "_blank" : null,
    rel: external ? "noopener noreferrer" : null, class: compact ? "insight-citation" : "insight-source",
    title: ref.detail }, compact ? ref.title : [h("strong", null, ref.title), h("span", null, ref.detail)],
    icon("chevron", 12));
}

function evidencePoint(point, references) {
  return h("li", null, h("p", null, point.text),
    h("div", { class: "insight-citations" }, point.reference_ids.map(id => references.find(ref => ref.id === id))
      .filter(Boolean).map(ref => referenceLink(ref, true))));
}

function analysisContent(gameId, close, upcoming = false) {
  const explain = upcoming ? api.upcomingExplanation : api.gameExplanation;
  const owner = state.user?.id;
  const root = h("div", { class: "game-insights", onClick: event => {
    if (event.target.closest("a")?.getAttribute("href")?.startsWith("#/")) close();
  } }, h("p", { role: "status" }, "Leyendo tu perfil y las referencias…"));
  let busy = false, revision = 0;
  const current = () => owner === state.user?.id && root.isConnected;

  async function load() {
    const version = ++revision;
    try {
      const data = await explain(gameId);
      if (version !== revision || !current()) return;
      render(data);
    } catch (error) {
      if (version !== revision || !current()) return;
      root.replaceChildren(h("p", { role: "alert" }, error.message || "No pudimos analizar este juego."),
        h("button", { class: "btn btn-sm", onClick: load }, "Reintentar"));
    }
  }

  function render(data) {
    const thread = h("div", { class: "insight-thread", role: "log", "aria-label": "Consultas sobre mi afinidad", "aria-live": "polite" });
    const inputId = `insight-question-${gameId}`;
    const input = h("textarea", { id: inputId, rows: "2", maxlength: "500", required: true,
      placeholder: "Ej.: ¿En qué se parece a los juegos que valoré bien?" });
    const submit = h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Consultar", icon("chevron", 14));
    const suggestions = h("div", { class: "insight-prompts", "aria-label": "Preguntas sugeridas" },
      data.suggested_questions.map(question => h("button", { type: "button", onClick: () => ask(question) }, question)));
    const form = h("form", { class: "insight-form", onSubmit: event => {
      event.preventDefault();
      if (input.value.trim()) ask(input.value.trim());
    } }, h("label", { for: inputId }, "Preguntá sobre este juego"), input,
      h("div", { class: "insight-form-footer" }, submit));

    function setBusy(value) {
      busy = value; submit.disabled = value; input.disabled = value;
      suggestions.querySelectorAll("button").forEach(button => { button.disabled = value; });
      submit.replaceChildren(value ? "Analizando…" : "Consultar", icon("chevron", 14));
    }

    async function ask(question) {
      if (busy || !current()) return;
      const version = ++revision;
      const message = h("article", { class: "insight-message" },
        h("p", { class: "insight-user-question" }, question),
        h("div", { class: "insight-reply", role: "status" }, "Buscando evidencia en tu perfil…"));
      thread.append(message);
      // Sólo conservamos las últimas consultas en la interfaz, no en la base.
      while (thread.children.length > 4) thread.firstElementChild.remove();
      input.value = ""; setBusy(true);
      message.scrollIntoView({ block: "nearest" });
      try {
        const response = await explain(gameId, question);
        if (version !== revision || !current()) return;
        message.lastElementChild.replaceChildren(h("span", { class: "insight-reply-label" }, icon("sparkles", 13), "Análisis local · evidencia"),
          h("ul", { class: "insight-points" }, response.answer.map(point => evidencePoint(point, response.references))));
      } catch (error) {
        if (version !== revision || !current()) return;
        message.lastElementChild.replaceChildren(h("p", { role: "alert" }, error.message || "No pudimos responder. Volvé a intentar tu consulta."));
        input.value = question;
      } finally {
        if (version === revision && current()) setBusy(false);
      }
    }

    function column(title, points, symbol, className) {
      return h("section", { class: `insight-column ${className}` }, h("h3", null, icon(symbol, 16), title),
        points.length ? h("ul", { class: "insight-points" }, points.slice(0, 2).map(point => evidencePoint(point, data.references)))
          : h("p", null, "Todavía no hay evidencia suficiente para señalar motivos personales a favor."),
        points.length > 2 ? h("details", null, h("summary", null, `Ver ${points.length - 2} motivos más`),
          h("ul", { class: "insight-points" }, points.slice(2).map(point => evidencePoint(point, data.references)))) : null);
    }

    mount(root,
      data.score.preliminary && h("p", { class: "prelaunch-notice" }, icon("clock",14), "PREVIO AL LANZAMIENTO · Estimación provisional"),
      h("div", { class: "insight-overview" }, h("div", { class: "insight-score", "aria-label": `GameTrackScore ${data.score.value ?? 'sin datos'}` },
        h("strong", null, data.score.value ?? "—"), h("span", null, "GameTrackScore")),
        h("div", null, h("p", { class: "insight-local" }, icon("sparkles", 13), "TU AFINIDAD, EXPLICADA"), h("h3", null, data.game_name), h("p", null, data.summary))),
      affinityLearningNote(data.score.learning),
      h("div", { class: "insight-columns" }, column("Podría gustarte", data.positives, "heart", "insight-fit"),
        column("Para tener en cuenta", data.cautions, "info", "insight-caution")),
      h("p",{class:"insight-confidence",dataset:{confidence:data.score.confidence||"low"}},icon("info",14),data.score.confidence_message||"Estimación provisional con los datos disponibles."),
      scoreExplanation(data.score),
      !upcoming && gameComparePanel(gameId,data.game_name),
      h("section", { class: "insight-consult" }, h("h3", null, "Más preguntas"),
        infoDetails("Preguntas sugeridas", suggestions), thread, form),
      h("details", { class: "insight-references" }, h("summary", null, "Fuentes y referencias"),
        h("div", null, data.references.map(ref => referenceLink(ref))), h("p", { class: "insight-method" }, data.method)));
  }
  load();
  return root;
}

export function openUpcomingAnalysis(appid) {
  openModal(close => h("div", null,
    modalHead("Afinidad antes del lanzamiento", null, close),
    !isLoggedIn() || isDeveloper() ? h("div", {class:"game-insights"},
      h("p",null,isDeveloper() ? "Esta estimación necesita una cuenta de jugador con gustos e historial personal." : "Iniciá sesión para comparar este anuncio con tus gustos e historial."),
      h("a",{class:"btn btn-primary",href:"#/cuentas",onClick:close},"Ir a mi cuenta")) : analysisContent(appid, close, true)), {wide:true});
}

export function openGameAnalysis(gameId) {
  openModal(close => h("div", null,
    modalHead("Tu afinidad con este juego", null, close), analysisContent(gameId, close)), { wide: true });
}

export function gameAnalysisButton(gameId) {
  return h("button", { type: "button", class: "btn btn-ghost btn-sm gts-consult-button", onClick: () => openGameAnalysis(gameId) },
    icon("sparkles", 15), "Entender mi afinidad", icon("chevron", 13));
}
