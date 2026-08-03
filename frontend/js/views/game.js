/* Detalle de juego: valorar, guardar en lista y publicar reseña. */

import { api } from "../api.js";
import {
  aspectChip,
  cover,
  gameGrid,
  reviewItem,
  saveToListButton,
  sentimentChip,
  starRating,
} from "../components.js";
import { isDeveloper, isLoggedIn, refreshRatings } from "../store.js";
import { emptyState, formatYear, h, icon, signed, spinnerBlock, toast } from "../ui.js";

export async function gameView({ params }) {
  const id = Number(params.id);
  const container = h("div", null, spinnerBlock("Cargando el juego…"));

  let game;
  try {
    game = await api.game(id);
  } catch (error) {
    return h("div", null, emptyState("No encontramos el juego", error.message));
  }

  const [similar, reviews] = await Promise.all([
    api.similar(id, 6).catch(() => []),
    api.reviews(id, 12).catch(() => []),
  ]);

  const reviewList = h(
    "div",
    null,
    reviews.length
      ? reviews.map(reviewItem)
      : h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, "Todavía no hay reseñas."),
  );

  container.replaceChildren(
    // --- Hero ---
    h(
      "div",
      { class: "game-hero" },
      h(
        "div",
        { class: "game-hero-bg" },
        cover(game).firstChild.cloneNode(true),
      ),
      h(
        "div",
        { class: "game-hero-content" },
        h(
          "div",
          { class: "row", style: { gap: "var(--s-2)", marginBottom: "var(--s-3)" } },
          game.genres.map((genre) => h("span", { class: "chip" }, genre.name)),
        ),
        h("h1", null, game.name),
        h(
          "div",
          { class: "row", style: { gap: "var(--s-4)", fontSize: "var(--fs-sm)" } },
          h("span", null, formatYear(game.released)),
          game.developer && h("span", null, game.developer),
          game.playtime_hours && h("span", { class: "row", style: { gap: "4px" } }, icon("clock", 13), `${game.playtime_hours} h`),
          game.metacritic && h("span", null, `Metacritic ${game.metacritic}`),
        ),
      ),
    ),

    h(
      "div",
      { class: "detail-layout" },

      // --- Columna principal ---
      h(
        "div",
        null,
        game.description &&
          h(
            "section",
            { class: "card" },
            h("h3", { class: "card-title", style: { marginBottom: "var(--s-3)" } }, "Sobre el juego"),
            h("p", { class: "secondary", style: { fontSize: "var(--fs-sm)", lineHeight: "1.7" } }, game.description),
            game.tags.length
              ? h(
                  "div",
                  { class: "row", style: { gap: "var(--s-2)", marginTop: "var(--s-4)" } },
                  game.tags.map((tag) => h("span", { class: "chip" }, tag.name)),
                )
              : null,
          ),

        reviewComposer(game, reviewList),

        h(
          "section",
          { class: "section" },
          h(
            "div",
            { class: "section-head" },
            h("h2", null, "Reseñas de la comunidad"),
            h(
              "span",
              { class: "muted", style: { fontSize: "var(--fs-sm)" } },
              `${game.reviews_count} en total · las etiquetas salen del módulo ABSA`,
            ),
          ),
          h("div", { class: "card" }, reviewList),
        ),
      ),

      // --- Columna lateral ---
      h(
        "aside",
        { class: "sticky-side" },
        h(
          "section",
          { class: "card" },
          h("p", { class: "eyebrow" }, "Nota de la comunidad"),
          h(
            "div",
            { class: "row", style: { gap: "var(--s-3)", alignItems: "baseline" } },
            h("span", { class: "hero-figure" }, game.avg_rating ? game.avg_rating.toFixed(2) : "—"),
            h("span", { class: "muted" }, "/ 5"),
          ),
          h(
            "p",
            { class: "stat-note", style: { marginTop: "var(--s-2)" } },
            `${game.ratings_count} valoraciones · ${game.reviews_count} reseñas`,
          ),
          h("div", { class: "menu-sep" }),
          isLoggedIn() && !isDeveloper()
            ? h(
                "div",
                null,
                h("p", { class: "label", style: { marginBottom: "var(--s-2)" } }, "Tu valoración"),
                starRating(game.id, { onChange: () => refreshRatings() }),
                h(
                  "div",
                  { class: "row", style: { marginTop: "var(--s-4)" } },
                  saveToListButton(game),
                ),
              )
            : h(
                "p",
                { class: "muted", style: { fontSize: "var(--fs-sm)" } },
                isDeveloper()
                  ? "La cuenta actual es de desarrollador: no valora juegos."
                  : "Iniciá sesión para valorar y guardar en listas.",
              ),
        ),

        game.platforms?.length
          ? h(
              "section",
              { class: "card" },
              h("p", { class: "eyebrow" }, "Plataformas"),
              h(
                "div",
                { class: "row", style: { gap: "var(--s-2)" } },
                game.platforms.map((platform) => h("span", { class: "chip" }, platform)),
              ),
            )
          : null,

        isDeveloper()
          ? h(
              "a",
              { class: "btn btn-primary", href: `#/dev/juego/${game.id}` },
              icon("chart", 15),
              "Ver analítica de este juego",
            )
          : null,
      ),
    ),

    // --- Similares ---
    similar.length
      ? h(
          "section",
          { class: "section" },
          h(
            "div",
            { class: "section-head" },
            h("h2", null, "Juegos parecidos"),
            h(
              "span",
              { class: "muted", style: { fontSize: "var(--fs-sm)" } },
              "Por similitud de contenido (TF-IDF + coseno)",
            ),
          ),
          gameGrid(
            similar.map((item) => item.game),
            (_g, index) => ({ reason: `Similitud ${similar[index].score.toFixed(3)}` }),
          ),
        )
      : null,
  );

  return container;
}

