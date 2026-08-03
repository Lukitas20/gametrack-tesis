/* Catálogo con búsqueda y filtros. */

import { api } from "../api.js";
import { gameGrid } from "../components.js";
import { loadCatalog, state } from "../store.js";
import { emptyState, h, icon, spinnerBlock } from "../ui.js";

const PAGE_SIZE = 24;

export async function catalogView({ query }) {
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

  const searchInput = h("input", {
    class: "input",
    type: "search",
    placeholder: "Buscar por nombre o desarrollador…",
    value: filters.search,
    onInput: (event) => {
      filters.search = event.target.value;
      debounceReload();
    },
  });

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

  // Los filtros van en una sola fila por encima de todo lo que condicionan.
  const tagRow = h(
    "div",
    { class: "tag-scroll" },
    h(
      "button",
      {
        class: "chip",
        "aria-pressed": String(!filters.tag),
        onClick: () => {
          filters.tag = "";
          reload();
        },
      },
      "Todas",
    ),
    state.tags.slice(0, 22).map((tag) =>
      h(
        "button",
        {
          class: "chip",
          "aria-pressed": String(filters.tag === tag.slug),
          onClick: () => {
            filters.tag = filters.tag === tag.slug ? "" : tag.slug;
            reload();
          },
        },
        tag.name,
      ),
    ),
  );

  let timer = null;
  function debounceReload() {
    clearTimeout(timer);
    timer = setTimeout(reload, 280);
  }

  async function reload({ append = false } = {}) {
    if (loading) return;
    loading = true;
    // En un refetch se conserva el render anterior atenuado, sin salto.
    if (!append && results.firstChild) results.classList.add("is-loading");
    if (!append) filters.offset = 0;

    try {
      const page = await api.games(filters);
      total = page.total;

      count.textContent = total
        ? `${total} juego${total === 1 ? "" : "s"}`
        : "Sin resultados";

      const grid = gameGrid(page.items);
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

  results.replaceChildren(spinnerBlock());
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
        h("p", { class: "eyebrow" }, "Catálogo"),
        h("h1", null, "Explorar juegos"),
        h("p", null, "Buscá por nombre o desarrollador y filtrá por género y etiquetas."),
      ),
      count,
    ),
    h(
      "div",
      { class: "filter-bar" },
      h("div", { class: "search-wrap" }, icon("search", 15), searchInput),
      genreSelect,
      sortSelect,
    ),
    h("div", { style: { marginBottom: "var(--s-5)" } }, tagRow),
    results,
  );
}
