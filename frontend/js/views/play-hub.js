import { api } from "../api.js";
import { state, isLoggedIn, isDeveloper } from "../store.js";
import { cover } from "../components.js";
import { h, icon, mount, emptyState, openModal, modalHead, toast } from "../ui.js";
import { openGameAnalysis } from "./game-insights.js";
import { steamAchievementPanel } from "./steam-achievements.js";
import { openPlayFeedback, enjoymentLabel, reasonLabel } from "./play-feedback.js";

const MODES = [
  ["hub", "Resumen", "sparkles"],
  ["biblioteca", "Mis pendientes", "list"],
  ["plan", "Buscar un juego", "gamepad"],
  ["experiencias", "Mis opiniones", "heart"],
];

export function playNavigation(mode) {
  return h("nav", { class: "play-hub-nav", "aria-label": "Formas de elegir qué jugar" },
    MODES.map(([key, label, symbol]) => h("a", {
      class: "play-hub-tab", href: key === "hub" ? "#/que-jugamos" : `#/que-jugamos?modo=${key}`,
      "aria-current": mode === key ? "page" : null,
    }, icon(symbol, 15), label)));
}

function guard() {
  return !isLoggedIn() || isDeveloper() ? h("div", { class: "card play-hub-empty" },
    h("h2", null, "Tu biblioteca y tus experiencias"),
    h("p", null, "Usá una cuenta de jugador para acceder a tus datos personales."),
    h("a", { class: "btn btn-primary", href: "#/cuentas" }, "Ir a mi cuenta")) : null;
}

function intro(kicker, title, text) {
  return h("header", { class: "play-hub-intro" }, h("p", { class: "eyebrow" }, kicker),
    h("h1", null, title), h("p", null, text));
}

function sourceLabel(item) {
  return item.source === "steam_pending" ? "En Steam y en Pendientes" :
    item.source === "steam" ? "Lo tenés en Steam" : "Guardado en Pendientes";
}

function timeLabel(item) {
  if (item.minutes == null) return null;
  if (item.minutes === 0) return "Sin jugar en Steam";
  return item.minutes < 60 ? `${item.minutes} min en Steam` : `${(item.minutes / 60).toLocaleString("es-AR", { maximumFractionDigits: 1 })} h en Steam`;
}

function scoreBadge(item, featured = false) {
  const label = item.score.value == null ? "Faltan datos de tus gustos" :
    item.score.evidence === "valoracion_propia" ? "Según tu valoración" :
    item.score.affinity == null ? "Afinidad por confirmar" :
    item.score.affinity >= 65 ? "Buena afinidad con vos" :
    item.score.affinity >= 45 ? "Afinidad moderada" : "Afinidad baja";
  return h("div", { class: featured ? "rescue-score" : "play-backlog-score",
    "aria-label": item.score.value == null ? "GameTrackScore sin datos" : `GameTrackScore ${item.score.value} de 100` },
    h("strong", null, item.score.value ?? "—", item.score.value != null ? h("span", { class: "play-score-total" }, "/100") : null),
    h("span", null, "GameTrackScore", h("small", null, label)));
}

function analysisAction(gameId) {
  return h("button", { type: "button", class: "btn btn-ghost btn-sm gts-consult-button", onClick: () => openGameAnalysis(gameId) },
    icon("sparkles", 15), "¿Por qué este juego?", icon("chevron", 13));
}

function feedbackAction(game, onSaved, label = "Ya lo probé: contar cómo me fue") {
  return h("button", { type: "button", class: "btn btn-ghost btn-sm", onClick: () => openPlayFeedback(game.id, game.name, onSaved) },
    icon("heart", 14), label);
}

function selectionReason(item) {
  const origin = item.source === "pending" ? "Lo guardaste en tu lista para jugar más adelante." :
    item.source === "steam_pending" ? "Ya lo tenés en Steam y lo guardaste en Pendientes." :
    "Ya lo tenés en Steam y registra como máximo 2 horas jugadas.";
  return origin + (item.score.value == null ? " Todavía faltan datos para estimar si te gustaría." :
    " Está primero al ordenar estos juegos por tu GameTrackScore.");
}

