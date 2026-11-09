/* Informes del estudio, con asistencia local basada en evidencia. */
import { api } from "../api.js";
import { developerOnly, requiresLogin } from "../components.js";
import { chartCard, divergingBar, divergingStackedBar, sentimentLegend, tableView } from "../charts.js";
import { navigate } from "../router.js";
import { isDeveloper, isLoggedIn, state } from "../store.js";
import { ASPECT_LABEL, SENTIMENT_LABEL, emptyState, h, icon, infoDetails, mount, pct, signed, spinnerBlock, toast } from "../ui.js";
import { developerReviewsPanel } from "./developer-reviews.js";
import { trainedReviewInsights } from "./developer-trained-insights.js";
import { audiencePanel } from "./developer-audience.js";

const count = value => Number(value || 0).toLocaleString("es-AR");
const aspectName = key => ASPECT_LABEL[key] || key;
const aspectOrder = ["jugabilidad", "graficos", "historia", "optimizacion"];
const sectionSlugs = { overview: "resumen", reviews: "resenas", audience: "audiencias" };
const initialSection = query => Object.keys(sectionSlugs).find(key => sectionSlugs[key] === query.get("seccion")) || "overview";

function reportPath({ gameId, studio }, section = "overview") {
  const query = new URLSearchParams();
  if (!gameId && studio) query.set("estudio", studio);
  if (section !== "overview" && sectionSlugs[section]) query.set("seccion", sectionSlugs[section]);
  return `${gameId ? `/dev/juego/${gameId}` : "/dev"}${query.size ? `?${query}` : ""}`;
}

