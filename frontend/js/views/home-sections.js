import { api } from "../api.js";
import { cover, ratingChip } from "../components.js";
import { h, icon } from "../ui.js";

const number = value => new Intl.NumberFormat("es-AR").format(value);
const catalog = (params = {}) => `#/catalogo?${new URLSearchParams(params)}`;
const releaseDate = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString("es-AR", {day:"numeric",month:"short",year:"numeric"}) : "Fecha sin registrar";

function heading(kicker, title, note, href, label = "Ver todos") {
  return h("div", {class:"section-head home-section-heading"},
    h("div", null, h("p", {class:"eyebrow"}, kicker), h("h2", null, title), note && h("p", {class:"home-section-note"}, note)),
    href && h("a", {class:"btn btn-ghost btn-sm",href,target:href.startsWith("https:") ? "_blank" : null,
      rel:href.startsWith("https:") ? "noopener noreferrer" : null},label,icon("chevron",14)));
}

function gameTile(game, variant = "poster") {
  return h("a", {class:`home-game-tile home-game-${variant}`,href:`#/juego/${game.id}`,"aria-label":`Ver ${game.name}`},
    cover(game), h("div", {class:"home-tile-copy"},
      variant === "recent" && h("span", {class:"home-release-date"},icon("clock",12),releaseDate(game.released)),
      h("h3",null,game.name),
      h("p",{class:"home-game-genres"},game.genres?.slice(0,2).map(genre=>genre.name).join(" · ")),
      ratingChip(game)));
}

