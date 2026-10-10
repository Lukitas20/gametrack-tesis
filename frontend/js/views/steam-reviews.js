/* Reseñas de Steam: el veredicto original se conserva, sin inventar estrellas. */
import { api } from "../api.js";
import { state } from "../store.js";
import { emptyState, h, icon, initials, spinnerBlock, toast } from "../ui.js";

const number = value => new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 }).format(value);

export function steamReviewsList(reviews, { error = null, loading = false, onLoadMore } = {}) {
  if (!reviews) return error ? emptyState("No pudimos cargar tus reseñas de Steam", error) : spinnerBlock("Trayendo tus reseñas de Steam…");
  const entries = reviews.status === "private" ? [] : reviews.items || [];
  const checked = reviews.updated_at ? new Date(reviews.updated_at * 1000).toLocaleString("es-AR") : null;
  const notice = reviews.status === "private" ? "Steam no está compartiendo tus reseñas. Revisá los ajustes de privacidad de tu perfil."
    : reviews.status === "unavailable" ? checked ? `Steam no respondió. Mostramos las reseñas guardadas del ${checked}.` : "No pudimos consultar tus reseñas de Steam. Probá sincronizar nuevamente."
    : reviews.status === "not_configured" ? "La conexión con Steam todavía no está disponible." : null;
  return h("div", null,
    error && h("p", { class: "steam-notice", role: "status" }, error),
    notice && h("p", { class: "steam-notice", role: "status" }, notice,
      reviews.status === "private" && h("a", { href: "https://steamcommunity.com/my/edit/settings", target: "_blank", rel: "noopener noreferrer" }, " Revisar privacidad en Steam ↗")),
    reviews.total != null && reviews.status !== "private" && h("p", { class: "steam-result-count" }, `${entries.length} de ${number(reviews.total)} reseñas de Steam`),
    entries.length ? h("div", { class: "steam-ratings-list" }, entries.map(review => {
      const fallback = h("span", { class: "steam-game-fallback", "aria-hidden": "true" }, initials(review.name));
      const cover = review.cover ? h("img", { src: review.cover, alt: "", loading: "lazy", onError: event => event.currentTarget.replaceWith(fallback) }) : fallback;
      return h("article", { class: "steam-review-card" },
        h("div", { class: "steam-review-heading" },
          h("a", { class: "steam-review-cover", href: review.game_id ? `#/juego/${review.game_id}` : `https://store.steampowered.com/app/${review.appid}/`, target: review.game_id ? null : "_blank", rel: review.game_id ? null : "noopener noreferrer", "aria-label": `Ver ${review.name}` }, cover),
          h("div", null, h("h3", null, review.name), review.minutes != null && h("p", { class: "steam-caption" }, `${number(review.minutes / 60)} h en Steam`)),
          h("span", { class: `steam-review-verdict ${review.is_recommended ? "is-recommended" : "is-not-recommended"}` }, icon(review.is_recommended ? "check" : "x", 14), review.is_recommended ? "Recomendado" : "No recomendado")),
        h("p", { class: "steam-review-content" }, review.content),
        h("div", { class: "steam-review-footer" }, h("span", { class: "steam-caption" }, (review.posted || "").replace(/Posted /g, "Publicada ").replace(/Updated /g, "Actualizada ")),
          h("a", { href: review.url, target: "_blank", rel: "noopener noreferrer" }, "Ver reseña en Steam ↗")));
    })) : reviews.status === "ok" ? emptyState("No hay reseñas publicadas en Steam", "Cuando publiques una reseña en Steam, vas a poder consultarla acá.") : null,
    reviews.next_page && reviews.status !== "private" && onLoadMore && h("button", { class: "btn btn-sm steam-more", disabled: loading, onClick: onLoadMore }, loading ? "Cargando…" : "Ver más reseñas"));
}

export function steamReviewsPanel(user) {
  const body = h("div");
  const refresh = h("button", { class: "btn btn-sm", type: "button", onClick: () => load({ force: true }) }, icon("refresh", 14), "Sincronizar reseñas");
  const root = h("section", { "aria-label": "Tus reseñas de Steam" },
    h("div", { class: "steam-section-head" }, h("div", null, h("h2", null, "Tus reseñas de Steam"), h("p", { class: "muted" }, "Lo que recomendaste y lo que no, con tus propias palabras.")), refresh), body);
  let reviews = null, error = null, loading = false;
  const currentUser = () => state.user?.id === user.id && state.user?.steam_id === user.steam_id && state.user?.steam_verified;
  const render = () => body.replaceChildren(steamReviewsList(reviews, { error, loading, onLoadMore: () => load({ page: reviews.next_page }) }));
  async function load({ force = false, page = 1 } = {}) {
    if (loading || !currentUser()) return;
    if (force && reviews?.next_refresh_at > Date.now() / 1000) {
      toast(`Podés volver a sincronizar en ${Math.ceil(reviews.next_refresh_at - Date.now() / 1000)} segundos.`);
      return;
    }
    loading = true; refresh.disabled = true; render();
    try {
      const next = await (force ? api.syncSteamReviews() : api.steamReviews(page));
      if (!currentUser()) return;
      if (page > 1) {
        if (next.status !== "ok") throw new Error("No pudimos cargar más reseñas. Probá nuevamente.");
        const items = new Map(reviews.items.map(review => [review.appid, review]));
        next.items.forEach(review => items.set(review.appid, review));
        reviews = { ...next, items: [...items.values()] };
      } else reviews = next;
      error = null;
    } catch (failure) {
      if (currentUser()) error = failure.message;
    } finally {
      if (currentUser()) { loading = false; refresh.disabled = false; render(); }
    }
  }
  load();
  return root;
}
