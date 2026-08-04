/* Componentes compartidos entre vistas. */

import { api } from "./api.js";
import { navigate } from "./router.js";
import { isDeveloper, isLoggedIn, ratingFor, setRating } from "./store.js";
import {
  ASPECT_LABEL,
  SENTIMENT_LABEL,
  SOURCE_LABEL,
  coverGradient,
  formatYear,
  h,
  icon,
  initials,
  openModal,
  modalHead,
  toast,
} from "./ui.js";

/** Portada del juego, con reserva cuando el dataset no trae imagen. */
export function cover(game, { rank = null } = {}) {
  const inner = game.background_image
    ? h("img", { src: game.background_image, alt: "", loading: "lazy" })
    : h(
        "div",
        { class: "cover-fallback", style: { background: coverGradient(game.name) } },
        initials(game.name),
      );

  return h(
    "div",
    { class: "game-cover" },
    inner,
    rank !== null && h("span", { class: "rank-badge tnum" }, rank),
  );
}

export function ratingChip(game) {
  if (!game.ratings_count) {
    return h("span", { class: "game-sub" }, "Sin valoraciones");
  }
  return h(
    "span",
    { class: "rating-inline" },
    icon("star", 12),
    game.avg_rating.toFixed(2),
    h("span", { class: "muted", style: { fontWeight: "400" } }, `· ${game.ratings_count}`),
  );
}

/**
 * Tarjeta de juego. El motivo de la recomendación se muestra siempre en la
 * tarjeta, no escondido en un tooltip.
 */
export function gameCard(game, { rank = null, reason = null, source = null } = {}) {
  const own = ratingFor(game.id);

  return h(
    "button",
    {
      class: "game-card",
      onClick: () => navigate(`/juego/${game.id}`),
      "aria-label": `Ver ${game.name}`,
    },
    cover(game, { rank }),
    h(
      "div",
      { class: "game-meta" },
      h("span", { class: "game-name" }, game.name),
      h(
        "span",
        { class: "game-sub" },
        `${formatYear(game.released)} · ${game.developer || "—"}`,
      ),
      h(
        "div",
        { class: "row", style: { gap: "var(--s-2)" } },
        ratingChip(game),
        own !== null &&
          h("span", { class: "chip chip-accent" }, icon("star", 10), `Tu nota ${own}`),
      ),
      game.genres?.length
        ? h(
            "div",
            { class: "row", style: { gap: "4px" } },
            game.genres.slice(0, 2).map((genre) => h("span", { class: "chip" }, genre.name)),
          )
        : null,
    ),
    reason &&
      h(
        "div",
        { class: "reason", title: source ? SOURCE_LABEL[source] : null },
        icon("sparkles", 12),
        h("span", null, reason),
      ),
  );
}

export function gameGrid(games, decorate = null) {
  return h(
    "div",
    { class: "game-grid" },
    games.map((game, index) => gameCard(game, decorate ? decorate(game, index) : {})),
  );
}

/* ------------------------------------------------------------------ *
 * Valoración con estrellas
 * ------------------------------------------------------------------ */

/**
 * Selector de 1 a 5 estrellas. Al elegir, guarda y avisa: el motor de
 * recomendación se invalida en el backend, así que el próximo pedido ya
 * refleja el cambio.
 */
export function starRating(gameId, { onChange = null } = {}) {
  const container = h("div", { class: "row", style: { gap: "var(--s-3)" } });

  function render() {
    const current = ratingFor(gameId);
    const stars = h("div", { class: "stars", role: "group", "aria-label": "Tu valoración" });

    for (let value = 1; value <= 5; value += 1) {
      stars.appendChild(
        h(
          "button",
          {
            dataset: { on: String(current !== null && value <= current) },
            "aria-label": `${value} de 5`,
            onClick: async () => {
              try {
                await api.rate(gameId, value);
                setRating(gameId, value);
                toast(`Valoraste con ${value} ${value === 1 ? "estrella" : "estrellas"}`);
                render();
                if (onChange) onChange(value);
              } catch (error) {
                toast(error.message, "error");
              }
            },
          },
          icon("star", 22),
        ),
      );
    }

    container.replaceChildren(
      stars,
      h(
        "span",
        { class: "muted", style: { fontSize: "var(--fs-sm)" } },
        current !== null ? `${current} / 5` : "Sin valorar",
      ),
    );
  }

  render();
  return container;
}

/* ------------------------------------------------------------------ *
 * Guardar en lista
 * ------------------------------------------------------------------ */

