/* Inicio del jugador: selección personal y colecciones del catálogo. */
import { cover } from "../components.js";
import { isDeveloper, isLoggedIn } from "../store.js";
import { emptyState, h, icon } from "../ui.js";
import { startNewQuiz } from "./quiz.js";
import { discoverySection } from "./gametrack-score.js";
import { loadHomeCollections } from "./home-sections.js";

function discoveryHero() {
  return h("section", { class: "discovery-hero play-hero console-hero" },
    h("div", { class: "discovery-hero-copy" },
      h("h1", null, "¿Qué jugamos ", h("br"), h("span", null, "hoy?")),
      h("p", { class: "discovery-lead" }, "Algo para vos. O para todo el grupo."),
      h("div", { class: "discovery-actions" },
        h("button", { class: "btn btn-primary", onClick: startNewQuiz }, "Encontrar un juego", icon("chevron", 17)),
        h("a", { class: "hero-catalog-link", href: "#/catalogo" }, "Explorar catálogo")),
      ),
    h("div", { class: "console-hero-art", "aria-label": "Juegos del catálogo" }));
}

function fillHero(hero, games) {
  const tiles = games.filter(game => game.background_image).slice(0, 3);
  const art = hero.querySelector(".console-hero-art");
  art.replaceChildren(...tiles.map(game => h("a", { class: "hero-game-tile", href: `#/juego/${game.id}`, "aria-label": `Ver ${game.name}`, title: game.name },
    cover(game), h("span", { class: "hero-game-name" }, game.name))));
  hero.classList.toggle("has-art", Boolean(tiles.length));
}

export async function recommendationsView() {
  if (isLoggedIn() && isDeveloper()) {
    return h("div", null,
      h("div", { class: "view-head" }, h("h1", null, "Recomendaciones")),
      emptyState("Recomendaciones para cuentas de jugador",
        "Tu cuenta de estudio tiene acceso a los informes de desarrollador.",
        h("a", { class: "btn btn-primary", href: "#/dev" }, "Ir al estudio")));
  }
  const hero = discoveryHero();
  const sections = h("div", { class: "console-home-rows" });
  const intro = h("div", { class: "home-intro" });
  loadHomeCollections(sections, intro, home => {
    fillHero(hero, [...(home.destacados || []), ...(home.populares || [])]
      .filter((game, index, all) => all.findIndex(item => item.id === game.id) === index));
  });
  return h("div", { class: "console-home" }, hero, intro,
    isLoggedIn() ? discoverySection() : null, sections);
}
