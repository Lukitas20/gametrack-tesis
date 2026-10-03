import { api } from "../api.js";
import { state, isLoggedIn, isDeveloper } from "../store.js";
import { h, icon, mount, openModal, modalHead } from "../ui.js";

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
      h("div", { class: "insight-form-footer" }, h("span", null, "Tus gustos y datos del juego. Todo local."), submit));

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
        message.lastElementChild.replaceChildren(h("span", { class: "insight-reply-label" }, icon("sparkles", 13), "IA local · evidencia"),
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
        h("div", null, h("p", { class: "insight-local" }, icon("sparkles", 13), "TU IA LOCAL"), h("h3", null, data.game_name), h("p", null, data.summary))),
      h("div", { class: "insight-columns" }, column("Podría gustarte", data.positives, "heart", "insight-fit"),
        column("Para tener en cuenta", data.cautions, "info", "insight-caution")),
      h("section", { class: "insight-consult" }, h("h3", null, "Mirá más allá del número"),
        h("p", null, "Explorá los motivos, compará con tu historial y revisá las referencias."), suggestions, thread, form),
      h("details", { class: "insight-references" }, h("summary", null, "Fuentes y referencias"),
        h("div", null, data.references.map(ref => referenceLink(ref))), h("p", { class: "insight-method" }, data.method)));
  }
  load();
  return root;
}

export function openUpcomingAnalysis(appid) {
  openModal(close => h("div", null,
    modalHead("¿Podría gustarte cuando salga?", "GameTrackScore previo al lanzamiento, con tus gustos y referencias.", close),
    !isLoggedIn() || isDeveloper() ? h("div", {class:"game-insights"},
      h("p",null,isDeveloper() ? "Esta estimación necesita una cuenta de jugador con gustos e historial personal." : "Iniciá sesión para comparar este anuncio con tus gustos e historial."),
      h("a",{class:"btn btn-primary",href:"#/cuentas",onClick:close},"Ir a mi cuenta")) : analysisContent(appid, close, true)), {wide:true});
}

export function openGameAnalysis(gameId) {
  openModal(close => h("div", null,
    modalHead("¿Este juego es para vos?", "Una explicación de tu afinidad, con referencias.", close), analysisContent(gameId, close)), { wide: true });
}

export function gameAnalysisButton(gameId) {
  return h("button", { type: "button", class: "btn btn-ghost btn-sm gts-consult-button", onClick: () => openGameAnalysis(gameId) },
    icon("sparkles", 15), "Consultarle a la IA local", icon("chevron", 13));
}
