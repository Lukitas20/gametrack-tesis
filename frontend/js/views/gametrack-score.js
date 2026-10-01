import { api } from "../api.js";
import { gameCard, saveToListButton } from "../components.js";
import { isLoggedIn, isDeveloper, state } from "../store.js";
import { h, icon } from "../ui.js";
import { gameAnalysisButton } from "./game-insights.js";

const EVIDENCE = { sin_datos: "Faltan tus gustos", inicial: "Perfil inicial", en_desarrollo: "Perfil en desarrollo", amplia: "Más historial disponible", valoracion_propia: "Según tu valoración" };

function scoreBadge(score) {
  const value = score?.value;
  return h("div", { class: "gts-badge", "aria-label": value == null ? "GameTrackScore sin datos" : `GameTrackScore ${value} de 100` },
    h("span", { class: "gts-number", style: { background: `conic-gradient(var(--brand-teal-ui) ${(value ?? 0) * 3.6}deg, var(--border) 0deg)` } }, h("b", null, value ?? "—")),
    h("span", null, h("strong", null, "GameTrackScore"), h("small", null, EVIDENCE[score?.evidence] || "Afinidad personal")));
}

function scoreExplanation(score) {
  const labels = { affinity: "Tus gustos", metacritic: "Metacritic", community: "Reseñas de Steam", reach: "Respaldo público", own_rating: "Tu valoración" };
  const breakdown = Object.entries(score.weights || {}).map(([key, weight]) => {
    const value = Math.round((score.components?.[key] ?? 0) * 100);
    return h("div", { class: "gts-component" },
      h("div", null, h("span", null, labels[key] || key), h("span", null, `${value}/100 · peso ${Math.round(weight * 100)}%`)),
      h("div", { class: "gts-component-track", "aria-hidden": "true" }, h("span", { style: { width: `${value}%` } })));
  });
  return h("details", { class: "gts-explanation" }, h("summary", null, "¿Por qué este puntaje?"),
    h("p", null, score.explanation), breakdown.length ? h("div", { class: "gts-breakdown" }, breakdown) : null,
    h("ul", null, score.reasons.map(reason => h("li", null, reason))));
}

export function personalScorePanel(gameId) {
  const panel = h("section", { class: "card gts-detail", "aria-label": "Tu GameTrackScore" });
  let revision = 0;
  panel.refresh = async () => {
    const request = ++revision, owner = state.user?.id;
    if (!isLoggedIn() || isDeveloper()) {
      panel.replaceChildren(scoreBadge(null), h("p", null, "Descubrí cuánto encaja este juego con tus gustos."),
        h("a", { class: "btn btn-sm", href: "#/cuentas" }, "Iniciar sesión"));
      return;
    }
    panel.replaceChildren(h("p", { role: "status" }, "Calculando tu GameTrackScore…"));
    try {
      const score = await api.gameTrackScore(gameId);
      if (request !== revision || owner !== state.user?.id) return;
      panel.replaceChildren(scoreBadge(score), scoreExplanation(score), gameAnalysisButton(gameId));
      if (score.value == null) panel.append(h("a", { href: "#/perfil" }, "Completar mis gustos"));
    } catch {
      if (request !== revision || owner !== state.user?.id) return;
      panel.replaceChildren(h("p", null, "No pudimos calcular tu afinidad."), h("button", { class: "btn btn-sm", onClick: panel.refresh }, "Reintentar"));
    }
  };
  panel.refresh();
  return panel;
}

