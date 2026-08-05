/* Inicio compacto: recomendaciones y selecciones de fichas ya completas. */

import { api } from "../api.js";
import { gameGrid, requiresLogin } from "../components.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import { emptyState, h } from "../ui.js";


function section(title, subtitle, games, href) {
  if (!games.length) return null;
  return h(
    "section",
    { style: { marginBottom: "var(--s-8)" } },
    h(
      "div",
      { class: "view-head", style: { marginBottom: "var(--s-4)" } },
      h(
        "div",
        null,
        h("h2", null, title),
        h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, subtitle),
      ),
      h("a", { class: "btn btn-sm", href }, "Ver m\u00e1s"),
    ),
    gameGrid(games),
  );
}


export async function homeView() {
  if (!isLoggedIn()) return requiresLogin("Eleg\u00ed un perfil para ver tu inicio.");
  if (isDeveloper()) {
    return emptyState(
      "Inicio para jugadores",
      "La cuenta desarrolladora tiene su informaci\u00f3n en el panel de anal\u00edtica.",
      h("a", { class: "btn btn-primary", href: "#/dev" }, "Ir al panel"),
    );
  }

  const [recommendations, popular, recent] = await Promise.all([
    api.recommendations("auto", 8),
    api.games({ sort: "popularidad", limit: 8, enriched_only: true }),
    api.games({ sort: "lanzamiento", limit: 8, enriched_only: true }),
  ]);

  const recommendedGames = recommendations.items.map((item) => item.game);
  const seen = new Set(recommendedGames.map((game) => game.id));
  const popularGames = popular.items.filter((game) => !seen.has(game.id));
  popularGames.forEach((game) => seen.add(game.id));
  const recentGames = recent.items.filter((game) => !seen.has(game.id));

  const sections = [
    section(
      "Para vos",
      recommendations.cold_start
        ? "Una selecci\u00f3n popular mientras conocemos tus gustos."
        : "Elegidos a partir de tus gustos y valoraciones.",
      recommendedGames,
      "#/recomendaciones",
    ),
    section(
      "Populares",
      "Juegos con m\u00e1s evidencia dentro de GameTrack.",
      popularGames,
      "#/catalogo?orden=popularidad",
    ),
    section(
      "Lanzamientos recientes",
      "Fichas completas ordenadas por fecha de lanzamiento.",
      recentGames,
      "#/catalogo?orden=lanzamiento",
    ),
  ].filter(Boolean);

  return h(
    "div",
    null,
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        null,
        h("p", { class: "eyebrow" }, "GameTrack"),
        h("h1", null, "Inicio"),
        h(
          "p",
          { class: "muted" },
          "Descubr\u00ed juegos o busc\u00e1 cualquier t\u00edtulo de Steam en el cat\u00e1logo.",
        ),
      ),
      h("a", { class: "btn btn-primary", href: "#/catalogo" }, "Explorar cat\u00e1logo"),
    ),
    sections.length
      ? sections
      : emptyState(
          "Todav\u00eda no hay fichas completas",
          "Sincroniz\u00e1 y enriquec\u00e9 algunos juegos de Steam para poblar el inicio.",
        ),
  );
}