/* ------------------------------------------------------------------ *
 * Composición de reseña con análisis en vivo
 * ------------------------------------------------------------------ */

/**
 * El textarea consulta `/reviews/analyze` mientras se escribe: ese endpoint no
 * guarda nada, así que se puede mostrar el resultado del ABSA antes de
 * publicar. Al publicar, la reseña se analiza en el backend y se muestra lo
 * que quedó persistido.
 */
function reviewComposer(game, reviewList) {
  if (!isLoggedIn() || isDeveloper()) return null;

  const title = h("input", { class: "input", placeholder: "Título (opcional)", maxlength: "200" });
  const content = h("textarea", {
    class: "textarea",
    placeholder:
      "Contá qué te pareció. Mencioná jugabilidad, gráficos, historia u optimización y el análisis los detecta por separado…",
  });

  const preview = h("div");
  const publishButton = h(
    "button",
    { class: "btn btn-primary", disabled: true, onClick: publish },
    icon("sparkles", 15),
    "Publicar reseña",
  );

  let timer = null;
  content.addEventListener("input", () => {
    publishButton.disabled = content.value.trim().length < 10;
    clearTimeout(timer);
    timer = setTimeout(analyze, 450);
  });

  async function analyze() {
    const text = content.value.trim();
    if (text.length < 10) {
      preview.replaceChildren();
      return;
    }
    try {
      const analysis = await api.analyzeText(text);
      preview.replaceChildren(analysisCard(analysis, "Vista previa del análisis"));
    } catch {
      preview.replaceChildren();
    }
  }

  async function publish() {
    publishButton.disabled = true;
    try {
      const review = await api.publishReview({
        game_id: game.id,
        title: title.value.trim() || null,
        content: content.value.trim(),
        is_recommended: null,
      });
      toast("Reseña publicada y analizada");
      preview.replaceChildren(
        analysisCard(
          {
            sentiment: review.sentiment,
            score: review.sentiment_score,
            confidence: review.sentiment_confidence,
            aspects: review.aspects,
          },
          "Resultado guardado del análisis",
        ),
      );
      // La reseña nueva se antepone sin recargar la vista completa.
      reviewList.prepend(reviewItem(review));
      title.value = "";
      content.value = "";
    } catch (error) {
      toast(error.message, "error");
      publishButton.disabled = false;
    }
  }

  return h(
    "section",
    { class: "card", style: { marginTop: "var(--s-4)" } },
    h(
      "div",
      { class: "card-head" },
      h(
        "div",
        null,
        h("h3", { class: "card-title" }, "Escribir una reseña"),
        h("p", { class: "card-sub" }, "El análisis de sentimiento y aspectos se actualiza mientras escribís."),
      ),
    ),
    h("div", { style: { display: "grid", gap: "var(--s-3)" } }, title, content),
    preview,
    h(
      "div",
      { class: "row", style: { marginTop: "var(--s-4)", justifyContent: "flex-end" } },
      publishButton,
    ),
  );
}

/** Tarjeta con el resultado del módulo NLP, incluyendo la evidencia textual. */
export function analysisCard(analysis, heading) {
  const rows = (analysis.aspects || []).map((aspect) =>
    h(
      "div",
      { class: "row", style: { gap: "var(--s-3)", alignItems: "flex-start" } },
      h("span", { style: { minWidth: "116px" } }, aspectChip(aspect)),
      h(
        "span",
        { class: "muted tnum", style: { fontSize: "var(--fs-xs)", minWidth: "48px" } },
        signed(aspect.score, 2),
      ),
      aspect.evidence &&
        h(
          "span",
          { class: "secondary", style: { fontSize: "var(--fs-xs)", fontStyle: "italic", flex: "1" } },
          `“${aspect.evidence}”`,
        ),
    ),
  );

  return h(
    "div",
    {
      style: {
        marginTop: "var(--s-4)",
        padding: "var(--s-4)",
        background: "var(--surface-sunken)",
        borderRadius: "var(--r-md)",
        border: "1px solid var(--border)",
      },
    },
    h(
      "div",
      { class: "row", style: { gap: "var(--s-3)", marginBottom: "var(--s-3)" } },
      h("span", { class: "eyebrow", style: { marginBottom: "0" } }, heading),
      h("span", { class: "spacer" }),
      sentimentChip(analysis.sentiment),
      h(
        "span",
        { class: "muted tnum", style: { fontSize: "var(--fs-xs)" } },
        `polaridad ${signed(analysis.score, 2)} · confianza ${(analysis.confidence ?? 0).toFixed(2)}`,
      ),
    ),
    rows.length
      ? h("div", { style: { display: "grid", gap: "var(--s-2)" } }, rows)
      : h(
          "p",
          { class: "muted", style: { fontSize: "var(--fs-xs)" } },
          "Todavía no se detectó ninguna opinión sobre jugabilidad, gráficos, historia u optimización. Mencionar un aspecto no alcanza: hace falta opinar sobre él.",
        ),
  );
}
