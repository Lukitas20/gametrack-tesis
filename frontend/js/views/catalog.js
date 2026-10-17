/* Catálogo con búsqueda y filtros. */

import { api } from "../api.js";
import { gameGrid } from "../components.js";
import { isDeveloper, loadCatalog, state } from "../store.js";
import { emptyState, h, infoDetails, magicLoader } from "../ui.js";
import { createDeveloperCatalog } from "./developer-catalog.js";

const PAGE_SIZE = 24;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function catalogView({ query }) {
  const developer = isDeveloper();
  const renderGrid = developer ? createDeveloperCatalog() : gameGrid;
  const filters = {
    search: query.get("q") || "",
    genre: query.get("genero") || "",
    tag: query.get("etiqueta") || "",
    sort: query.get("orden") || "popularidad",
    limit: PAGE_SIZE,
    offset: 0,
  };

  await loadCatalog();

  const results = h("div");
  const count = h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } });
  let loading = false;
  let total = 0;

  const syncSummary = h("p", { class: "muted", role: "status" }, "Consultando el catálogo guardado…");
  const syncDetail = h("p", { class: "discovery-control-note" });
  const syncTitle = h("strong", null, "Estado del catálogo");
  const syncNotice = h("section", { class: "catalog-sync-notice" },
    h("div", null, syncTitle, syncSummary, syncDetail),
    h("button", { class: "btn btn-sm", onClick: () => { loadSyncStatus(); reload(); } }, "Actualizar vista"));

  async function loadSyncStatus() {
    try {
      const status = await api.steamCatalogStatus();
      const scheduled = status.worker_mode === "scheduled";
      const shared = scheduled || status.worker_mode === "external";
      syncTitle.textContent = shared ? "Catálogo compartido" : "Catálogo en esta PC";
      const entries = status.entry_counts || {};
      const pending = (entries.pending || 0) + (entries.unavailable || 0);
      syncSummary.textContent = `${status.total_games.toLocaleString("es-AR")} juegos guardados · ${(entries.ready || 0).toLocaleString("es-AR")} fichas de Steam verificadas · ${pending.toLocaleString("es-AR")} pendientes o en reintento`;
      if (!status.worker_enabled) {
        syncDetail.textContent = "La actualización automática está desactivada. Se conservan los juegos ya guardados.";
      } else if (shared && !scheduled && !status.worker_running) {
        syncDetail.textContent = "El servidor no está informando actividad de sincronización. Los juegos guardados siguen disponibles.";
      } else if (!status.key_configured) {
        syncDetail.textContent = shared
          ? "La conexión con Steam está pendiente de configuración en el servidor. Podés consultar el catálogo guardado."
          : "La conexión del índice de Steam está pendiente de configuración. Podés seguir usando el catálogo local.";
      } else if (status.status === "error" || status.worker_error) {
        syncDetail.textContent = status.worker_error || "No se pudo completar la actualización. Se reintentará conservando los datos y el progreso guardado.";
      } else if (scheduled && !status.worker_running) {
        const lastRun = status.worker_finished_at
          ? ` Última tanda: ${new Date(status.worker_finished_at).toLocaleString("es-AR")}.`
          : "";
        syncDetail.textContent = `Actualizaciones programadas cada ${status.interval_minutes / 60} horas.${lastRun} Las fichas solicitadas se completan en las próximas tandas, aunque tu PC esté apagada.`;
      } else if (status.partial) {
        syncDetail.textContent = `Actualizando el índice: ${status.processed.toLocaleString("es-AR")} entradas procesadas en esta pasada. Los juegos aparecen a medida que se guardan.`;
      } else if (status.completed_at) {
        syncDetail.textContent = `Índice actualizado: ${new Date(status.completed_at).toLocaleString("es-AR")}. ${shared
          ? "Las fichas se completan en el servidor, aunque cierres la app."
          : "Las fichas se completan por tandas mientras la app está abierta."}`;
      } else {
        syncDetail.textContent = "La primera actualización del índice está pendiente. Podés consultar los juegos guardados.";
      }
    } catch {
      syncSummary.textContent = "El catálogo local sigue disponible.";
      syncDetail.textContent = "No pudimos consultar el estado de la sincronización.";
    }
  }

  loadSyncStatus();
  // El temporizador deja de programarse al salir de esta vista.
  setTimeout(async function pollSync() {
    if (!syncNotice.isConnected) return;
    await loadSyncStatus();
    if (syncNotice.isConnected) setTimeout(pollSync, 15000);
  }, 15000);

  const genreSelect = h(
    "select",
    {
      class: "select",
      "aria-label": "Filtrar por género",
      onChange: (event) => {
        filters.genre = event.target.value;
        reload();
      },
    },
    h("option", { value: "" }, "Todos los géneros"),
    state.genres.map((genre) =>
      h("option", { value: genre.slug, selected: genre.slug === filters.genre }, genre.name),
    ),
  );

  const sortSelect = h(
    "select",
    {
      class: "select",
      "aria-label": "Ordenar",
      onChange: (event) => {
        filters.sort = event.target.value;
        reload();
      },
    },
    [
      ["popularidad", "Más valorados por cantidad"],
      ["rating", "Mejor nota en GameTrack"],
      ["metacritic", "Mejor Metacritic"],
      ["lanzamiento", "Más recientes"],
      ["nombre", "Nombre (A-Z)"],
    ].map(([value, label]) =>
      h("option", { value, selected: value === filters.sort }, label),
    ),
  );

  const visibleTags = state.tags.filter(tag => tag.name && !tag.name.startsWith("#"));
  const tagSelect = h("select", { class: "select", "aria-label": "Filtrar por característica", onChange: event => {
    filters.tag = event.target.value; reload();
  } }, h("option", { value: "" }, "Todas las características"),
    filters.tag && !visibleTags.some(tag => tag.slug === filters.tag) && h("option", { value: filters.tag, selected: true }, "Característica seleccionada"),
    visibleTags.map(tag => h("option", { value: tag.slug, selected: tag.slug === filters.tag }, tag.name)));

  async function reload({ append = false } = {}) {
    if (loading) return;
    loading = true;
    // Si ya hay resultados en pantalla, un refetch los mantiene atenuados en
    // vez de reemplazarlos por un loader (sin salto). La primera carga, en
    // cambio, arranca de la nada: ahí se ve la animación completa, con un
    // mínimo de tiempo en pantalla para que no sea un parpadeo (las
    // consultas locales suelen resolver en menos de lo que dura verla).
    const isFirstLoad = !append && !results.querySelector(".game-grid");
    if (!append && isFirstLoad) results.replaceChildren(magicLoader());
    else if (!append) results.classList.add("is-loading");
    if (!append) filters.offset = 0;

    try {
      const minWait = isFirstLoad ? wait(550) : Promise.resolve();
      const [page] = await Promise.all([api.games(filters), minWait]);
      total = page.total;

      count.textContent = total
        ? `${total} juego${total === 1 ? "" : "s"}`
        : "Sin resultados";

      const grid = renderGrid(page.items);
      if (append && results.firstChild) {
        const existing = results.querySelector(".game-grid");
        for (const child of [...grid.children]) existing.appendChild(child);
      } else if (page.items.length) {
        results.replaceChildren(grid);
      } else {
        results.replaceChildren(
          emptyState(
            "No encontramos nada",
            "Probá con otro término o quitá algún filtro.",
          ),
        );
      }

      // Botón de "cargar más" mientras queden resultados.
      const loaded = filters.offset + page.items.length;
      const oldMore = results.querySelector("[data-more]");
      if (oldMore) oldMore.remove();
      if (loaded < total) {
        results.appendChild(
          h(
            "div",
            { dataset: { more: "1" }, class: "row", style: { justifyContent: "center", marginTop: "var(--s-6)" } },
            h(
              "button",
              {
                class: "btn",
                onClick: () => {
                  filters.offset = loaded;
                  reload({ append: true });
                },
              },
              `Ver más (${total - loaded} restantes)`,
            ),
          ),
        );
      }
    } finally {
      loading = false;
      results.classList.remove("is-loading");
    }
  }

  reload();

  return h(
    "div",
    null,
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, developer ? "Catálogo del desarrollador" : "Catálogo"),
        h("h1", null, developer ? "Investigar y comparar juegos" : "Explorar juegos"),
        developer && h("p", null, "Elegí un juego para analizarlo o compararlo."),
      ),
      count,
    ),
    h(
      "div",
      { class: "filter-bar catalog-filters" },
      genreSelect,
      tagSelect,
      sortSelect,
    ),
    results,
    infoDetails("Estado y actualización del catálogo", syncNotice),
  );
}