function achievementsButton(item) {
  return item.source !== "pending" && item.steam_app_id ? h("button", {
    class: "btn btn-ghost btn-sm", onClick: () => openModal(close => h("div", null,
      modalHead("Tus próximos logros", "Progreso de logros; no equivale a completar la historia.", close),
      steamAchievementPanel(item.steam_app_id, item.game.name)), { wide: true }),
  }, icon("trophy", 14), "Ver logros") : null;
}

function rescueSpotlight(item, onSaved) {
  const art = h("div", { class: "rescue-art", "aria-hidden": "true" }, cover(item.game));
  return h("article", { class: "play-rescue-spotlight", "aria-label": "Un juego de tus pendientes para revisar" }, art,
    h("div", { class: "rescue-topline" }, h("span", { class: "rescue-kicker" }, icon("list", 14), "DE TUS PENDIENTES"), scoreBadge(item, true)),
    h("div", { class: "rescue-copy" },
      h("div", { class: "rescue-meta" }, h("span", null, sourceLabel(item)), timeLabel(item) ? h("span", null, timeLabel(item)) : null),
      h("h2", null, item.game.name),
      h("p", null, selectionReason(item)),
      h("div", { class: "rescue-main-actions" },
        h("a", { class: "btn rescue-primary", href: `#/juego/${item.game.id}` }, "Ver detalles del juego", icon("chevron", 15)),
        analysisAction(item.game.id)),
      h("div", { class: "rescue-secondary-actions" }, feedbackAction(item.game, onSaved), achievementsButton(item))));
}

function companionTiles() {
  return h("aside", { class: "play-companion-tiles", "aria-label": "Más formas de elegir tu partida" },
    h("a", { class: "play-companion-tile play-plan-tile", href: "#/que-jugamos?modo=plan" },
      h("div", { class: "play-plan-orbits", "aria-hidden": "true" }, h("span"), h("span"), h("span")),
      h("span", { class: "play-tile-icon" }, icon("gamepad", 24)),
      h("div", null, h("small", null, "BUSCAR ALGO PARA JUGAR"), h("h2", null, "¿No sabés qué elegir?"), h("p", null, "Respondé 4 preguntas sobre tu tiempo, ánimo y compañía. Te sugerimos juegos.")),
      h("span", { class: "play-tile-link" }, "Buscar recomendaciones", icon("chevron", 14))),
    h("a", { class: "play-companion-tile play-experience-tile", href: "#/que-jugamos?modo=experiencias" },
      h("span", { class: "play-tile-icon" }, icon("heart", 22)),
      h("div", null, h("small", null, "DESPUÉS DE JUGAR"), h("h2", null, "Revisá tus opiniones"), h("p", null, "Mirá qué contaste de tus partidas y editá tus respuestas.")),
      h("span", { class: "play-tile-link" }, "Ver mis opiniones", icon("chevron", 14))));
}

function backlogCard(item, onSaved) {
  return h("article", { class: "play-backlog-card" },
    h("a", { class: "play-backlog-cover", href: `#/juego/${item.game.id}`, "aria-label": `Ver ${item.game.name}` },
      cover(item.game), timeLabel(item) ? h("span", { class: "play-time-chip" }, icon("clock", 12), timeLabel(item)) : null),
    h("div", { class: "play-backlog-copy" }, h("span", { class: "play-backlog-source" }, sourceLabel(item)),
      h("h3", null, item.game.name), scoreBadge(item),
      h("div", { class: "play-backlog-actions" }, analysisAction(item.game.id),
        feedbackAction(item.game, onSaved), achievementsButton(item))));
}

function emptyRescue(mode, onReset) {
  return h("section", { class: "play-rescue-empty" },
    h("span", { class: "play-empty-icon" }, icon("list", 32)), h("p", { class: "eyebrow" }, "MIS PENDIENTES"),
    h("h2", null, mode === "light" ? "Todavía no hay juegos para mostrar." : "No hay juegos con este filtro."),
    h("p", null, mode === "light" ? "Guardá juegos en tu lista Pendientes o conectá Steam para encontrar los que apenas jugaste." :
      "Probá ver todos tus pendientes. Para consultar juegos de Steam, tu biblioteca debe estar conectada y disponible."),
    h("div", { class: "rescue-main-actions" },
      mode === "light" ? h("a", { class: "btn btn-primary", href: "#/listas" }, "Abrir mi lista Pendientes", icon("chevron", 14)) :
        h("button", { class: "btn btn-primary", onClick: onReset }, "Ver todos mis pendientes", icon("chevron", 14)),
      h("a", { class: "btn", href: "#/perfil" }, "Mi perfil y Steam")));
}