export function saveToListButton(game) {
  const button = h(
    "button",
    { class: "btn", onClick: open },
    icon("list", 15),
    "Guardar en lista",
  );

  async function open() {
    let lists = [];
    let contained = [];
    try {
      [lists, contained] = await Promise.all([
        api.myListsSummary(),
        api.listsContaining(game.id),
      ]);
    } catch (error) {
      toast(error.message, "error");
      return;
    }

    openModal((close) => {
      const body = h("div", { style: { display: "grid", gap: "var(--s-2)" } });

      function renderRows() {
        body.replaceChildren(
          ...lists.map((list) => {
            const inside = contained.includes(list.id);
            return h(
              "button",
              {
                class: "account-card",
                onClick: async () => {
                  try {
                    if (inside) {
                      await api.removeFromList(list.id, game.id);
                      contained = contained.filter((id) => id !== list.id);
                      toast(`Quitado de ${list.name}`);
                    } else {
                      await api.addToList(list.id, game.id);
                      contained = [...contained, list.id];
                      toast(`Agregado a ${list.name}`);
                    }
                    list.total += inside ? -1 : 1;
                    renderRows();
                  } catch (error) {
                    toast(error.message, "error");
                  }
                },
              },
              h(
                "span",
                { class: "avatar" },
                icon(inside ? "check" : "plus", 16),
              ),
              h(
                "span",
                { style: { flex: "1" } },
                h("span", { class: "account-name", style: { display: "block" } }, list.name),
                h("span", { class: "account-note" }, `${list.total} juego${list.total === 1 ? "" : "s"}`),
              ),
            );
          }),
        );
      }

      renderRows();

      const nameInput = h("input", {
        class: "input",
        placeholder: "Nombre de la nueva lista",
        maxlength: "120",
      });

      return h(
        "div",
        null,
        modalHead("Guardar en lista", game.name, close),
        body,
        h("div", { class: "menu-sep" }),
        h(
          "div",
          { class: "row", style: { gap: "var(--s-2)" } },
          nameInput,
          h(
            "button",
            {
              class: "btn btn-primary",
              onClick: async () => {
                const name = nameInput.value.trim();
                if (!name) return;
                try {
                  const created = await api.createList({ name, is_public: true });
                  await api.addToList(created.id, game.id);
                  lists = [...lists, { id: created.id, name: created.name, list_type: created.list_type, total: 1 }];
                  contained = [...contained, created.id];
                  nameInput.value = "";
                  renderRows();
                  toast(`Lista "${name}" creada`);
                } catch (error) {
                  toast(error.message, "error");
                }
              },
            },
            "Crear",
          ),
        ),
      );
    });
  }

  return button;
}

/* ------------------------------------------------------------------ *
 * Reseñas
 * ------------------------------------------------------------------ */

export function sentimentChip(sentiment) {
  if (!sentiment) return h("span", { class: "chip" }, "Sin analizar");
  return h(
    "span",
    { class: "chip chip-sent", dataset: { s: sentiment } },
    h("span", { class: "dot" }),
    SENTIMENT_LABEL[sentiment],
  );
}

export function aspectChip(aspect) {
  return h(
    "span",
    { class: "chip chip-sent", dataset: { s: aspect.sentiment }, title: aspect.evidence || "" },
    h("span", { class: "dot" }),
    ASPECT_LABEL[aspect.aspect] || aspect.aspect,
  );
}

export function reviewItem(review) {
  const authorName = review.author_name || "Usuario";
  return h(
    "article",
    { class: "review" },
    h(
      "div",
      { class: "review-head" },
      h("span", { class: "avatar" }, initials(authorName)),
      h("span", { class: "review-title" }, review.title || "Reseña"),
      h("span", { class: "muted", style: { fontSize: "var(--fs-xs)" } }, authorName),
      review.source === "steam"
        ? h("span", { class: "chip", title: "Reseña real importada de Steam" }, "Steam")
        : null,
      sentimentChip(review.sentiment),
      h("span", { class: "spacer" }),
      review.sentiment_score !== null &&
        h(
          "span",
          { class: "muted tnum", style: { fontSize: "var(--fs-xs)" } },
          `polaridad ${review.sentiment_score > 0 ? "+" : ""}${review.sentiment_score.toFixed(2)}`,
        ),
    ),
    h("p", { class: "review-body" }, review.content),
    review.aspects?.length
      ? h("div", { class: "aspect-tags" }, review.aspects.map(aspectChip))
      : null,
  );
}

/* ------------------------------------------------------------------ *
 * Aviso de sesión requerida
 * ------------------------------------------------------------------ */

export function requiresLogin(message) {
  return h(
    "div",
    { class: "notice" },
    icon("info", 16),
    h(
      "div",
      null,
      h("strong", null, "Necesitás iniciar sesión. "),
      message,
      " ",
      h("a", { href: "#/cuentas" }, "Elegir una cuenta de demostración"),
      ".",
    ),
  );
}

export function developerOnly() {
  return h(
    "div",
    { class: "notice notice-warn" },
    icon("alert", 16),
    h(
      "div",
      null,
      h("strong", null, "Sección exclusiva del rol desarrollador. "),
      "Cambiá a la cuenta ",
      h("code", null, "dev.demo"),
      " desde el menú de cuenta para ver el panel de analítica.",
    ),
  );
}
