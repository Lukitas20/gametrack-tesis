/* Listas del usuario. */

import { api } from "../api.js";
import { gameGrid, requiresLogin } from "../components.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import { emptyState, h, icon } from "../ui.js";

export async function listsView() {
  if (!isLoggedIn()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Mis listas")),
      requiresLogin("Las listas son de cada usuario."),
    );
  }

  if (isDeveloper()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Mis listas")),
      emptyState("Sin listas", "Las cuentas de desarrollador no manejan listas de juegos."),
    );
  }

  let lists;
  try {
    lists = await api.myLists();
  } catch (error) {
    return emptyState("No se pudieron cargar las listas", error.message);
  }

  const withGames = lists.filter((list) => list.items.length);
  const empty = lists.filter((list) => !list.items.length);

  return h(
    "div",
    null,
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, "Colecciones"),
        h("h1", null, "Mis listas"),
        h("p", null, "Favoritos, Jugando y Pendientes existen desde el registro; el resto las creás vos."),
      ),
      h("span", { class: "chip" }, `${lists.length} listas`),
    ),

    withGames.length
      ? withGames.map((list) =>
          h(
            "section",
            { class: "section", style: { marginTop: "var(--s-8)" } },
            h(
              "div",
              { class: "section-head" },
              h(
                "div",
                null,
                h(
                  "div",
                  { class: "row", style: { gap: "var(--s-3)" } },
                  h("h2", null, list.name),
                  h("span", { class: "chip" }, `${list.items.length}`),
                  list.list_type !== "personalizada"
                    ? h("span", { class: "chip chip-accent" }, "del sistema")
                    : null,
                ),
                list.description &&
                  h(
                    "p",
                    { class: "muted", style: { fontSize: "var(--fs-sm)", marginTop: "2px" } },
                    list.description,
                  ),
              ),
            ),
            gameGrid(list.items.map((item) => item.game)),
          ),
        )
      : emptyState(
          "Todavía no guardaste nada",
          "Entrá a cualquier juego y usá «Guardar en lista».",
          h("a", { class: "btn btn-primary", href: "#/catalogo" }, "Explorar el catálogo"),
        ),

    empty.length
      ? h(
          "section",
          { class: "section" },
          h("p", { class: "eyebrow" }, "Listas vacías"),
          h(
            "div",
            { class: "row", style: { gap: "var(--s-2)" } },
            empty.map((list) => h("span", { class: "chip" }, icon("list", 11), list.name)),
          ),
        )
      : null,
  );
}
