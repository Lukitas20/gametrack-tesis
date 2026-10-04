/* Componentes compartidos entre vistas. */

import { api } from "./api.js";
import { navigate } from "./router.js";
import { ratingFor, setRating } from "./store.js";
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
  const count=Number(game.ratings_count || 0),score=Number(game.avg_rating);
  if(!count || !Number.isFinite(score))return h("span",{class:"game-rating-empty"},"Sin nota de la comunidad");
  const compact=new Intl.NumberFormat("es-AR",{notation:"compact",maximumFractionDigits:1}).format(count);
  const value=score.toLocaleString("es-AR",{minimumFractionDigits:1,maximumFractionDigits:1});
  return h("span",{class:"rating-inline game-community-score",title:`${count.toLocaleString("es-AR")} valoraciones públicas`,"aria-label":`Comunidad: ${value} de 5, ${count.toLocaleString("es-AR")} valoraciones`},
    icon("star",13),h("strong",null,`${value}`),h("span",{class:"game-score-scale"},"/ 5"),h("span",{class:"game-score-label"},"Comunidad"),h("small",null,`${compact} votos`));
}

export function gameCard(game, { rank = null, reason = null, source = null } = {}) {
  const own=ratingFor(game.id),art=cover(game,{rank});
  if(own!==null)art.append(h("span",{class:"game-own-rating"},icon("star",11),`Tu nota · ${own}/5`));
  const year=formatYear(game.released),meta=[year!=="—"?year:null,game.developer].filter(Boolean).join(" · ");
  return h("button",{class:"game-card clean-game-card",type:"button",onClick:()=>navigate(`/juego/${game.id}`),"aria-label":`Ver ${game.name}`},art,
    h("div",{class:"game-meta"},
      h("span",{class:"game-name",title:game.name},game.name),
      game.genres?.length?h("span",{class:"game-card-genres"},game.genres.slice(0,2).map(genre=>genre.name).join(" · ")):null,
      meta?h("span",{class:"game-sub",title:meta},meta):null,
      h("div",{class:"game-card-scores"},ratingChip(game)),
      game.is_enriched===false?h("span",{class:"game-pending-label"},icon("clock",11),"Completando ficha desde Steam"):null),
    reason?h("div",{class:"reason clean-card-reason",title:source?SOURCE_LABEL[source]:null},icon("sparkles",14),
      h("span",null,h("small",null,"POR QUÉ TE LO SUGERIMOS"),h("span",null,reason))):null);
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

let reviewBodySequence=0;
export function reviewItem(review) {
  const author=review.author_name||"Jugador",steam=review.source==="steam";
  const date=new Date(review.created_at),validDate=Number.isFinite(date.getTime());
  const content=(review.content||"").replace(/\r\n?/g,"\n").replace(/\n[ \t]*\n(?:[ \t]*\n)+/g,"\n\n").trim();
  const long=content.length>280||content.split("\n").length>5;
  const body=h("p",{class:`review-body${long?" is-collapsed":""}`,id:`review-text-${++reviewBodySequence}`},content);
  const expand=long?h("button",{class:"review-read-more",type:"button","aria-expanded":"false","aria-controls":body.id,onClick:()=>{
    const collapsed=body.classList.toggle("is-collapsed");
    expand.setAttribute("aria-expanded",String(!collapsed));
    expand.textContent=collapsed?"Leer reseña completa":"Mostrar menos";
  }},"Leer reseña completa"):null;
  const recommendation=typeof review.is_recommended==="boolean"?h("span",{class:"game-review-verdict",dataset:{recommended:String(review.is_recommended)}},
    icon(review.is_recommended?"check":"x",13),review.is_recommended?"Recomendado":"No recomendado"):null;
  return h("article",{class:"review game-real-review clean-review",dataset:{reviewId:review.id}},
    h("header",{class:"review-head"},h("span",{class:"avatar","aria-hidden":"true"},initials(author)),
      h("div",{class:"game-review-author"},h("strong",null,author),h("span",{class:"game-review-byline"},
        h("span",{class:"game-review-origin"},steam?"Reseña de Steam":"Reseña de GameTrack"),
        validDate?h("time",{datetime:date.toISOString()},date.toLocaleDateString("es-AR",{day:"numeric",month:"short",year:"numeric"})):null)),recommendation),
    review.title?h("h3",{class:"game-review-title"},review.title):null,body,expand,
    review.is_analyzed?h("details",{class:"game-review-analysis"},h("summary",null,icon("sparkles",13),"Análisis de esta reseña",icon("chevronDown",13)),
      h("div",{class:"review-analysis-content"},h("p",null,"La IA local interpreta el texto. Este análisis no reemplaza la opinión del autor."),sentimentChip(review.sentiment),
        review.aspects?.length?h("div",{class:"aspect-tags"},review.aspects.map(aspectChip)):null)):null);
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
