/* Historial de valoraciones del usuario. */

import { api } from "../api.js";
import { gameGrid, requiresLogin } from "../components.js";
import { isDeveloper, isLoggedIn, state } from "../store.js";
import { emptyState, h, spinnerBlock } from "../ui.js";
import { steamReviewsPanel } from "./steam-reviews.js";

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

  const user = state.user;
  const localBody = h("div", null, spinnerBlock("Cargando tus notas de GameTrack…"));
  const count = h("span", { class: "chip" });
  const root = h(
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
        h("p", null, "Tus reseñas de Steam y las notas que guardaste en GameTrack."),
      ),
    ),
    user.steam_verified && user.steam_id ? steamReviewsPanel(user) : h("p", { class: "steam-notice" }, "Conectá tu cuenta de Steam para ver también tus reseñas.", h("a", { href: "#/perfil" }, " Ir a mi perfil →")),
    h("section", { "aria-label": "Tus notas en GameTrack" },
      h("div", { class: "steam-section-head steam-gametrack-ratings-head" }, h("div", null, h("h2", null, "Tus notas en GameTrack"), h("p", { class: "muted" }, "Las puntuaciones de 1 a 5 que guardaste acá.")), count), localBody),
  );
  // Cargar ambas fuentes por separado: un fallo de Steam no oculta las notas.
  api.myRatings().then(rows => {
    if (state.user?.id !== user.id) return;
    rows = [...rows].sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at));
    count.textContent = `${rows.length} juegos`;
    localBody.replaceChildren(rows.length ? gameGrid(rows.map(row => row.game)) : emptyState(
      "Todavía no guardaste notas en GameTrack",
      "Elegí un juego y guardá una puntuación de 1 a 5 para afinar tus recomendaciones.",
      h("a", { class: "btn btn-primary", href: "#/catalogo" }, "Explorar el catálogo")));
  }).catch(error => {
    if (state.user?.id === user.id) localBody.replaceChildren(emptyState("No se pudieron cargar tus notas de GameTrack", error.message));
  });
  return root;
}