function reportScopePicker(studio, games, gameId, activeSection) {
  const selected = gameId ? String(gameId) : "studio";
  const titles = [...games].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  const occurrences = new Map();
  for (const { nombre } of titles) occurrences.set(nombre, (occurrences.get(nombre) || 0) + 1);
  const select = h("select", { class: "select", onChange: event => {
    const value = event.target.value;
    navigate(reportPath(value === "studio" ? { studio } : { gameId: Number(value) }, activeSection()));
  } },
  (studio || !gameId) && h("option", { value: "studio" }, studio ? `Todo el estudio · ${studio}` : "Todo el estudio"),
  titles.map(game => h("option", { value: String(game.id) }, occurrences.get(game.nombre) > 1
    ? `${game.nombre} · ${game.steam_app_id ? "Steam" : "Otra ficha"}` : game.nombre)));
  select.value = selected;
  return h("label", { class: "dev-report-scope" }, h("span", null, "Informe"), select);
}
function guard() {
  if (!isLoggedIn()) return requiresLogin("Iniciá sesión para abrir tu espacio de estudio.");
  if (!isDeveloper()) return developerOnly();
  return null;
}
function cover(game, className = "dev-cover") {
  const src = game.background_image || (game.steam_app_id ? `https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/${game.steam_app_id}/header.jpg` : null);
  const fallback = h("div", {class:`${className} dev-cover-placeholder`, "aria-hidden":"true"}, icon("gamepad",32));
  return src ? h("img", {class:className,src,alt:"",loading:"lazy",onError:e => e.currentTarget.replaceWith(fallback)}) : fallback;
}
function metric(label, value, caption, symbol, tone = "") {
  return h("article", {class:`dev-metric ${tone}`}, h("span",{class:"dev-label"},icon(symbol,17),label),h("strong",null,value),h("span",{class:"dev-caption"},caption));
}
function metricRow(report) {
  const s=report.resenas, total=s.analizadas+(s.pendientes || 0);
  return h("div",{class:"dev-metrics"},
    metric("Reseñas analizadas",count(s.analizadas),`${count(s.pendientes)} pendientes en la muestra guardada`,"quote"),
    metric("Recepción positiva",s.analizadas ? pct(s.distribucion.positivo,s.analizadas) : "—","Clasificación del texto de las reseñas","heart","dev-metric-positive"),
    metric("Sentimiento neto",s.analizadas ? signed(s.sentimiento_neto,2) : "—","Positivas menos negativas · escala −1 a +1","trending"),
    metric("Cobertura del análisis",total ? pct(s.analizadas,total) : "—",`${count(total)} reseñas guardadas · no es el total de Steam`,"chart"));
}
function studioArtwork(games) {
  const featured=games.filter(game => game.background_image || game.steam_app_id).slice(0,3);
  if(!featured.length) return h("div",{class:"dev-hero-emblem","aria-hidden":"true"},icon("chart",42));
  return h("div",{class:"dev-hero-art","aria-label":"Juegos del estudio"},featured.map(game =>
    h("a",{class:"dev-hero-tile",href:`#/dev/juego/${game.id}`,"aria-label":`Ver informe de ${game.nombre}`},cover(game,"dev-hero-art-cover"),h("span",null,game.nombre))));
}
function tabs(items, initial = "overview") {
  const root=h("div",{class:"dev-workspace"}), nav=h("div",{class:"dev-tabs",role:"tablist","aria-label":"Secciones del informe"});
  const buttons=[],panels=[]; let current=Math.max(0,items.findIndex(item => item.key===initial));
  function activate(index,focus=false,load=true) {
    current=index;
    buttons.forEach((button,i) => {button.setAttribute("aria-selected",String(i===index));button.tabIndex=i===index ? 0 : -1;panels[i].hidden=i!==index;});
    if(focus) buttons[index].focus();
    if(load) items[index].onActivate?.();
  }
  items.forEach((item,index) => {
    const button=h("button",{type:"button",role:"tab",id:`dev-tab-${item.key}`,"aria-controls":`dev-panel-${item.key}`,onClick:() => activate(index),onKeydown:event => {
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();activate(event.key==="Home" ? 0 : event.key==="End" ? items.length-1 : (current+(event.key==="ArrowRight" ? 1 : -1)+items.length)%items.length,true);
    }},icon(item.icon || "chart",16),item.label);
    const panel=h("section",{class:"dev-panel",role:"tabpanel",id:`dev-panel-${item.key}`,"aria-labelledby":button.id,tabindex:"0"},item.content);
    buttons.push(button);panels.push(panel);nav.append(button);
  });
  root.append(nav,...panels);activate(current,false,false);root.activeKey=() => items[current].key;
  requestAnimationFrame(() => {if(root.isConnected) items[current].onActivate?.();});
  root.select=key=>{const index=items.findIndex(item=>item.key===key);if(index>=0) activate(index,true);};return root;
}
function prioritiesPanel(aspects, openReviews) {
  const priorities=aspects.filter(a=>a.menciones>=5&&a.sentimiento_neto<0)
    .sort((a,b)=>b.distribucion.negativo**2/b.menciones-a.distribucion.negativo**2/a.menciones);
  return h("section",{class:"dev-priorities"},heading("Por dónde empezar","Ordenamos los aspectos con balance negativo por cantidad y proporción de críticas. Son señales para investigar."),
    priorities.length ? h("div",{class:"dev-priority-list"},priorities.map((a,index)=>h("article",{class:"dev-priority"},
      h("span",{class:"dev-priority-rank","aria-label":`Prioridad ${index+1}`},String(index+1).padStart(2,"0")),
      h("div",null,h("h3",null,aspectName(a.aspecto)),h("p",{class:"dev-caption"},`${count(a.distribucion.negativo)} de ${count(a.menciones)} menciones negativas (${pct(a.distribucion.negativo,a.menciones)}) · neto ${signed(a.sentimiento_neto,2)}`)),
      h("button",{class:"btn",type:"button",onClick:()=>openReviews(a.aspecto)},"Ver reseñas",icon("chevron",15))))) :
      h("p",{class:"dev-caption"},"No hay aspectos con balance negativo y al menos cinco menciones. Podés explorar críticas puntuales en Reseñas."));
}
function heading(title, caption, action=null) {
  return h("div",{class:"dev-section-heading section-head"},h("div",null,h("h2",null,title),caption && h("p",null,caption)),action);
}
function sentimentBar(summary) {
  const n=summary.analizadas;
  const bar=h("div",{class:"dev-sentiment-bar",role:"img","aria-label":n ? `Positivas ${pct(summary.distribucion.positivo,n)}, neutras ${pct(summary.distribucion.neutro,n)}, negativas ${pct(summary.distribucion.negativo,n)}` : "Sin reseñas analizadas"});
  for(const key of ["positivo","neutro","negativo"]) if(summary.distribucion[key]>0) bar.append(h("span",{style:{width:`${100*summary.distribucion[key]/n}%`,background:`var(--${key})`}}));
  return h("div",{class:"dev-reception"},bar,h("div",{class:"dev-sentiment-legend"},["positivo","neutro","negativo"].map(key => h("span",null,h("i",{style:{background:`var(--${key})`}}),SENTIMENT_LABEL[key],h("strong",null,count(summary.distribucion[key]))))));
}
function insightCards(aspects) {
  const reliable=aspects.filter(a => a.menciones>=5);
  const negative=reliable.filter(a => a.sentimiento_neto<0).sort((a,b) => b.distribucion.negativo**2/b.menciones-a.distribucion.negativo**2/a.menciones);
  const positive=reliable.filter(a => a.sentimiento_neto>0).sort((a,b) => b.sentimiento_neto-a.sentimiento_neto);
  const tile=(label,a,good) => h("article",{class:`dev-insight ${good ? "dev-insight-strength" : "dev-insight-attention"}`},h("span",{class:"dev-insight-mark","aria-hidden":"true"},icon(good ? "trending" : "alert",23)),h("div",{class:"dev-insight-content"},h("span",{class:"dev-label"},label),h("h3",null,a ? aspectName(a.aspecto) : good ? "Sin fortaleza clara" : "Sin alerta dominante"),h("p",null,a ? `${count(a.menciones)} menciones · ${pct(a.distribucion[good ? "positivo" : "negativo"],a.menciones)} ${good ? "positivas" : "negativas"} · neto ${signed(a.sentimiento_neto,2)}` : "Ningún aspecto cumple el balance y las cinco menciones necesarias para destacarlo.")));
  return h("div",{class:"dev-insights"},tile("Qué conviene conservar",positive[0],true),tile("Qué conviene revisar",negative[0],false));
}
function aspectPanel(aspects, comparison, openReviews) {
  if(!aspects.length) return emptyState("Todavía no hay aspectos","Las reseñas necesitan opiniones detectables sobre jugabilidad, gráficos, historia u optimización.");
  const ordered=[...aspects].sort((a,b) => aspectOrder.indexOf(a.aspecto)-aspectOrder.indexOf(b.aspecto));
  return h("section",{class:"dev-aspects"},heading("Recepción por aspecto","El balance se interpreta junto con la cantidad de menciones."),
    h("div",{class:"dev-aspect-grid"},ordered.map(a => h("article",{class:"dev-aspect-card"},
      h("div",{class:"dev-aspect-head"},h("h3",null,aspectName(a.aspecto)),h("span",{class:"dev-badge"},a.menciones<5 ? "Muestra pequeña" : `${count(a.menciones)} menciones`)),
      h("strong",{class:"dev-aspect-score",style:{color:a.sentimiento_neto>=0 ? "var(--positivo)" : "var(--negativo)"}},signed(a.sentimiento_neto,2)),
      sentimentBar({analizadas:a.menciones,distribucion:a.distribucion}),
      comparison?.[a.aspecto]!==undefined && h("p",{class:"dev-caption"},`Catálogo: ${signed(comparison[a.aspecto],2)} · diferencia ${signed(a.sentimiento_neto-comparison[a.aspecto],2)}`),
      a.menciones>0 && h("button",{class:"btn btn-ghost btn-sm dev-aspect-link",type:"button","aria-label":`Ver reseñas sobre ${aspectName(a.aspecto)}`,onClick:()=>openReviews(a.aspecto)},"Ver reseñas",icon("chevron",14))))),
    infoDetails("Ver gráfico por aspecto",chartCard({title:"Balance por aspecto",subtitle:"Proporción positiva menos negativa, de −1 a +1.",chart:divergingBar(ordered.map(a => ({label:aspectName(a.aspecto),value:a.sentimiento_neto,tip:[`${count(a.menciones)} menciones`]})),{labelWidth:120,domain:1}),table:tableView(["Aspecto","Neto","Menciones","Positivas","Neutras","Negativas"],ordered.map(a => [aspectName(a.aspecto),signed(a.sentimiento_neto,2),a.menciones,a.distribucion.positivo,a.distribucion.neutro,a.distribucion.negativo]))})));
}
function evidencePanel(references, openReview) {
  const entries=Object.entries(references || {}).filter(([,list]) => list.length);
  return entries.length ? infoDetails("Fragmentos de reseñas para revisar",
    h("p",null,"Fragmentos asociados a aspectos que el análisis clasificó como negativos."),
    h("div",{class:"dev-evidence-grid"},entries.map(([aspect,list]) => h("section",{class:"dev-evidence-card"},
      h("h3",null,aspectName(aspect)),list.map(item => h("div",{class:"dev-evidence-content"},
        h("blockquote",null,item.cita),
        h("button",{class:"btn btn-ghost btn-sm",type:"button",onClick:()=>openReview(aspect,item.review_id)},"Ver reseña completa",icon("chevron",14)))))))) : null;
}
function assistantPanel(scope) {
  const response=h("div",{class:"dev-assistant-response",role:"status","aria-live":"polite"},h("p",{class:"dev-caption"},"Consultá prioridades, temas o evidencia del informe."));
  const input=h("input",{class:"dev-question-input",placeholder:"¿Qué debería revisar de la optimización?",maxlength:"600",required:true,"aria-label":"Pregunta al asistente del estudio"});
  const submit=h("button",{class:"btn btn-primary",type:"submit"},icon("sparkles",16),"Consultar"),suggestions=h("div",{class:"dev-suggestions"});
  let busy=false;
  async function ask(question) {
    if(busy || !question.trim()) return;
    busy=true;input.disabled=submit.disabled=true;suggestions.querySelectorAll("button").forEach(b => b.disabled=true);
    response.setAttribute("aria-busy","true");mount(response,spinnerBlock("Leyendo el informe…"));
    try {
      const answer=await api.developerAssistant({...scope,question:question.trim()});
      mount(response,h("span",{class:"dev-label"},"Tu consulta"),h("p",{class:"dev-assistant-question"},question),h("h3",null,answer.title),h("p",null,answer.answer),answer.actions.length ? h("ul",{class:"dev-assistant-actions"},answer.actions.map(action => h("li",null,action))) : null,
        answer.evidence.length ? h("details",{class:"dev-assistant-evidence"},h("summary",null,`Ver ${answer.evidence.length} fragmentos de respaldo`),answer.evidence.map(item => h("blockquote",null,h("span",{class:"dev-label"},aspectName(item.aspect)),item.quote))) : null,
        h("p",{class:"dev-caption"},answer.basis),
        h("span",{class:"dev-badge"},answer.trained_ai_used ? "Incluye el modelo entrenado con reseñas reales" : "Respuesta basada en las métricas disponibles"),
        h("details",{class:"dev-method"},h("summary",null,"Alcance del análisis"),h("p",null,answer.limitation),h("p",null,"El modelo clasifica textos y detecta temas. Las respuestas se organizan a partir de los datos del informe; no es un chatbot generativo.")));
    } catch(error) {mount(response,h("p",{role:"alert"},error.message));}
    finally {busy=false;input.disabled=submit.disabled=false;response.removeAttribute("aria-busy");suggestions.querySelectorAll("button").forEach(b => b.disabled=false);}
  }
  for(const question of ["¿Qué conviene mejorar?","¿Qué temas detecta la IA?","¿Qué funciona mejor?","¿Hay suficiente evidencia?","Comparar con el catálogo"]) suggestions.append(h("button",{type:"button",class:"dev-suggestion",onClick:() => {input.value=question;ask(question);}},question));
  const panel=h("section",{class:"dev-assistant","aria-label":"Asistente del estudio"},h("div",{class:"dev-assistant-heading"},h("span",{class:"dev-assistant-mark"},icon("sparkles",22)),h("h2",null,"Consultá tu informe")),infoDetails("Preguntas sugeridas",suggestions),response,h("form",{class:"dev-question-form",onSubmit:event => {event.preventDefault();ask(input.value);}},input,submit));
  panel.ask=question=>{input.value=question;ask(question);};
  return panel;
}
function exportReport(report) {
  const rows=[["Juego","Nota GameTrack (1-5)","Valoraciones GameTrack","Reseñas guardadas","Analizadas","Positivas","Neutras","Negativas","Neto (-1 a +1)"]];
  const games=report.juegos || [{nombre:report.juego.nombre,rating_local_promedio:report.juego.rating_local_promedio,cantidad_ratings_local:report.juego.cantidad_ratings_local,cantidad_resenas:report.resenas.analizadas+report.resenas.pendientes,resenas_analizadas:report.resenas.analizadas,distribucion:report.resenas.distribucion,sentimiento_neto:report.resenas.sentimiento_neto}];
  for(const g of games) rows.push([g.nombre,g.cantidad_ratings_local ? g.rating_local_promedio : "Sin valoraciones",g.cantidad_ratings_local,g.cantidad_resenas,g.resenas_analizadas,g.distribucion.positivo,g.distribucion.neutro,g.distribucion.negativo,g.resenas_analizadas ? g.sentimiento_neto : "Sin análisis"]);
  rows.push([],["Aspecto","Menciones","Positivas","Neutras","Negativas","Neto"]);
  for(const a of report.aspectos) rows.push([aspectName(a.aspecto),a.menciones,a.distribucion.positivo,a.distribucion.neutro,a.distribucion.negativo,a.sentimiento_neto]);
  if(report.audience) {
    rows.push([],["Audiencia observada","Tamaño","Unidad","Denominador","Criterio","Procedencia"]);
    for(const group of report.audience.groups) rows.push([group.label,group.suppressed ? "Menos de 5; oculto" : group.size,group.unit,group.denominator,group.criterion,group.source]);
    const histories=report.audience.steam_histories;
    if(histories) {
      rows.push([],["Historiales públicos Steam",histories.public_histories,"Consultados",histories.checked_authors,"Autores identificados",histories.identified_authors],
        ["Recomendaciones con metadatos",histories.recommendations_with_metadata,"Recomendaciones observadas",histories.other_recommendations],
        ["Grupo de autores Steam","Autores","Historiales públicos","Sólo recomiendan","Sólo desaconsejan","Votos distintos entre títulos","Sin voto guardado","Criterio"]);
      for(const group of histories.groups) rows.push([group.label,group.size,group.denominator,group.reception.recommended,group.reception.not_recommended,group.reception.mixed,group.reception.unknown,group.criterion]);
      rows.push(["Procedencia",histories.source],["Selección",histories.selection],["Consulta más antigua",histories.oldest_checked_at],["Consulta más reciente",histories.newest_checked_at]);
    }
    rows.push(...report.audience.limitations.map(text=>["Alcance",text]));
  }
  rows.push([],["Fuente","Reseñas guardadas; clasificación automática local. No es una puntuación de Metacritic."]);
  const csv=rows.map(row => row.map(value => {let text=String(value ?? "");if(typeof value==="string" && /^[=+\-@\t\r]/.test(text)) text="'"+text;return '"'+text.replaceAll('"','""')+'"';}).join(";")).join("\r\n");
  const url=URL.createObjectURL(new Blob(["\ufeff",csv],{type:"text/csv;charset=utf-8"})),link=h("a",{href:url,download:`gametrack-informe-${new Date().toISOString().slice(0,10)}.csv`});document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url),1000);toast("Informe exportado con los datos de esta vista.");
}
function studioPicker(current = "", activeSection = () => "overview") {
  const choices=h("div",{class:"dev-studio-options","aria-label":"Estudios encontrados"}),input=h("input",{value:current,placeholder:"Buscar un estudio…",maxlength:"120","aria-label":"Buscar estudio"});
  let timer,revision=0;
  async function search() {
    const request=++revision;
    try {const rows=await api.studios(input.value);if(request!==revision || !choices.isConnected) return;
      mount(choices,rows.length ? rows.map(row => h("button",{type:"button",onClick:() => navigate(reportPath({studio:row.name},activeSection()))},h("span",null,row.name),h("span",{class:"dev-caption"},`${count(row.games)} juegos`))) : h("p",{class:"dev-caption"},"No encontramos estudios con ese nombre."));
    } catch(error) {if(request===revision) mount(choices,h("p",{role:"alert"},error.message));}
  }
  input.addEventListener("input",() => {clearTimeout(timer);++revision;timer=setTimeout(search,250);});
  return h("details",{class:"dev-studio-picker",onToggle:event => {if(event.currentTarget.open) {search();input.focus();}},onKeydown:event => {
    if(event.key==="Escape") {event.currentTarget.open=false;event.currentTarget.querySelector("summary").focus();}
  }},h("summary",null,icon("search",15),"Cambiar estudio"),h("div",{class:"dev-studio-popover"},h("form",{onSubmit:event => {event.preventDefault();if(input.value.trim()) navigate(reportPath({studio:input.value.trim()},activeSection()));}},input,h("button",{class:"btn btn-sm",type:"submit"},"Abrir")),choices));
}
function processButton(scope, pending, refresh) {
  return h("button",{class:"btn btn-primary",type:"button",disabled:!pending,onClick:async event => {
    const button=event.currentTarget;button.disabled=true;const old=button.textContent;button.textContent="Analizando…";
    try {const result=await api.processReviews(false,{...scope,limit:300});toast(result.mensaje);await refresh();}
    catch(error) {toast(error.message,"error");button.disabled=false;button.textContent=old;}
  }},icon("refresh",15),pending ? "Analizar pendientes" : "Análisis al día");
}
function reportToolbar(report, scope, activeSection, refresh) {
  const game=report.juego, studio=report.estudio || game?.desarrollador?.trim();
  return h("div",{class:"dev-toolbar dev-report-toolbar"},
    reportScopePicker(studio,report.juegos || (report.juegos_del_estudio?.length ? report.juegos_del_estudio : [game]),game?.id,activeSection),
    h("div",{class:"dev-toolbar-actions"},
      studioPicker(studio,activeSection),
      game && h("a",{class:"btn",href:`#/juego/${game.id}`},"Ver ficha pública",icon("chevron",15)),
      h("button",{class:"btn",type:"button",onClick:()=>exportReport(report)},icon("list",15),"Exportar informe"),
      h("button",{class:"btn",type:"button",onClick:async event=>{event.currentTarget.disabled=true;await refresh();}},icon("refresh",15),"Actualizar"),
      processButton(scope,report.resenas.pendientes,refresh)));
}
function receptionChart(report) {
  const games=report.juegos || [{nombre:report.juego.nombre,resenas_analizadas:report.resenas.analizadas,...report.resenas}];
  return chartCard({title:report.juegos ? "Recepción por título" : "Cómo se recibe este juego",subtitle:"Comparación de la muestra guardada y analizada.",chart:games.some(g => g.resenas_analizadas) ? divergingStackedBar(games.map(g => ({label:g.nombre,gameId:g.id,...g.distribucion})),{labelWidth:170,onRowClick:report.juegos ? row => navigate(`/dev/juego/${row.gameId}`) : null}) : emptyState("Todavía no hay reseñas analizadas","Procesá las reseñas pendientes para completar el informe."),legendEntries:sentimentLegend(report.resenas.distribucion),table:tableView(["Juego","Analizadas","Positivas","Neutras","Negativas","Neto"],games.map(g => [report.juegos ? h("a",{href:`#/dev/juego/${g.id}`},g.nombre) : g.nombre,g.resenas_analizadas,g.distribucion.positivo,g.distribucion.neutro,g.distribucion.negativo,g.resenas_analizadas ? signed(g.sentimiento_neto,2) : "—"]))});
}
function workspaceFor(report, scope, initial, comparison) {
  const assistant=assistantPanel(scope), assistantFold=infoDetails("Asistente del informe",assistant);
  const audience=audiencePanel(report.audience,scope);
  const openReviews=(aspect,sentiment="",reviewId=null)=>{
    reviews.setFilters({aspect,sentiment,reviewId});
    workspace.select("reviews");
  };
  const evidence=evidencePanel(report.referencias_negativas,(aspect,id)=>openReviews(aspect,"negativo",id));
  const reviews=developerReviewsPanel(scope,report.juegos,evidence);
  const overview=h("div",{class:"dev-overview"},assistantFold,insightCards(report.aspectos),
    receptionChart(report),aspectPanel(report.aspectos,comparison,openReviews),
    infoDetails("Prioridades y reseñas para revisar",prioritiesPanel(report.aspectos,aspect=>openReviews(aspect,"negativo"))),
    trainedReviewInsights(report.trained_ai,{onAsk:question=>{
      assistantFold.open=true;
      assistantFold.querySelector("summary").focus();
      assistant.ask(question);
    }}),
    h("p",{class:"dev-data-note"},"Muestra guardada · el sentimiento del texto se distingue del voto de Steam y del Metascore."));
  const workspace=tabs([
    {key:"overview",label:"Resumen",icon:"chart",content:overview},
    {key:"reviews",label:"Reseñas",icon:"quote",content:reviews,onActivate:reviews.load},
    {key:"audience",label:"Audiencias",icon:"users",content:audience,onActivate:audience.load},
  ],initial);
  return workspace;
}
export async function developerView({query=new URLSearchParams()} = {}) {
  const blocked=guard();if(blocked) return blocked;
  if (!query.get("estudio") && state.user?.analysis_game_id) return developerGameView({params:{id:state.user.analysis_game_id},query});
  if (!query.get("estudio") && !state.user?.studio) return h("div", {class:"dev-page"},
    emptyState("Prepará tu espacio de estudio", "Indicá tu nombre y elegí un juego propio o de referencia para empezar.", h("a", {class:"btn btn-primary",href:"#/bienvenida"}, "Configurar mi estudio")));
  const target=query.get("estudio") || state.user?.studio,container=h("div",{class:"dev-page"});let workspace;
  async function load() {
    const selected=workspace?.activeKey() || initialSection(query);
    try {
      const [studio,overview]=await Promise.all([api.studioAnalytics(target),api.overview().catch(() => null)]);
      const comparison=overview?.resenas_analizadas ? Object.fromEntries(overview.aspectos.map(a => [a.aspecto,a.sentimiento_neto])) : null;
      const scope={studio:studio.estudio};workspace=workspaceFor(studio,scope,selected,comparison);
      mount(container,
        reportToolbar(studio,scope,workspace.activeKey,load),
        h("header",{class:"dev-hero"},
          h("div",{class:"dev-hero-content"},
            h("span",{class:"dev-eyebrow"},icon("chart",16),"Tu espacio de desarrollo"),
            h("h1",null,studio.estudio),
            h("div",{class:"dev-hero-meta"},
              h("span",{class:"dev-badge"},icon("gamepad",14),`${studio.juegos.length} títulos`),
              h("span",{class:"dev-badge"},icon("quote",14),`${count(studio.resenas.analizadas+studio.resenas.pendientes)} reseñas guardadas`))),
          studioArtwork(studio.juegos)),
        metricRow(studio),workspace);
    } catch(error) {mount(container,heading("Panel del estudio","Elegí un estudio para explorar su recepción."),studioPicker(target || ""),emptyState("No pudimos abrir este informe",error.message),h("button",{class:"btn",onClick:load},"Volver a intentar"));}
  }
  await load();return container;
}
export async function developerGameView({params,query=new URLSearchParams()}) {
  const blocked=guard();if(blocked) return blocked;
  const container=h("div",{class:"dev-page"});let workspace;
  async function load() {
    try {
      const data=await api.gameAnalytics(Number(params.id)),game=data.juego,scope={game_id:game.id};
      workspace=workspaceFor(data,scope,workspace?.activeKey() || initialSection(query));
      mount(container,reportToolbar(data,scope,workspace.activeKey,load),
        h("header",{class:"dev-hero dev-game-hero"},cover(game,"dev-hero-cover"),
          h("div",{class:"dev-hero-content"},h("span",{class:"dev-eyebrow"},icon("chart",16),"Informe del juego"),
            h("h1",null,game.nombre),h("p",null,game.desarrollador || "Desarrollador no informado"),
            h("div",{class:"dev-hero-meta"},h("span",{class:"dev-badge"},game.cantidad_ratings_local ? `${game.rating_local_promedio.toFixed(2)}/5 · ${count(game.cantidad_ratings_local)} valoraciones GameTrack` : "Sin valoraciones GameTrack")))),
        metricRow(data),workspace);
    } catch(error) {mount(container,h("a",{class:"dev-back",href:"#/dev"},"Volver al estudio"),emptyState("No pudimos abrir la analítica",error.message),h("button",{class:"btn",onClick:load},"Volver a intentar"));}
  }
  await load();return container;
}