function railSection(title, kicker, note, games, href, variant = "poster") {
  const rail = h("div", {class:`home-rail home-rail-${variant}`,tabindex:"0","aria-label":title},games.map(game=>gameTile(game,variant)));
  const controls = h("div", {class:"home-rail-controls"},
    [[-1,"Anterior","arrowLeft"],[1,"Siguiente","chevron"]].map(([direction,label,symbol])=>h("button",{
      class:"btn btn-icon btn-ghost", "aria-label":`${label}: ${title}`, onClick:()=>rail.scrollBy({left:direction*rail.clientWidth*.85,
        behavior:document.body.classList.contains("motion-paused") || matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"})},icon(symbol,16))));
  return h("section",{class:"home-section"},heading(kicker,title,note,href),rail,controls);
}

function genresSection(collections) {
  const used = new Set();
  return h("section",{class:"home-section home-genres-section"},heading("A TU MANERA","Explorá por género",null,catalog(),"Todo el catálogo"),
    h("div",{class:"home-genre-grid"},collections.map(collection=>{
      const game=collection.games.find(game=>game.background_image && !used.has(game.id)) || collection.games.find(game=>game.background_image);
      if (game) used.add(game.id);
      return h("a",{class:`home-genre-card home-genre-${collection.key}`,href:catalog({genero:collection.slug}),"aria-label":`Explorar ${collection.title}`},
        game && h("img",{src:game.background_image,alt:"",loading:"lazy",onError:event=>event.target.remove()}),
        h("div",null,h("h3",null,collection.title),h("span",null,`${number(collection.count)} juegos`)),icon("chevron",15));
    })));
}

function rankingSection(games) {
  return h("section",{class:"home-section"},heading("LA COMUNIDAD ELIGE","Los más populares","Por cantidad de reseñas en el catálogo.",catalog({orden:"popularidad"})),
    h("ol",{class:"home-ranking"},games.slice(0,6).map((game,index)=>h("li",null,
      h("a",{class:"home-rank-card",href:`#/juego/${game.id}`},h("span",{class:"home-rank-number"},String(index+1).padStart(2,"0")),cover(game),
        h("div",{class:"home-rank-copy"},h("h3",null,game.name),h("span",null,`${number(game.ratings_count)} reseñas`)),
        h("span",{class:"home-rank-rating"},icon("star",12),game.avg_rating.toFixed(1)))))));
}

function criticsSection(games) {
  return h("section",{class:"home-section"},heading("CON EL RESPALDO DE LA CRÍTICA","Los favoritos de la crítica","Ordenados por Metascore de Metacritic.",catalog({orden:"metacritic"})),
    h("div",{class:"home-critics-grid"},games.slice(0,3).map((game,index)=>h("a",{class:`home-critic-card${index===0 ? " home-critic-featured" : ""}`,href:`#/juego/${game.id}`},
      cover(game),h("div",{class:"home-critic-copy"},h("span",{class:"home-metascore"},h("strong",null,game.metacritic),"Metascore"),
        h("h3",null,game.name),h("p",null,game.genres?.slice(0,2).map(genre=>genre.name).join(" · ")),h("span",{class:"home-card-link"},"Ver juego",icon("chevron",14)))))));
}

function upcomingCard(game, featured = false) {
  return h("a",{class:`home-upcoming-card${featured ? " featured" : ""}`,href:game.store_url,target:"_blank",rel:"noopener noreferrer","aria-label":`Ver ${game.name} en Steam`},
    h("img",{src:game.background_image,alt:"",loading:"lazy",onError:event=>event.target.remove()}),
    h("div",{class:"home-upcoming-copy"},h("span",{class:"home-upcoming-label"},icon("clock",12),game.release_label),
      h("h3",null,game.name),h("span",{class:"home-card-link"},featured ? "Ver en Steam ↗" : "Steam ↗")));
}

async function loadUpcoming(section) {
  const href="https://store.steampowered.com/explore/upcoming/";
  section.append(heading("LO QUE VIENE","Próximos lanzamientos","La próxima partida también puede estar por llegar.",href,"Explorar en Steam"),
    h("div",{class:"home-agenda-loading",role:"status"},icon("clock",18),"Consultando próximos juegos en Steam…"));
  try {
    const data=await api.upcoming();
    section.querySelector('.home-agenda-loading')?.remove();
    if (data.items?.length) {
      section.append(h("div",{class:"home-upcoming-layout"},upcomingCard(data.items[0],true),
        h("div",{class:"home-upcoming-list"},data.items.slice(1,5).map(game=>upcomingCard(game)))));
      section.append(h("p",{class:"home-source-note"},data.status === "stale" ? "Mostramos la última consulta guardada. Steam no respondió ahora." : "Fechas anunciadas en Steam · pueden cambiar.",
        data.updated_at && ` Consulta: ${new Date(data.updated_at*1000).toLocaleDateString("es-AR")}.`));
    } else section.append(h("div",{class:"home-agenda-empty"},icon("clock",22),h("p",null,"La agenda de Steam no está disponible ahora."),
      h("a",{href,target:"_blank",rel:"noopener noreferrer",class:"btn btn-sm"},"Consultar próximos lanzamientos ↗")));
  } catch {
    section.querySelector('.home-agenda-loading')?.remove();
    section.append(h("p",{class:"home-source-note"},"No pudimos consultar la agenda. Podés explorar los próximos lanzamientos en Steam."));
  }
}

export async function loadHomeCollections(container, intro, onHome) {
  const agenda=h("section",{class:"home-section home-agenda"});
  const genres=h("div");
  intro.append(agenda,genres);
  loadUpcoming(agenda);
  try {
    const home=await api.home(8);
    onHome(home);
    if (home.colecciones?.length) genres.replaceChildren(genresSection(home.colecciones));
    const sections=[];
    if (home.populares?.length) sections.push(rankingSection(home.populares));
    if (home.critica?.length) sections.push(criticsSection(home.critica));
    if (home.recientes?.length) sections.push(railSection("Lanzamientos recientes","PARA DESCUBRIR AHORA","Las últimas fechas de lanzamiento del catálogo.",home.recientes,catalog({orden:"lanzamiento"}),"recent"));
    const role=home.colecciones?.find(collection=>collection.key === "rol");
    if (role?.games.length) sections.push(railSection("Un mundo en el que perderte","UN POCO MÁS LEJOS",role.caption,role.games,catalog({genero:role.slug}),"wide"));
    const indie=home.colecciones?.find(collection=>collection.key === "indie");
    if (indie?.games.length) sections.push(railSection("Pequeños estudios, grandes ideas","EL RINCÓN INDIE",indie.caption,indie.games,catalog({genero:indie.slug})));
    container.replaceChildren(...sections);
  } catch {
    container.replaceChildren(h("p",{class:"home-source-note"},"No pudimos cargar las colecciones del catálogo."));
  }
}