export function discoverySection() {
  const root = h("section", { class: "gts-section", "aria-label": "Descubrí con GameTrackScore" });
  if (!isLoggedIn() || isDeveloper()) return root;
  let mode = "affinity", revision = 0, friendsLoaded = false;
  const modes = [["affinity", "Para vos", "heart"], ["critics", "Afinidad + crítica", "star"], ["friends", "Con un amigo", "user"]];
  const tabs = h("div", { class: "gts-tabs", role: "group", "aria-label": "Cómo descubrir juegos" });
  const body = h("div", { class: "gts-results" });
  const note = h("p", { class: "gts-note" });
  const selector = h("select", { id: "gts-friend", "aria-label": "Con quién querés jugar", onChange: () => load() },
    h("option", { value: "" }, "Elegí un amigo"));
  const friendHint = h("p", null, "Elegí a alguien con quien solés jugar.");
  const friendRow = h("div", { class: "gts-friend-row", hidden: true },
    h("label", { for: "gts-friend" }, "Tu compañero"), selector, friendHint);
  root.append(h("div", { class: "gts-heading" },
    h("div", null, h("p", { class: "eyebrow" }, "HECHO PARA VOS"), h("h2", null, "Tu próximo favorito")),
    h("a", { class: "btn btn-ghost btn-sm", href: "#/que-jugamos" }, icon("dice", 16), "Armar una partida")),
    h("p", { class: "gts-intro" }, "Tus gustos, la crítica y tus amigos. Tres formas de encontrar qué jugar."), tabs, friendRow, note, body);
  function renderTabs() {
    tabs.replaceChildren(...modes.map(([key, label, symbol]) => h("button", {
      class: "gts-tab", "aria-pressed": String(mode === key), onClick: () => { mode = key; renderTabs(); load(); },
    }, icon(symbol, 15), label)));
  }
  async function load() {
    const request = ++revision, owner = state.user?.id, currentMode = mode;
    friendRow.hidden = mode !== "friends"; note.textContent = "";
    if (mode === "friends" && !friendsLoaded) {
      body.replaceChildren(h("p", { role: "status" }, "Buscando tus amigos…"));
      try {
        const response = await api.friends();
        if (request !== revision || owner !== state.user?.id) return;
        friendsLoaded = true;
        selector.append(...response.friends.map(friend => h("option", { value: friend.id }, friend.steam_username || friend.full_name || friend.username)));
        if (!response.friends.length) friendHint.replaceChildren("Todavía no tenés amigos agregados. ", h("a", { href: "#/amigos" }, "Ir a Amigos"));
      } catch {
        if (request !== revision || owner !== state.user?.id) return;
        body.replaceChildren(h("p", null, "No pudimos cargar tus amigos."), h("button", { class: "btn", onClick: load }, "Reintentar")); return;
      }
    }
    if (mode === "friends" && !selector.value) {
      body.replaceChildren(h("div", { class: "gts-empty" }, icon("user", 28), h("h3", null, "Una partida en compañía"),
        h("p", null, "Elegí un amigo para encontrar juegos multijugador que tenga en Steam o haya valorado bien.")));
      return;
    }
    body.replaceChildren(h("div", { class: "gts-skeleton", role: "status" }, "Buscando juegos para vos…"));
    try {
      const data = await api.discovery(currentMode, currentMode === "friends" ? Number(selector.value) : null);
      if (request !== revision || owner !== state.user?.id) return;
      note.replaceChildren(data.note);
      if (!data.personal_data) note.append(h("a", { href: "#/perfil", style: { marginLeft: "8px" } }, "Completar mis gustos"));
      body.replaceChildren(data.items.length ? h("div", { class: "gts-grid" }, data.items.map(item =>
        h("article", { class: "gts-card" }, gameCard(item.game),
          h("div", { class: "gts-card-info" }, scoreBadge(item.gametrack_score),
            h("p", { class: "gts-meta" }, "Metascore ", h("strong", null, item.gametrack_score.metascore != null ? `${item.gametrack_score.metascore}/100` : "Sin dato")),
            h("p", { class: "gts-public" }, `${new Intl.NumberFormat("es-AR").format(item.gametrack_score.review_count)} reseñas públicas`),
            item.reasons.length ? h("ul", { class: "gts-reasons" }, item.reasons.map(reason => h("li", null, reason))) : null,
            scoreExplanation(item.gametrack_score), gameAnalysisButton(item.game.id), saveToListButton(item.game)))))
        : h("div", { class: "gts-empty" }, icon("search", 26), h("h3", null, "Todavía no hay coincidencias"),
          h("p", null, currentMode === "friends" ? "No encontramos juegos multijugador compartibles con los datos disponibles. Probá otro amigo o el asistente de partidas." : currentMode === "critics" ? "No hay juegos nuevos con Metascore disponible para esta selección." : "Probá otra forma de descubrir juegos.")));
    } catch (error) {
      if (request !== revision || owner !== state.user?.id) return;
      body.replaceChildren(h("p", { role: "status" }, error.message || "No pudimos cargar la selección."), h("button", { class: "btn", onClick: load }, "Reintentar"));
    }
  }
  renderTabs(); load();
  return root;
}
