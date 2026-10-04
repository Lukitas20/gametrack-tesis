import { api } from "../api.js";
import { state } from "../store.js";
import { h, icon, mount } from "../ui.js";

export function gameComparePanel(gameId, gameName) {
  const owner = state.user?.id;
  let revision = 0, timer;
  const search = h("input", {type:"search", placeholder:"Nombre del otro juego…", "aria-label":"Buscar otro juego para comparar", autocomplete:"off", maxlength:120});
  const choices = h("div", {class:"game-compare-choices", "aria-live":"polite"});
  const output = h("div", {class:"game-compare-output", "aria-live":"polite"});
  const root = h("details", {class:"game-compare"},
    h("summary", null, icon("sparkles",16), "Comparar con otro juego", icon("chevronDown",14)),
    h("div", {class:"game-compare-body"}, h("p",null,`¿${gameName} u otro juego? Compará qué encaja mejor con tus gustos y qué dudas hay. Todo se analiza localmente.`), search, choices, output));
  const current = version => version === revision && root.isConnected && state.user?.id === owner;
  const cover = game => game.background_image ? h("img",{src:game.background_image, alt:"", loading:"lazy"}) : icon("gamepad",24);

  function sourcePoint(point, refs) {
    return h("li",null,h("p",null,point.text),h("div",{class:"insight-citations"}, point.reference_ids.map(id=>refs.find(ref=>ref.id===id)).filter(Boolean).map(ref=>
      h("a",{class:"insight-citation",href:ref.url,target:ref.url.startsWith("https://")?"_blank":null,rel:"noopener noreferrer",title:ref.detail},ref.title))));
  }
  function comparisonCard(item, winner) {
    const {game, explanation:e} = item;
    const section = (title, points) => h("div",{class:"game-compare-reasons"},h("h5",null,title),
      points.length ? h("ul",null,points.slice(0,2).map(point=>sourcePoint(point,e.references))) : h("p",null,"No hay motivos personales suficientes con los datos actuales."));
    return h("article",{class:"game-compare-card",dataset:{preferred:winner===game.id?"true":"false"}},
      h("div",{class:"game-compare-cover"},cover(game)), h("div",{class:"game-compare-card-body"},
        winner===game.id&&h("span",{class:"game-compare-pick"},icon("check",12),"Mejor encaje actual"),
        h("h4",null,game.name),h("div",{class:"game-compare-number"},h("strong",null,e.score.value??"—"),h("span",null,"GameTrackScore")),
        h("p",{class:"game-compare-confidence"},e.score.confidence_message),
        section("A favor",e.positives),section("A tener en cuenta",e.cautions),
        h("a",{href:`#/juego/${game.id}`,class:"btn btn-ghost btn-sm"},"Ver el juego",icon("chevron",12))));
  }
  async function compare(game) {
    const version = ++revision;
    clearTimeout(timer); choices.replaceChildren(); search.value=game.name;
    mount(output,h("p",{role:"status"},"Comparando con tus datos y las referencias…"));
    try {
      const data = await api.compareGames([gameId,game.id]);
      if (!current(version)) return;
      mount(output,h("p",{class:"game-compare-conclusion"},icon("info",16),data.conclusion),
        h("div",{class:"game-compare-grid"},data.items.map(item=>comparisonCard(item,data.preferred_game_id))),
        h("details",{class:"game-compare-differences"},h("summary",null,"Comparar los datos"),h("ul",null,data.differences.map(text=>h("li",null,text)))));
    } catch(error) {
      if(current(version)) mount(output,h("p",{role:"alert"},error.message||"No pudimos comparar los juegos."),h("button",{class:"btn btn-sm",onClick:()=>compare(game)},"Reintentar"));
    }
  }
  search.addEventListener("input",()=>{
    clearTimeout(timer);
    const version=++revision, query=search.value.trim();
    choices.replaceChildren(); output.replaceChildren();
    if(query.length<2) return;
    mount(choices,h("p",{role:"status"},"Buscando juegos…"));
    timer=setTimeout(async()=>{
      if(!current(version)) return;
      try {
        const page=await api.games({search:query,limit:6,sort:"popularidad"});
        if(!current(version)) return;
        const games=page.items.filter(game=>game.id!==gameId).slice(0,6);
        mount(choices,games.length?games.map(game=>h("button",{type:"button",class:"game-compare-choice",onClick:()=>compare(game)},cover(game),h("span",null,game.name),icon("chevron",14))):h("p",{role:"status"},"No encontramos otro juego con ese nombre."));
      } catch(error) {
        if(current(version)) mount(choices,h("p",{role:"alert"},error.message||"No pudimos buscar juegos."));
      }
    },350);
  });
  return root;
}
