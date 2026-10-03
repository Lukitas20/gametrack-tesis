/* Informes del estudio, con asistencia local basada en evidencia. */
import { api } from "../api.js";
import { developerOnly, requiresLogin } from "../components.js";
import { chartCard, divergingBar, divergingStackedBar, sentimentLegend, tableView } from "../charts.js";
import { navigate } from "../router.js";
import { isDeveloper, isLoggedIn, state } from "../store.js";
import { ASPECT_LABEL, SENTIMENT_LABEL, emptyState, h, icon, mount, pct, signed, spinnerBlock, toast } from "../ui.js";

const count = value => Number(value || 0).toLocaleString("es-AR");
const aspectName = key => ASPECT_LABEL[key] || key;
const aspectOrder = ["jugabilidad", "graficos", "historia", "optimizacion"];
function guard() {
  if (!isLoggedIn()) return requiresLogin("Iniciá sesión con una cuenta de desarrollador para abrir el estudio.");
  if (!isDeveloper()) return developerOnly();
  return null;
}
function cover(game, className = "dev-cover") {
  const src = game.background_image || (game.steam_app_id ? `https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/${game.steam_app_id}/header.jpg` : null);
  const fallback = h("div", {class:`${className} dev-cover-placeholder`, "aria-hidden":"true"}, icon("gamepad",32));
  return src ? h("img", {class:className,src,alt:"",loading:"lazy",onError:e => e.currentTarget.replaceWith(fallback)}) : fallback;
}
function metric(label, value, caption, tone = "") {
  return h("article", {class:`dev-metric ${tone}`}, h("span",{class:"dev-label"},label),h("strong",null,value),h("span",{class:"dev-caption"},caption));
}
function metricRow(report) {
  const s=report.resenas, total=s.analizadas+(s.pendientes || 0);
  return h("div",{class:"dev-metrics"},
    metric("Reseñas analizadas",count(s.analizadas),`${count(s.pendientes)} pendientes en la muestra guardada`),
    metric("Recepción positiva",s.analizadas ? pct(s.distribucion.positivo,s.analizadas) : "—","Clasificación local del texto","dev-metric-positive"),
    metric("Sentimiento neto",s.analizadas ? signed(s.sentimiento_neto,2) : "—","Positivas menos negativas · escala −1 a +1"),
    metric("Cobertura del análisis",total ? pct(s.analizadas,total) : "—",`${count(total)} reseñas guardadas · no es el total de Steam`));
}
function tabs(items, initial = "overview") {
  const root=h("div",{class:"dev-workspace"}), nav=h("div",{class:"dev-tabs",role:"tablist","aria-label":"Secciones del estudio"});
  const buttons=[],panels=[]; let current=Math.max(0,items.findIndex(item => item.key===initial));
  function activate(index,focus=false) {
    current=index;
    buttons.forEach((button,i) => {button.setAttribute("aria-selected",String(i===index));button.tabIndex=i===index ? 0 : -1;panels[i].hidden=i!==index;});
    if(focus) buttons[index].focus();
  }
  items.forEach((item,index) => {
    const button=h("button",{type:"button",role:"tab",id:`dev-tab-${item.key}`,"aria-controls":`dev-panel-${item.key}`,onClick:() => activate(index),onKeydown:event => {
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();activate(event.key==="Home" ? 0 : event.key==="End" ? items.length-1 : (current+(event.key==="ArrowRight" ? 1 : -1)+items.length)%items.length,true);
    }},icon(item.icon || "chart",16),item.label);
    const panel=h("section",{class:"dev-panel",role:"tabpanel",id:`dev-panel-${item.key}`,"aria-labelledby":button.id,tabindex:"0"},item.content);
    buttons.push(button);panels.push(panel);nav.append(button);
  });
  root.append(nav,...panels);activate(current);root.activeKey=() => items[current].key;return root;
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
  const tile=(label,a,good) => h("article",{class:"dev-insight"},h("span",{class:"dev-label"},icon(good ? "trending" : "alert",16),label),h("h3",null,a ? aspectName(a.aspecto) : good ? "Sin fortaleza clara" : "Sin alerta dominante"),h("p",null,a ? `${count(a.menciones)} menciones · ${pct(a.distribucion[good ? "positivo" : "negativo"],a.menciones)} ${good ? "positivas" : "negativas"} · neto ${signed(a.sentimiento_neto,2)}` : "Ningún aspecto cumple el balance y las cinco menciones necesarias para destacarlo."));
  return h("div",{class:"dev-insights"},tile("Qué conviene conservar",positive[0],true),tile("Qué conviene revisar",negative[0],false));
}
function aspectPanel(aspects, comparison) {
  if(!aspects.length) return emptyState("Todavía no hay aspectos","Las reseñas necesitan opiniones detectables sobre jugabilidad, gráficos, historia u optimización.");
  const ordered=[...aspects].sort((a,b) => aspectOrder.indexOf(a.aspecto)-aspectOrder.indexOf(b.aspecto));
  return h("div",{class:"dev-aspects"},heading("Qué dicen de cada aspecto","Leé el balance junto con las menciones. Una muestra pequeña necesita más revisión."),
    h("div",{class:"dev-aspect-grid"},ordered.map(a => h("article",{class:"dev-aspect-card"},h("div",{class:"dev-aspect-head"},h("h3",null,aspectName(a.aspecto)),h("span",{class:"dev-badge"},a.menciones<5 ? "Muestra pequeña" : `${count(a.menciones)} menciones`)),h("strong",{class:"dev-aspect-score",style:{color:a.sentimiento_neto>=0 ? "var(--positivo)" : "var(--negativo)"}},signed(a.sentimiento_neto,2)),sentimentBar({analizadas:a.menciones,distribucion:a.distribucion}),comparison?.[a.aspecto]!==undefined && h("p",{class:"dev-caption"},`Catálogo: ${signed(comparison[a.aspecto],2)} · diferencia ${signed(a.sentimiento_neto-comparison[a.aspecto],2)}`)))),
    chartCard({title:"Balance por aspecto",subtitle:"Proporción positiva menos negativa, de −1 a +1.",chart:divergingBar(ordered.map(a => ({label:aspectName(a.aspecto),value:a.sentimiento_neto,tip:[`${count(a.menciones)} menciones`]})),{labelWidth:120,domain:1}),table:tableView(["Aspecto","Neto","Menciones","Positivas","Neutras","Negativas"],ordered.map(a => [aspectName(a.aspecto),signed(a.sentimiento_neto,2),a.menciones,a.distribucion.positivo,a.distribucion.neutro,a.distribucion.negativo]))}));
}
function evidencePanel(quotes) {
  const entries=Object.entries(quotes || {}).filter(([,list]) => list.length);
  return h("div",null,heading("Escuchá a la comunidad","Fragmentos que el análisis local clasificó como negativos. Abrí cada aspecto para revisar la evidencia."),entries.length ? h("div",{class:"dev-evidence-grid"},entries.map(([aspect,list]) => h("details",{class:"dev-evidence-card"},h("summary",null,icon("quote",18),h("span",null,aspectName(aspect)),h("span",{class:"dev-badge"},`${list.length} fragmentos`),icon("chevronDown",16)),h("div",{class:"dev-evidence-content"},list.map(quote => h("blockquote",null,quote)))))) : emptyState("Sin fragmentos negativos disponibles","No hay evidencia textual guardada para mostrar en este informe."));
}
function assistantPanel(scope) {
  const response=h("div",{class:"dev-assistant-response",role:"status","aria-live":"polite"},h("p",{class:"dev-caption"},"Elegí una consulta o escribí sobre el informe. La respuesta usará las reseñas guardadas y su análisis local."));
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
        h("p",{class:"dev-caption"},answer.basis),h("details",{class:"dev-method"},h("summary",null,"Alcance del análisis"),h("p",null,answer.limitation)));
    } catch(error) {mount(response,h("p",{role:"alert"},error.message));}
    finally {busy=false;input.disabled=submit.disabled=false;response.removeAttribute("aria-busy");suggestions.querySelectorAll("button").forEach(b => b.disabled=false);}
  }
  for(const question of ["¿Qué conviene mejorar?","¿Qué funciona mejor?","¿Hay suficiente evidencia?","Comparar con el catálogo"]) suggestions.append(h("button",{type:"button",class:"dev-suggestion",onClick:() => {input.value=question;ask(question);}},question));
  return h("section",{class:"dev-assistant","aria-label":"Asistente local del estudio"},h("div",{class:"dev-assistant-heading"},h("span",{class:"dev-assistant-mark"},icon("sparkles",22)),h("div",null,h("h2",null,"Una segunda mirada a tu informe"),h("p",null,"Consultá prioridades, fortalezas y evidencia.")),h("span",{class:"dev-badge"},"IA local · sentimiento y aspectos")),suggestions,response,h("form",{class:"dev-question-form",onSubmit:event => {event.preventDefault();ask(input.value);}},input,submit));
}
function exportReport(report) {
  const rows=[["Juego","Nota GameTrack (1-5)","Valoraciones GameTrack","Reseñas guardadas","Analizadas","Positivas","Neutras","Negativas","Neto (-1 a +1)"]];
  const games=report.juegos || [{nombre:report.juego.nombre,rating_local_promedio:report.juego.rating_local_promedio,cantidad_ratings_local:report.juego.cantidad_ratings_local,cantidad_resenas:report.resenas.analizadas+report.resenas.pendientes,resenas_analizadas:report.resenas.analizadas,distribucion:report.resenas.distribucion,sentimiento_neto:report.resenas.sentimiento_neto}];
  for(const g of games) rows.push([g.nombre,g.cantidad_ratings_local ? g.rating_local_promedio : "Sin valoraciones",g.cantidad_ratings_local,g.cantidad_resenas,g.resenas_analizadas,g.distribucion.positivo,g.distribucion.neutro,g.distribucion.negativo,g.resenas_analizadas ? g.sentimiento_neto : "Sin análisis"]);
  rows.push([],["Aspecto","Menciones","Positivas","Neutras","Negativas","Neto"]);
  for(const a of report.aspectos) rows.push([aspectName(a.aspecto),a.menciones,a.distribucion.positivo,a.distribucion.neutro,a.distribucion.negativo,a.sentimiento_neto]);
  rows.push([],["Fuente","Reseñas guardadas; clasificación automática local. No es una puntuación de Metacritic."]);
  const csv=rows.map(row => row.map(value => {let text=String(value ?? "");if(typeof value==="string" && /^[=+\-@\t\r]/.test(text)) text="'"+text;return '"'+text.replaceAll('"','""')+'"';}).join(";")).join("\r\n");
  const url=URL.createObjectURL(new Blob(["\ufeff",csv],{type:"text/csv;charset=utf-8"})),link=h("a",{href:url,download:`gametrack-informe-${new Date().toISOString().slice(0,10)}.csv`});document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url),1000);toast("Informe exportado con los datos de esta vista.");
}
function studioPicker(current = "") {
  const choices=h("div",{class:"dev-studio-options","aria-label":"Estudios encontrados"}),input=h("input",{value:current,placeholder:"Buscar un estudio…",maxlength:"120","aria-label":"Buscar estudio"});
  let timer,revision=0;
  async function search() {
    const request=++revision;
    try {const rows=await api.studios(input.value);if(request!==revision || !choices.isConnected) return;
      mount(choices,rows.length ? rows.map(row => h("button",{type:"button",onClick:() => navigate(`/dev?estudio=${encodeURIComponent(row.name)}`)},h("span",null,row.name),h("span",{class:"dev-caption"},`${count(row.games)} juegos`))) : h("p",{class:"dev-caption"},"No encontramos estudios con ese nombre."));
    } catch(error) {if(request===revision) mount(choices,h("p",{role:"alert"},error.message));}
  }
  input.addEventListener("input",() => {clearTimeout(timer);++revision;timer=setTimeout(search,250);});
  return h("details",{class:"dev-studio-picker",onToggle:event => {if(event.currentTarget.open) {search();input.focus();}}},h("summary",null,icon("search",15),"Cambiar estudio"),h("form",{onSubmit:event => {event.preventDefault();if(input.value.trim()) navigate(`/dev?estudio=${encodeURIComponent(input.value.trim())}`);}},input,h("button",{class:"btn btn-sm",type:"submit"},"Abrir")),choices);
}
function processButton(scope, pending, refresh) {
  return h("button",{class:"btn btn-primary",type:"button",disabled:!pending,onClick:async event => {
    const button=event.currentTarget;button.disabled=true;const old=button.textContent;button.textContent="Analizando…";
    try {const result=await api.processReviews(false,{...scope,limit:300});toast(result.mensaje);await refresh();}
    catch(error) {toast(error.message,"error");button.disabled=false;button.textContent=old;}
  }},icon("refresh",15),pending ? "Analizar pendientes" : "Análisis al día");
}
function gamesPanel(games) {
  const list=h("div",{class:"dev-game-grid"}),resultCount=h("span",{class:"dev-caption",role:"status"});
  const input=h("input",{placeholder:"Buscar entre los juegos del estudio","aria-label":"Buscar juegos del estudio"});
  const sort=h("select",{"aria-label":"Ordenar juegos del estudio"},h("option",{value:"reviews"},"Más reseñas analizadas"),h("option",{value:"negative"},"Mayor proporción negativa"),h("option",{value:"pending"},"Más pendientes"),h("option",{value:"name"},"Nombre A–Z"));
  const normalize=text => text.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
  function render() {
    const rows=games.filter(g => normalize(g.nombre).includes(normalize(input.value.trim()))).sort((a,b) => sort.value==="name" ? a.nombre.localeCompare(b.nombre,"es") : sort.value==="negative" ? (b.resenas_analizadas ? b.distribucion.negativo/b.resenas_analizadas : -1)-(a.resenas_analizadas ? a.distribucion.negativo/a.resenas_analizadas : -1) : sort.value==="pending" ? (b.cantidad_resenas-b.resenas_analizadas)-(a.cantidad_resenas-a.resenas_analizadas) : b.resenas_analizadas-a.resenas_analizadas);
    resultCount.textContent=`${rows.length} de ${games.length} juegos`;
    mount(list,rows.length ? rows.map(g => h("a",{class:"dev-game-card",href:`#/dev/juego/${g.id}`,"aria-label":`Analizar ${g.nombre}`},cover(g),h("div",{class:"dev-game-info"},h("div",{class:"dev-game-title"},h("h3",null,g.nombre),icon("chevron",18)),h("p",{class:"dev-caption"},`${count(g.resenas_analizadas)} reseñas analizadas · ${count(Math.max(0,g.cantidad_resenas-g.resenas_analizadas))} pendientes`),sentimentBar({analizadas:g.resenas_analizadas,distribucion:g.distribucion}),h("div",{class:"dev-game-footer"},h("span",null,g.resenas_analizadas ? `${pct(g.distribucion.positivo,g.resenas_analizadas)} positivas` : "Sin análisis"),h("span",{class:"dev-badge"},"Ver informe"))))) : emptyState(games.length ? "No hay coincidencias" : "Sin títulos en este estudio",games.length ? "Probá con otro nombre de juego." : "Elegí otro estudio del catálogo o comprobá su nombre."));
  }
  input.addEventListener("input",render);sort.addEventListener("change",render);render();
  return h("div",null,heading("Tus títulos, en detalle","Abrí un juego para consultar sus aspectos y la evidencia de sus reseñas."),h("div",{class:"dev-game-controls"},h("div",{class:"dev-search-field"},icon("search",16),input),sort,resultCount),list);
}
function receptionChart(report) {
  const games=report.juegos || [{nombre:report.juego.nombre,resenas_analizadas:report.resenas.analizadas,...report.resenas}];
  return chartCard({title:report.juegos ? "Recepción por título" : "Cómo se recibe este juego",subtitle:"Comparación de la muestra guardada y analizada.",chart:games.some(g => g.resenas_analizadas) ? divergingStackedBar(games.map(g => ({label:g.nombre,gameId:g.id,...g.distribucion})),{labelWidth:170,onRowClick:report.juegos ? row => navigate(`/dev/juego/${row.gameId}`) : null}) : emptyState("Todavía no hay reseñas analizadas","Procesá las reseñas pendientes para completar el informe."),legendEntries:sentimentLegend(report.resenas.distribucion),table:tableView(["Juego","Analizadas","Positivas","Neutras","Negativas","Neto"],games.map(g => [report.juegos ? h("a",{href:`#/dev/juego/${g.id}`},g.nombre) : g.nombre,g.resenas_analizadas,g.distribucion.positivo,g.distribucion.neutro,g.distribucion.negativo,g.resenas_analizadas ? signed(g.sentimiento_neto,2) : "—"]))});
}
function workspaceFor(report, scope, initial, comparison) {
  const items=[{key:"overview",label:"Resumen",icon:"chart",content:h("div",{class:"dev-overview"},insightCards(report.aspectos),receptionChart(report),h("p",{class:"dev-data-note"},icon("info",15),"El informe describe la muestra guardada. Una clasificación positiva no equivale a la recomendación de Steam ni al Metascore."))}];
  if(report.juegos) items.push({key:"games",label:`Juegos (${report.juegos.length})`,icon:"gamepad",content:gamesPanel(report.juegos)});
  items.push({key:"aspects",label:"Aspectos",icon:"list",content:aspectPanel(report.aspectos,comparison)},{key:"evidence",label:"Evidencia",icon:"quote",content:evidencePanel(report.citas_negativas)},{key:"assistant",label:"Asistente",icon:"sparkles",content:assistantPanel(scope)});
  return tabs(items,initial);
}
export async function developerView({query=new URLSearchParams()} = {}) {
  const blocked=guard();if(blocked) return blocked;
  const target=query.get("estudio") || state.user?.studio,container=h("div",{class:"dev-page"});let workspace;
  async function load() {
    const selected=workspace?.activeKey() || "overview";
    try {
      const [studio,overview]=await Promise.all([api.studioAnalytics(target),api.overview().catch(() => null)]);
      const comparison=overview?.resenas_analizadas ? Object.fromEntries(overview.aspectos.map(a => [a.aspecto,a.sentimiento_neto])) : null;
      const scope={studio:studio.estudio};workspace=workspaceFor(studio,scope,selected,comparison);
      mount(container,h("header",{class:"dev-hero"},h("div",{class:"dev-hero-content"},h("span",{class:"dev-eyebrow"},icon("chart",16),"GameTrack para desarrolladores"),h("h1",null,studio.estudio),h("p",null,"Entendé cómo se reciben tus juegos y convertí las opiniones en decisiones."),h("div",{class:"dev-hero-meta"},h("span",{class:"dev-badge"},`${studio.juegos.length} títulos`),h("span",{class:"dev-badge"},"Análisis local de reseñas"))),studioPicker(studio.estudio)),
        h("div",{class:"dev-toolbar"},h("span",{class:"dev-caption"},"Tu estudio, de un vistazo"),h("div",{class:"dev-toolbar-actions"},h("button",{class:"btn",type:"button",onClick:() => exportReport(studio)},icon("list",15),"Exportar informe"),h("button",{class:"btn",type:"button",onClick:async event => {event.currentTarget.disabled=true;await load();}},icon("refresh",15),"Actualizar"),processButton(scope,studio.resenas.pendientes,load))),metricRow(studio),workspace);
    } catch(error) {mount(container,heading("Panel del estudio","Elegí un estudio para explorar su recepción."),studioPicker(target || ""),emptyState("No pudimos abrir este informe",error.message),h("button",{class:"btn",onClick:load},"Volver a intentar"));}
  }
  await load();return container;
}
export async function developerGameView({params}) {
  const blocked=guard();if(blocked) return blocked;
  const container=h("div",{class:"dev-page"});let workspace;
  async function load() {
    try {
      const data=await api.gameAnalytics(Number(params.id)),game=data.juego,s=data.resenas;
      workspace=workspaceFor(data,{game_id:game.id},workspace?.activeKey());
      mount(container,h("a",{class:"dev-back",href:`#/dev${game.desarrollador ? `?estudio=${encodeURIComponent(game.desarrollador)}` : ""}`},icon("arrowLeft",16),"Volver al estudio"),h("header",{class:"dev-hero dev-game-hero"},cover(game,"dev-hero-cover"),h("div",{class:"dev-hero-content"},h("span",{class:"dev-eyebrow"},"Informe del juego"),h("h1",null,game.nombre),h("p",null,game.desarrollador || "Desarrollador no informado"),h("div",{class:"dev-hero-meta"},h("span",{class:"dev-badge"},game.cantidad_ratings_local ? `${game.rating_local_promedio.toFixed(2)}/5 · ${count(game.cantidad_ratings_local)} valoraciones GameTrack` : "Sin valoraciones GameTrack")))),h("div",{class:"dev-toolbar"},h("a",{class:"btn",href:`#/juego/${game.id}`},"Ver ficha pública",icon("chevron",15)),h("div",{class:"dev-toolbar-actions"},h("button",{class:"btn",onClick:() => exportReport(data)},icon("list",15),"Exportar informe"),processButton({game_id:game.id},s.pendientes,load))),metricRow(data),workspace);
    } catch(error) {mount(container,h("a",{class:"dev-back",href:"#/dev"},"Volver al estudio"),emptyState("No pudimos abrir la analítica",error.message),h("button",{class:"btn",onClick:load},"Volver a intentar"));}
  }
  await load();return container;
}