export function backlogView({ hub = false } = {}) {
  const blocked = guard(); if (blocked) return blocked;
  const owner = state.user.id, root = h("div", { class: "play-hub" });
  const spotlight = h("div", { class: "play-rescue-stage", "aria-live": "polite" });
  const more = h("div"), meta = h("div", { class: "play-library-meta" }), context = h("div", { class: "play-library-context" });
  let mode = "light", revision = 0;
  const current = request => request === revision && owner === state.user?.id;
  const filter = h("select", { id: "play-backlog-filter", "aria-label": "Filtrar juegos pendientes", onChange: () => { mode = filter.value; load(); } },
    h("option", { value: "light" }, "Todos mis pendientes"), h("option", { value: "unplayed" }, "Sin jugar en Steam"), h("option", { value: "pending" }, "Sólo mi lista Pendientes"));
  const reload = h("button", { class: "btn btn-ghost btn-sm", onClick: load }, icon("refresh", 14), "Actualizar");
  const sync = h("button", { class: "btn btn-sm", onClick: async () => {
    sync.disabled = true; sync.textContent = "Sincronizando…";
    try { await api.syncSteamProfile(); if (owner === state.user?.id) await load(); }
    catch (error) { if (owner === state.user?.id) toast(error.message, "error"); }
    finally { sync.disabled = false; sync.textContent = "Sincronizar Steam"; }
  } }, icon("refresh", 14), "Sincronizar Steam");
  const controls = h("div", { class: "play-backlog-controls" },
    h("div", { class: "play-library-heading" }, icon("list", 18), h("h2", null, "Volvé a tus pendientes")),
    h("div", { class: "play-library-filters" }, h("label", { for: "play-backlog-filter" }, "Mostrar:"), filter, reload, state.user.steam_verified ? sync : null));
  root.append(intro("ELEGÍ TU PRÓXIMA PARTIDA", hub ? "¿Qué jugamos hoy?" : "Mis pendientes",
    hub ? "Retomá un pendiente, buscá recomendaciones o revisá cómo te fue al jugar." : "Encontrá juegos que guardaste para después o apenas probaste en Steam."),
    controls, context, spotlight, more, meta);

  async function load() {
    const request = ++revision; reload.disabled = true; spotlight.setAttribute("aria-busy", "true");
    mount(spotlight, h("div", { class: "play-rescue-loading", role: "status" },
      h("span", { class: "play-loading-orb", "aria-hidden": "true" }, icon("sparkles", 26)), "Buscando juegos entre tus pendientes…"), companionTiles());
    mount(more); mount(meta); mount(context);
    try {
      const data = await api.playBacklog(mode); if (!current(request)) return;
      const [featured, ...rest] = data.items;
      mount(spotlight, featured ? rescueSpotlight(featured, load) : emptyRescue(mode, () => { mode = "light"; filter.value = mode; load(); }), companionTiles());
      const fromSteam = data.library_status === "ok";
      mount(context, h("p", null, mode === "unplayed" ? "Mostramos juegos de Steam con 0 minutos registrados." :
        mode === "pending" ? "Mostramos sólo tu lista Pendientes." : fromSteam ? "Tu lista Pendientes + juegos de Steam con hasta 2 horas jugadas." : "Por ahora mostramos tu lista Pendientes."),
        data.library_status === "not_linked" ? h("a", { href: "#/perfil" }, "Conectar Steam", icon("chevron", 12)) : null);
      if (rest.length) mount(more,
        h("div", { class: "play-more-heading" }, h("h2", null, "Otros pendientes para revisar"), h("span", null, `${rest.length} juegos en esta selección`)),
        h("div", { class: "play-backlog-grid" }, rest.map(item => backlogCard(item, load))));
      const status = {
        not_linked: null,
        private: "Steam no permite consultar tu biblioteca por privacidad. Mostramos tus Pendientes.",
        unavailable: "No hay una biblioteca de Steam disponible en la última sincronización.",
        not_configured: "La sincronización de Steam no está configurada.",
      };
      mount(meta, h("div", { class: "play-library-status" },
        h("span", null, icon("list", 13), `${data.eligible_count} ${data.eligible_count === 1 ? "juego con este filtro" : "juegos con este filtro"}`),
        h("a", { href: "#/listas" }, "Mis listas", icon("chevron", 12))),
        featured ? h("p", { class: "play-score-help" }, "GameTrackScore (0–100) combina tus gustos y la recepción del juego. Tocá “¿Por qué este juego?” para ver los motivos.") : null,
        status[data.library_status] ? h("p", null, status[data.library_status]) : null,
        h("details", { class: "play-backlog-note" }, h("summary", null, "Cómo elegimos estos juegos", icon("chevronDown", 13)),
          h("p", null, "GameTrackScore va de 0 a 100 y combina tus gustos con la recepción del juego. No es una probabilidad de que te guste. Los juegos guardados en Pendientes pueden ser títulos que todavía no compraste."),
          h("p", null, data.note), featured ? h("p", null, featured.reason) : null,
          data.checked_at ? h("p", null, `Última sincronización: ${new Date(data.checked_at * 1000).toLocaleString("es-AR")}.`) : null,
          data.unmapped_games > 0 ? h("p", null, `${data.unmapped_games} juegos de Steam todavía no tienen una ficha utilizable en el catálogo.`) : null));
    } catch (error) {
      if (current(request)) mount(spotlight, h("div", { class: "play-rescue-empty" }, h("h2", null, "Tu biblioteca, en un momento"),
        h("p", { role: "alert" }, error.message), h("button", { class: "btn btn-primary", onClick: load }, "Reintentar")), companionTiles());
    } finally {
      if (current(request)) { reload.disabled = false; spotlight.removeAttribute("aria-busy"); }
    }
  }
  load(); return root;
}

