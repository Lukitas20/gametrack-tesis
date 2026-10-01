/* Panel privado: datos Steam y valoraciones GameTrack tienen fuentes distintas. */
import { api } from "../api.js";
import { state } from "../store.js";
import { h, icon, initials, spinnerBlock, emptyState, toast } from "../ui.js";
import { steamFriendsList } from "./steam-friends.js";
import { achievementProgress, steamAchievementPanel } from "./steam-achievements.js";

const number = value => new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 }).format(value);
const hours = minutes => minutes == null ? "Horas no disponibles" : minutes === 0 ? "Sin jugar" : minutes < 60 ? `${minutes} min` : `${number(minutes / 60)} h`;
const date = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleDateString("es-AR") : null;

function sectionNotice(section, label) {
  const messages = {
    private: `Steam no está compartiendo ${label}. Revisá qué información compartís desde los ajustes de privacidad de Steam.`,
    unavailable: section.updated_at ? `Steam no respondió. Estos son los últimos datos guardados, del ${date(section.updated_at)}.` : `No pudimos consultar ${label}. Podés volver a sincronizar en un momento.`,
    not_configured: "La conexión con los datos de Steam todavía no está habilitada. Podés seguir usando tu cuenta de GameTrack.",
  };
  const text = messages[section.status];
  return text ? h("div", { class: "steam-notice", role: "status" }, icon("info", 17), h("div", null, text,
    section.status === "private" && h("a", { href: "https://steamcommunity.com/my/edit/settings", target: "_blank", rel: "noopener noreferrer" }, " Revisar privacidad en Steam ↗"))) : null;
}

function cover(src, name) {
  const fallback = h("span", { class: "steam-game-fallback", "aria-hidden": "true" }, initials(name));
  if (!src) return fallback;
  const image = h("img", { src, alt: "", loading: "lazy", onError: () => image.replaceWith(fallback) });
  return image;
}

