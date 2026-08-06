/* Historial de valoraciones del usuario. */

import { api } from "../api.js";
import { gameGrid, requiresLogin } from "../components.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import { emptyState, h } from "../ui.js";

export async function ratingsView() {
  if (!isLoggedIn()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Mis valoraciones")),
      requiresLogin("Tu historial de valoraciones es privado."),
    );
  }

  if (isDeveloper()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Mis valoraciones")),
      emptyState("Sin valoraciones", "Las cuentas de desarrollador no valoran juegos."),
    );
  }

  let rows;
  try {
    rows = await api.myRatings();
  } catch (error) {
    return emptyState("No se pudo cargar el historial", error.message);
  }

  rows = [...rows].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  return h(
    "div",
    null,
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, "Historial"),
        h("h1", null, "Mis valoraciones"),
        h("p", null, "Todo lo que puntuaste, de lo más reciente a lo más viejo."),
      ),
      h("span", { class: "chip" }, `${rows.length} juegos`),
    ),

    rows.length
      ? gameGrid(rows.map((row) => row.game))
      : emptyState(
          "Todavía no valoraste nada",
          "Puntuar juegos es lo que hace que el recomendador deje de mostrarte popularidad y empiece a mostrarte algo hecho a tu medida.",
          h("a", { class: "btn btn-primary", href: "#/catalogo" }, "Explorar el catálogo"),
        ),
  );
}
