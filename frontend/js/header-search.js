import { api } from "./api.js";
import { navigate } from "./router.js";
import { state } from "./store.js";
import { h, icon } from "./ui.js";
import { cover } from "./components.js";

// El historial queda en este navegador, separado por cuenta.
const historyKey = () => `gametrack.search.v1.${state.user?.id ?? "guest"}`;
export function createHeaderSearch(slot) {
  let timer, revision = 0, active = -1, choices = [];
  const readHistory = () => {
    try {
      const value = JSON.parse(localStorage.getItem(historyKey()) || "[]");
      return Array.isArray(value) ? value.filter(item => item && typeof item.label === "string" && item.label.length <= 160 &&
        (item.type === "query" || (item.type === "game" && Number.isSafeInteger(item.id) && item.id > 0))).slice(0, 8) : [];
    } catch { return []; }
  };
  const save = (item) => {
    const items = [item, ...readHistory().filter(old => !(old.type === item.type &&
      (item.type === "game" ? old.id === item.id : old.label.toLowerCase() === item.label.toLowerCase())))].slice(0, 8);
    try { localStorage.setItem(historyKey(), JSON.stringify(items)); } catch { /* Búsqueda disponible sin almacenamiento. */ }
  };
  const input = h("input", { type: "search", role: "combobox", placeholder: "Buscar juegos…", maxlength: "160",
    "aria-label": "Buscar juegos", "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": "quick-search-list",
    autocomplete: "off", spellcheck: "false" });
  const status = h("p", { class: "search-status", role: "status", "aria-live": "polite" });
  const heading = h("span", null, "Búsquedas recientes");
  const clear = h("button", { type: "button", class: "search-clear", onClick: () => {
    try { localStorage.removeItem(historyKey()); } catch { /* No hay historial persistente. */ }
    input.focus(); showRecent();
  } }, "Borrar historial");
  const list = h("div", { id: "quick-search-list", role: "listbox", "aria-label": "Sugerencias de juegos" });
  const panel = h("div", { class: "search-dropdown", hidden: true },
    h("div", { class: "search-dropdown-head" }, heading, clear), status, list,
    h("div", { class: "search-help", "aria-hidden": "true" }, "↑ ↓ para elegir", h("span", null, "Enter para abrir · Esc para cerrar")));
  const wrap = h("div", { class: "header-search-wrap search-combobox" }, icon("search", 17), input,
    h("kbd", { class: "search-shortcut", "aria-hidden": "true" }, "/"), panel);
  slot.replaceChildren(wrap);

  function open() { panel.hidden = false; input.setAttribute("aria-expanded", "true"); }
  function close() {
    clearTimeout(timer); revision++; panel.hidden = true; active = -1;
    input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
  }
  function select(index) {
    active = index;
    [...list.children].forEach((row, i) => row.setAttribute("aria-selected", String(i === active)));
    if (active < 0) input.removeAttribute("aria-activedescendant");
    else {
      input.setAttribute("aria-activedescendant", `quick-search-option-${active}`);
      list.children[active]?.scrollIntoView({ block: "nearest" });
    }
  }
  function render(items, message = "") {
    choices = items; active = -1; input.removeAttribute("aria-activedescendant");
    status.textContent = message;
    list.replaceChildren(...items.map((item, index) => h("button", {
      type: "button", role: "option", id: `quick-search-option-${index}`, "aria-selected": "false", tabindex: "-1",
      class: `search-result ${item.game ? "search-result-game" : ""}`, onClick: item.run,
    }, item.game ? cover(item.game) : h("span", { class: "search-result-symbol" }, icon(item.symbol || "clock", 18)),
    h("span", { class: "search-result-copy" }, h("strong", null, item.label), item.note ? h("small", null, item.note) : null), icon("chevron", 15))));
  }
  function openGame(game) {
    save({ type: "game", id: game.id, label: game.name.slice(0, 160) });
    close(); input.blur(); navigate(`/juego/${game.id}`);
  }
  function submit(query = input.value.trim()) {
    if (!query) return;
    save({ type: "query", label: query.slice(0, 160) });
    close(); input.blur(); navigate(`/catalogo?q=${encodeURIComponent(query)}`);
  }
  function showRecent() {
    revision++; clearTimeout(timer); open();
    heading.textContent = "Búsquedas recientes";
    const history = readHistory(); clear.hidden = !history.length;
    render(history.map(item => ({ label: item.label, symbol: item.type === "game" ? "dice" : "clock",
      note: item.type === "game" ? "Abrir juego" : "Volver a buscar", run: () => {
        if (item.type === "game") openGame({ id: item.id, name: item.label });
        else { input.value = item.label; if (document.activeElement === input) search(); else input.focus(); }
      } })), history.length ? "Guardadas en este navegador" : "Buscá un juego. Tus búsquedas recientes aparecerán acá.");
  }
  async function search() {
    const query = input.value.trim();
    if (!query) { showRecent(); return; }
    open(); const request = ++revision, owner = historyKey();
    heading.textContent = "Juegos"; clear.hidden = true;
    render([], "Buscando…");
    const all = { label: `Ver todos los resultados de «${query}»`, symbol: "search", run: () => submit(query) };
    try {
      const page = await api.games({ search: query, limit: 6, offset: 0, sort: "popularidad" });
      if (request !== revision || panel.hidden || owner !== historyKey()) return;
      const items = page.items || [];
      render([...items.map(game => ({ game, label: game.name,
        note: [game.released?.slice(0, 4), ...(game.genres || []).slice(0, 2).map(genre => genre.name)].filter(Boolean).join(" · "),
        run: () => openGame(game) })), all], items.length ? `${page.total ?? items.length} juegos encontrados` : "No encontramos juegos con ese nombre. Probá otra búsqueda.");
    } catch {
      if (request !== revision || panel.hidden || owner !== historyKey()) return;
      render([{ label: "Reintentar búsqueda", symbol: "search", run: search }, all], "No pudimos cargar los juegos. Volvé a intentar.");
    }
  }
  input.addEventListener("focus", () => input.value.trim() ? search() : showRecent());
  input.addEventListener("input", () => {
    clearTimeout(timer); revision++;
    if (!input.value.trim()) { showRecent(); return; }
    open(); heading.textContent = "Juegos"; clear.hidden = true; render([], "Buscando…");
    timer = setTimeout(search, 220);
  });
  input.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (panel.hidden) { input.value.trim() ? search() : showRecent(); return; }
      if (choices.length) select((active + (event.key === "ArrowDown" ? 1 : active < 0 ? 0 : -1) + choices.length) % choices.length);
    }
    if (event.key === "Enter") { event.preventDefault(); if (!panel.hidden && active >= 0) choices[active]?.run(); else submit(); }
  });
  wrap.addEventListener("focusout", event => { if (!wrap.contains(event.relatedTarget)) close(); });
  document.addEventListener("pointerdown", event => { if (!wrap.contains(event.target)) close(); });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") close();
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey &&
      !event.target.closest("input,textarea,select,[contenteditable],[role=dialog]")) {
      event.preventDefault(); if (input.getClientRects().length) input.focus();
    }
  });
  return { input, close };
}