export function steamDashboard(user) {
  const root = h("section", { class: "steam-dashboard", "aria-label": "Tu actividad y amigos" });
  const body = h("div");
  const summary = h("div");
  const tabs = h("div", { class: "steam-tabs", role: "group", "aria-label": "Secciones del perfil" });
  const updateNote = h("p", { class: "steam-caption", "aria-live": "polite" });
  let data = null, ratings = null, ratingsError = null, steamError = null;
  let active = "library", search = "", filter = "all", order = "hours", shown = 12;
  let selectedAchievement = null, achievementSearch = "";
  let loading = false, revision = 0;
  const owner = user.id;
  const refresh = h("button", { class: "btn btn-sm", onClick: () => load(true) }, icon("refresh", 14), "Sincronizar Steam");
  const tabButtons = [];
  for (const [key, label, symbol] of [["library", "Biblioteca", "gamepad"], ["achievements", "Logros y progreso", "trophy"], ["ratings", "Valoraciones", "star"], ["friends", "Amigos", "user"]]) {
    const button = h("button", { type: "button", "aria-pressed": String(active === key), onClick: () => {
      active = key; shown = 12; renderBody();
      tabButtons.forEach(([id, element]) => element.setAttribute("aria-pressed", String(id === active)));
    } }, icon(symbol, 15), label);
    tabButtons.push([key, button]); tabs.append(button);
  }
  root.showSection = key => {
    if (!tabButtons.some(([id]) => id === key)) return;
    active=key;shown=12;renderBody();
    tabButtons.forEach(([id,element]) => element.setAttribute("aria-pressed",String(id === active)));
  };

  function openAchievements(game) {
    selectedAchievement=game.appid;root.showSection("achievements");
    body.scrollIntoView({block:"start",behavior:document.body.classList.contains("motion-paused") || matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
  }

  function metrics() {
    const library = data?.library;
    const available = library?.updated_at != null;
    const friendsAvailable = data?.friends.updated_at != null;
    const entries = [
      ["list", available ? number(library.items.length) : "—", "Juegos en Steam", available ? `${library.played_count} con tiempo registrado` : "Biblioteca"],
      ["clock", available && (!library.items.length || library.items.some(game => game.minutes != null)) ? number(library.total_hours) + " h" : "—", "Tiempo en Steam", library?.hours_complete === false ? "Total parcial · hay horas privadas" : "Horas registradas por Steam"],
      ["star", ratings ? number(ratings.length) : "—", "Tus valoraciones", "Puntuaciones de GameTrack"],
      ["user", friendsAvailable ? number(data.friends.items.length) : "—", "Amigos en Steam", friendsAvailable ? `${data.friends.items.filter(friend => friend.account).length} vinculados en GameTrack` : "Tu comunidad"],
    ];
    summary.replaceChildren(h("div", { class: "steam-metrics" }, entries.map(([symbol, value, label, note]) => h("article", { class: "steam-metric" },
      h("span", { class: "steam-metric-label" }, icon(symbol, 15), label), h("strong", null, value), h("span", null, note)))));
  }

  function gameCard(game) {
    return h("article", { class: "steam-game" },
      h("a", { class: "steam-game-cover", href: game.game_id ? `#/juego/${game.game_id}` : `https://store.steampowered.com/app/${game.appid}/`, target: game.game_id ? null : "_blank", rel: game.game_id ? null : "noopener noreferrer", "aria-label": `Ver ${game.name}` }, cover(game.cover, game.name)),
      h("div", { class: "steam-game-info" }, h("h3", null, game.name),
        h("p", { class: "steam-game-hours" }, icon("clock", 14), hours(game.minutes), h("span", null, "en Steam")),
        game.recent_minutes > 0 && h("p", { class: "steam-caption" }, `${hours(game.recent_minutes)} en las últimas 2 semanas`),
        game.last_played > 0 && h("p", { class: "steam-caption" }, `Última partida · ${date(game.last_played)}`),
        game.achievements?.percentage != null && achievementProgress(game.achievements,true),
        h("div", { class: "steam-game-rating" },
          h("span", { class: game.rating == null ? "muted" : "steam-own-score" }, game.rating == null ? "Sin valorar" : [icon("star", 13), `${number(game.rating)} / 5`]),
          game.game_id ? h("a", { href: `#/juego/${game.game_id}` }, game.rating == null ? "Valorar →" : "Ver tu nota →") : h("span", { class: "steam-caption" }, "Fuera del catálogo GameTrack")),
        h("button",{class:"steam-achievement-link",onClick:() => openAchievements(game)},icon("trophy",14),"Ver logros",icon("chevron",12))));
  }

  function libraryTab() {
    if (!data) return loading ? spinnerBlock("Trayendo tu biblioteca y tus amigos de Steam…") : emptyState("No pudimos consultar Steam", steamError || "Probá sincronizar de nuevo.");
    const library = data.library;
    const results = h("div", { "aria-live": "polite" });
    const query = h("input", { class: "input", type: "search", placeholder: "Buscar en tu biblioteca…", "aria-label": "Buscar en tu biblioteca", value: search, onInput: event => { search = event.target.value; shown = 12; rows(); } });
    const sort = h("select", { class: "select", "aria-label": "Ordenar biblioteca", onChange: event => { order = event.target.value; shown = 12; rows(); } },
      ...[["hours", "Más jugados"], ["name", "Nombre A–Z"], ["recent", "Última partida"], ["rating", "Tu valoración"]].map(([value, label]) => h("option", { value, selected: order === value }, label)));
    const filters = h("div", { class: "steam-filters", role: "group", "aria-label": "Filtrar biblioteca" });
    for (const [value, label] of [["all", "Todos"], ["played", "Jugados"], ["unplayed", "Sin jugar"], ["rated", "Valorados"]]) {
      const button = h("button", { type: "button", "aria-pressed": String(filter === value), onClick: () => { filter = value; shown = 12; for (const item of filters.children) item.setAttribute("aria-pressed", String(item === button)); rows(); } }, label);
      filters.append(button);
    }
    function rows() {
      const games = (library.items || []).filter(game => game.name.toLocaleLowerCase().includes(search.toLocaleLowerCase().trim()) &&
        (filter === "all" || filter === "played" && game.minutes > 0 || filter === "unplayed" && game.minutes === 0 || filter === "rated" && game.rating != null));
      games.sort((a, b) => order === "name" ? a.name.localeCompare(b.name) : order === "recent" ? (b.last_played || 0) - (a.last_played || 0) : order === "rating" ? (b.rating || 0) - (a.rating || 0) : (b.minutes || 0) - (a.minutes || 0));
      results.replaceChildren(h("p", { class: "steam-result-count" }, `${games.length} juegos${search ? " encontrados" : ""}`),
        games.length ? h("div", { class: "steam-game-grid" }, games.slice(0, shown).map(gameCard)) : emptyState(
          library.updated_at ? "No hay juegos para mostrar" : "Tu biblioteca todavía no está disponible",
          search || filter !== "all" ? "Probá otra búsqueda o cambiá el filtro." : library.status === "ok" ? "Steam devolvió una biblioteca vacía para tu cuenta." : "Cuando Steam comparta los datos, aparecerán acá."));
      if (games.length > shown) results.append(h("button", { class: "btn steam-more", onClick: () => { shown += 12; rows(); } }, `Mostrar más · ${games.length - shown} restantes`));
    }
    rows();
    return h("div", null, sectionNotice(library, "tu biblioteca"),
      h("div", { class: "steam-section-head" }, h("div", null, h("h2", null, "Tu biblioteca"), h("p", { class: "muted" }, "Juegos, tiempo de juego y progreso.")),
        h("a", { class: "btn btn-sm", href: data.profile_url + "games/?tab=all", target: "_blank", rel: "noopener noreferrer" }, "Ver en Steam ↗")),
      h("div", { class: "steam-library-tools" }, query, sort), filters, results);
  }

  function achievementsTab() {
    if (!data) return loading ? spinnerBlock("Cargando tu biblioteca…") : emptyState("Steam no está disponible",steamError || "Volvé a sincronizar.");
    const games = [...data.library.items].sort((a,b) => (b.last_played || 0)-(a.last_played || 0) || (b.minutes || 0)-(a.minutes || 0));
    if (!games.length) return h("div",null,sectionNotice(data.library,"tu biblioteca"),emptyState("Tu progreso empieza acá","Cuando Steam comparta tu biblioteca, vas a poder consultar los logros de cada juego."));
    if (!games.some(game => game.appid === selectedAchievement)) selectedAchievement=games[0].appid;
    const selected=games.find(game => game.appid === selectedAchievement);
    const rows=h("div",{class:"achievement-game-list"});
    const details=h("div",{class:"achievement-game-detail"});
    const query=h("input",{class:"input",type:"search",placeholder:"Buscar un juego…","aria-label":"Buscar juego para consultar logros",value:achievementSearch,onInput:event => {achievementSearch=event.target.value;renderRows();}});
    function renderRows() {
      const found=games.filter(game => game.name.toLocaleLowerCase().includes(achievementSearch.toLocaleLowerCase().trim()));
      rows.replaceChildren(...found.slice(0,40).map(game => h("button",{class:"achievement-game-option","aria-pressed":String(game.appid === selectedAchievement),onClick:() => {
        selectedAchievement=game.appid;renderRows();renderDetail(game);
      }},cover(game.cover,game.name),h("span",null,h("strong",null,game.name),h("small",null,game.achievements?.percentage != null ? `${number(game.achievements.percentage)}% · ${game.achievements.unlocked}/${game.achievements.total} logros` : hours(game.minutes))),icon("chevron",12))));
      if (!found.length) rows.append(h("p",{class:"steam-caption"},"No hay juegos con ese nombre."));
      if (found.length > 40) rows.append(h("p",{class:"steam-caption"},"Usá el buscador para encontrar otros juegos de tu biblioteca."));
    }
    function renderDetail(game) {
      details.replaceChildren(steamAchievementPanel(game.appid,game.name,{onUpdate:progress => {
        if (state.user?.id !== owner) return;
        game.achievements=progress;renderRows();
      }}));
    }
    renderRows();renderDetail(selected);
    return h("div",null,h("div",{class:"steam-section-head"},h("div",null,h("h2",null,"Cada logro cuenta"),h("p",{class:"muted"},"Elegí un juego para ver lo que desbloqueaste y lo que te falta."))),
      h("div",{class:"achievement-workspace"},h("aside",{class:"achievement-game-picker","aria-label":"Elegir juego"},query,rows),details));
  }

  function ratingsTab() {
    if (!ratings) return ratingsError ? emptyState("No pudimos cargar tus valoraciones", ratingsError) : spinnerBlock("Cargando tus valoraciones…");
    const gamesById = new Map((data?.library.items || []).map(game => [game.game_id, game]));
    return h("div", null,
      h("div", { class: "steam-section-head" }, h("div", null, h("h2", null, "Tu criterio. Tus favoritos."), h("p", { class: "muted" }, "Tus puntuaciones guardadas en GameTrack. También podés consultar tus reseñas en Steam.")),
        data && h("a", { class: "btn btn-sm", href: data.reviews_url, target: "_blank", rel: "noopener noreferrer" }, "Mis reseñas en Steam ↗")),
      ratings.length ? h("div", { class: "steam-ratings-list" }, [...ratings].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map(row => {
        const steam = gamesById.get(row.game_id);
        return h("a", { class: "steam-rating-row", href: `#/juego/${row.game_id}` }, cover(row.game.background_image, row.game.name),
          h("div", null, h("strong", null, row.game.name), h("p", { class: "steam-caption" }, steam ? `${hours(steam.minutes)} en Steam` : `${number(row.hours_played)} h guardadas en GameTrack`)),
          h("span", { class: "steam-own-score" }, icon("star", 15), `${number(row.score)} / 5`), icon("chevron", 16));
      })) : emptyState("Tu primera valoración te espera", "Elegí un juego de tu biblioteca y contanos qué te pareció. Tus notas ayudan a recomendarte juegos según tus gustos.", h("a", { class: "btn", href: "#/catalogo" }, "Buscar un juego")));
  }

  function friendsTab() {
    if (!data) return loading ? spinnerBlock("Buscando tus amigos de Steam…") : emptyState("No pudimos consultar Steam", steamError || "Probá sincronizar de nuevo.");
    return steamFriendsList(data, { onChange: async () => {
      const updated = await api.steamProfile();
      if (state.user?.id !== owner) return;
      data = updated; metrics(); renderBody();
    } });
  }

  function renderBody() { body.replaceChildren(active === "library" ? libraryTab() : active === "achievements" ? achievementsTab() : active === "ratings" ? ratingsTab() : friendsTab()); }
  async function load(force = false) {
    if (loading) return;
    if (force && data?.next_refresh_at > Date.now() / 1000) { toast(`Podés volver a sincronizar en ${Math.ceil(data.next_refresh_at - Date.now() / 1000)} segundos.`); return; }
    const current = ++revision; loading = true; refresh.disabled = true; refresh.textContent = "Sincronizando…";
    updateNote.textContent = "Consultando la información que Steam comparte…";
    renderBody();
    const results = await Promise.allSettled([force ? api.syncSteamProfile() : api.steamProfile(), api.myRatings()]);
    if (current !== revision || state.user?.id !== owner) return;
    if (results[0].status === "fulfilled") { data = results[0].value; steamError = null; }
    else steamError = results[0].reason.message;
    if (results[1].status === "fulfilled") { ratings = results[1].value; ratingsError = null; }
    else ratingsError = results[1].reason.message;
    loading = false; refresh.disabled = false; refresh.replaceChildren(icon("refresh", 14), "Sincronizar Steam");
    updateNote.textContent = steamError || (data ? `Última consulta · ${new Date(data.checked_at * 1000).toLocaleString("es-AR")} · Datos privados de tu perfil` : "");
    metrics(); renderBody();if (data) root.onData?.(data);
  }
  root.append(h("div", { class: "steam-sync-bar" }, h("div", null, h("p", { class: "eyebrow" }, "TU ACTIVIDAD"), h("h2", null, "Mi espacio de juego")), refresh), updateNote, summary, tabs, body);
  metrics(); load();
  return root;
}
