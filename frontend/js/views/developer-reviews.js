import { api } from "../api.js";
import { reviewItem } from "../components.js";
import { state } from "../store.js";
import { ASPECT_LABEL, SENTIMENT_LABEL, emptyState, h, icon, mount, spinnerBlock } from "../ui.js";

export function developerReviewsPanel(scope, games = []) {
  const owner=state.user?.id, pageSize=20;
  const root=h("div",{class:"dev-review-explorer"});
  const list=h("div",{class:"dev-review-results","aria-live":"polite"});
  const status=h("p",{class:"dev-caption",role:"status"});
  const dateNote=h("p",{class:"dev-data-note"});
  let revision=0, timer, offset=0, total=0, loaded=false, busy=false;
  const search=h("input",{type:"search",placeholder:"Buscar una palabra o frase…",maxlength:300,"aria-label":"Buscar en el texto de las reseñas"});
  const select=(label, entries) => h("select",{"aria-label":label},entries.map(([value,text]) => h("option",{value},text)));
  const game=select("Juego de las reseñas",[["","Todos los juegos"],...games.map(g=>[String(g.id),g.nombre])]);
  const aspect=select("Aspecto de las reseñas",[["","Todos los aspectos"],...Object.entries(ASPECT_LABEL)]);
  const sentiment=select("Sentimiento de las reseñas",[["","Todos los sentimientos"],...Object.entries(SENTIMENT_LABEL)]);
  const source=select("Fuente de las reseñas",[["","Todas las fuentes"],["steam","Steam"],["user","GameTrack"],["seed","Demostración"]]);
  const sort=select("Orden de las reseñas",[["newest","Más recientes"],["oldest","Más antiguas"],["helpful","Más útiles"]]);
  const from=h("input",{type:"date","aria-label":"Reseñas desde",max:"9999-12-30"});
  const to=h("input",{type:"date","aria-label":"Reseñas hasta",max:"9999-12-30"});
  const sentimentLabel=h("span",null,"Sentimiento del texto");
  const field=(label,input) => h("label",{class:"dev-review-field"},label,input);
  const reset=h("button",{class:"btn",type:"button",onClick:()=>{
    for(const input of [search,game,aspect,sentiment,source,from,to]) input.value="";
    sort.value="newest";offset=0;load();
  }},icon("x",15),"Limpiar filtros");
  const prev=h("button",{class:"btn",type:"button",onClick:()=>{offset=Math.max(0,offset-pageSize);load(true);}},icon("arrowLeft",15),"Anterior");
  const next=h("button",{class:"btn",type:"button",onClick:()=>{offset+=pageSize;load(true);}},"Siguiente",icon("chevron",15));
  function sync() {prev.disabled=busy||offset===0;next.disabled=busy||offset+pageSize>=total;}
  function params() {
    return {...scope,game_id:scope.game_id||game.value||undefined,search:search.value.trim(),
      aspect:aspect.value,sentiment:sentiment.value,source:source.value,date_from:from.value,date_to:to.value,
      sort:sort.value,limit:pageSize,offset};
  }
  async function load(scrollToResults=false) {
    clearTimeout(timer);loaded=true;busy=true;sync();
    sentimentLabel.textContent=aspect.value ? "Sentimiento del aspecto" : "Sentimiento del texto";
    const version=++revision, filters=params();
    status.textContent="Buscando reseñas…";dateNote.textContent="";
    mount(list,spinnerBlock("Leyendo la muestra guardada…"));
    if(from.value&&to.value&&from.value>to.value) {
      status.textContent="La fecha inicial debe ser anterior o igual a la final.";
      mount(list,emptyState("Revisá el rango de fechas","Corregí las fechas para continuar."));busy=false;sync();return;
    }
    try {
      const result=await api.developerReviews(filters);
      if(version!==revision||state.user?.id!==owner) return;
      total=result.total;
      status.textContent=total ? `${offset+1}–${Math.min(offset+pageSize,total)} de ${total.toLocaleString("es-AR")} reseñas` : "0 reseñas coinciden con los filtros";
      mount(list,result.items.length ? result.items.map(review=>{
        const matched=filters.aspect ? review.aspects.find(a=>a.aspect===filters.aspect) : null;
        return h("section",{class:"dev-review-entry"},
          h("a",{class:"dev-review-game",href:`#/dev/juego/${review.game_id}`},icon("gamepad",16),review.game_name,icon("chevron",14)),
          matched&&h("div",{class:"dev-review-match"},h("span",{class:"dev-label"},`${ASPECT_LABEL[matched.aspect]} · ${SENTIMENT_LABEL[matched.sentiment]}`),
            matched.evidence&&h("blockquote",null,matched.evidence)),reviewItem(review));
      }) : emptyState("No encontramos reseñas con esos filtros","Probá otro texto o ampliá la selección."));
      if(result.undated) mount(dateNote,icon("info",15),`${result.undated.toLocaleString("es-AR")} reseñas coincidentes no tienen fecha original conocida y quedan fuera cuando filtrás por fecha.`);
      if(scrollToResults===true&&list.isConnected) list.scrollIntoView({block:"start",behavior:"instant"});
    } catch(error) {
      if(version!==revision||state.user?.id!==owner) return;
      total=0;status.textContent="No pudimos cargar las reseñas.";
      mount(list,emptyState("No pudimos abrir la muestra",error.message),h("button",{class:"btn",type:"button",onClick:load},"Volver a intentar"));
    } finally {if(version===revision) {busy=false;sync();}}
  }
  const filters=h("form",{class:"dev-review-filters",onSubmit:event=>{event.preventDefault();offset=0;load();}},
    h("div",{class:"dev-review-search"},h("div",{class:"dev-search-field"},icon("search",17),search),h("button",{class:"btn btn-primary",type:"submit"},"Buscar"),reset),
    h("div",{class:"dev-review-filter-grid"},!scope.game_id&&field("Juego",game),field("Aspecto",aspect),field(sentimentLabel,sentiment),field("Fuente",source),field("Desde",from),field("Hasta",to),field("Ordenar",sort)));
  for(const input of [game,aspect,sentiment,source,from,to,sort]) input.addEventListener("change",()=>{offset=0;load();});
  search.addEventListener("input",()=>{clearTimeout(timer);++revision;timer=setTimeout(()=>{offset=0;load();},300);});
  root.append(h("div",{class:"dev-section-heading"},h("div",null,h("h2",null,"Explorá las opiniones completas"),
    h("p",null,"Buscá en la muestra guardada. Al elegir un aspecto, el sentimiento se refiere a ese aspecto, no a la reseña completa."))),
    filters,h("p",{class:"dev-caption"},"Las fechas corresponden a la publicación original; el rango incluye ambos días en UTC."),
    status,dateNote,list,h("nav",{class:"dev-review-pagination","aria-label":"Páginas de reseñas"},prev,next));
  root.load=()=>{if(!loaded) load();};
  root.setFilters=values=>{search.value="";game.value="";source.value="";from.value="";to.value="";sort.value="newest";aspect.value=values.aspect||"";sentiment.value=values.sentiment||"";offset=0;load();};
  sync();return root;
}
