import { h } from "../ui.js";

const number = value => Number(value || 0).toLocaleString("es-AR");
export function audiencePanel(data) {
  if (!data) return h("p", null, "No se pudo obtener la cobertura de audiencias para este informe.");
  const coverageNames = {ratings:"Valoraciones", reviews:"Reseñas propias", feedback:"Experiencias", public_lists:"Listas públicas", preferences:"Preferencias guardadas"};
  const cards = unit => data.groups.filter(group=>!group.suppressed && group.unit===unit).map(group =>
    h("article", {class:"dev-aspect-card"}, h("h3", null, group.label),
      h("strong", null, `${number(group.size)} ${group.unit} de ${number(group.denominator)}`),
      h("p", null, group.criterion), h("p", {class:"dev-caption"}, `Procedencia: ${group.source}.`)));
  return h("section", {class:"dev-audience", "aria-label":"Audiencias observadas"},
    h("h2", null, "Audiencias observadas"),
    h("p", null, `${number(data.gametrack_users)} usuarios de GameTrack con interacciones en ${number(data.games)} ${data.games===1 ? "juego analizado" : "juegos analizados"}. ${number(data.steam_reviews)} reseñas de Steam guardadas, contadas por separado.`),
    h("h3", null, "Cobertura de GameTrack"),
    data.gametrack_users>0 && h("ul", null, Object.entries(data.coverage).map(([key, n]) => h("li", null, `${coverageNames[key] || key}: ${number(n)} de ${number(data.gametrack_users)} usuarios de la muestra.`))),
    data.gametrack_users<data.minimum_group && h("p", {class:"dev-data-note"}, "Faltan interacciones suficientes de usuarios de GameTrack para publicar sus grupos."),
    cards("usuarios").length>0 && h("div",null,h("h3",null,"Usuarios de GameTrack: criterios y tamaños"),h("div",{class:"dev-aspect-grid"},cards("usuarios"))),
    h("h3", null, "Reseñas de Steam: criterios y tamaños"),
    h("div", {class:"dev-aspect-grid"}, cards("reseñas")),
    data.steam_reviews<data.minimum_group && h("p", {class:"dev-data-note"}, "No hay suficientes reseñas de Steam guardadas para publicar grupos de esa fuente."),
    !data.groups.some(group=>!group.suppressed) && h("p", {class:"dev-data-note"}, "No hay grupos con al menos cinco observaciones para publicar. Faltan datos; no se estiman tamaños."),
    h("details", {class:"dev-method",open:true}, h("summary", null, "Alcance y datos faltantes"), data.limitations.map(text=>h("p", null, text))));
}