export function experienceView() {
  const blocked = guard(); if (blocked) return blocked;
  const owner = state.user.id, root = h("div", { class: "play-hub" }), body = h("div"); let revision = 0;
  root.append(intro("DESPUÉS DE JUGAR", "Mis opiniones", "Revisá tus respuestas y editalas. Lo que te gustó o no ayuda a mejorar las próximas sugerencias."), body);
  async function load() {
    const request = ++revision; mount(body, h("p", { role: "status" }, "Leyendo tus devoluciones…"));
    try {
      const rows = await api.playExperiences(); if (request !== revision || owner !== state.user?.id) return;
      mount(body, rows.length ? h("div", { class: "play-experience-list" }, rows.map(({ game, feedback }) =>
        h("article", { class: "play-experience-row" }, cover(game), h("div", null, h("h2", null, game.name),
          h("p", null, feedback.played ? enjoymentLabel[feedback.enjoyment] : "No llegué a jugar", " · ", reasonLabel[feedback.reason]),
          h("small", null, `${new Date(feedback.updated_at).toLocaleDateString("es-AR")}${feedback.minutes != null ? ` · ${feedback.minutes} min declarados` : ""}${feedback.replay != null ? ` · ${feedback.replay ? "Volvería a jugarlo" : "No volvería a jugarlo"}` : ""}`),
          feedback.note ? h("p", { class: "play-experience-note" }, feedback.note) : null), feedbackAction(game, load, "Editar mi respuesta")))) :
        emptyState("Todavía no contaste cómo te fue", "En un pendiente, tocá “Ya lo probé: contar cómo me fue”. También podés registrar tu experiencia desde la ficha del juego o desde una recomendación."),
        h("p", { class: "play-experience-help" }, "Tus últimas 40 devoluciones. Las interrupciones se guardan como contexto; tus estrellas no se modifican."));
    } catch (error) {
      if (request === revision && owner === state.user?.id) mount(body, h("p", { role: "alert" }, error.message), h("button", { class: "btn", onClick: load }, "Reintentar"));
    }
  }
  load(); return root;
}
