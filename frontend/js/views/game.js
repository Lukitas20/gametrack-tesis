/* Ficha del juego: capturas oficiales, opiniones reales y tu experiencia. */
import { api } from "../api.js";
import { personalScorePanel } from "./gametrack-score.js";
import { steamAchievementPanel } from "./steam-achievements.js";
import { cover, gameGrid, reviewItem, saveToListButton, starRating, aspectChip, sentimentChip } from "../components.js";
import { resolve } from "../router.js";
import { isDeveloper, isLoggedIn, refreshRatings, state } from "../store.js";
import { emptyState, h, icon, mount, toast, openModal, modalHead } from "../ui.js";

const sectionHead=(title,caption)=>h("div",{class:"game-section-head"},h("h2",null,title),caption&&h("p",null,caption));

export async function gameView({params,query}) {
  let game;
  try {game=await api.game(Number(params.id));} catch(error){return emptyState("No encontramos el juego",error.message);}
  if(!game.is_enriched)return pendingGameView(game);
  const root=h("div",{class:"game-page game-page-simple"}),scorePanel=personalScorePanel(game.id,game.name,{showFeedback:false});
  const [similar,firstReviews]=await Promise.all([api.similar(game.id,4).catch(()=>[]),api.reviews(game.id,6).catch(error=>error)]);
  const reviewBody=h("div",{class:"game-review-list"});
  let reviews=Array.isArray(firstReviews)?firstReviews:[],offset=reviews.length,hasMore=reviews.length===6;
  const more=h("button",{class:"btn btn-sm",type:"button",onClick:loadMore},"Ver más reseñas");
  function renderReviews(){mount(reviewBody,reviews.length?reviews.map(reviewItem):h("div",{class:"game-empty"},icon("chat",24),h("h3",null,"Todavía no hay reseñas"),h("p",null,"Podés ser la primera persona en contar su experiencia.")));more.hidden=!hasMore;}
  async function loadMore(){more.disabled=true;try{const rows=await api.reviews(game.id,6,offset);offset+=rows.length;hasMore=rows.length===6;reviews=[...new Map([...reviews,...rows].map(row=>[row.id,row])).values()];renderReviews();}catch(error){toast(error.message,"error");}finally{more.disabled=false;}}
  renderReviews();
  if(firstReviews instanceof Error)mount(reviewBody,h("p",{role:"alert"},"No pudimos cargar las reseñas."),h("button",{class:"btn",onClick:loadMore},"Reintentar"));
  const composer=reviewComposer(game,review=>{reviews=[review,...reviews.filter(row=>row.id!==review.id)];renderReviews();scorePanel.refresh();});
  const nav=h("nav",{class:"game-section-nav game-detail-tabs","aria-label":"Ir a una sección del juego"});
  const sections=new Map(),buttons=new Map();
  function scrollToSection(key,{focus=false}={}) {
    if(!sections.has(key))key="resumen";
    for(const [id,button] of buttons){if(id===key)button.setAttribute("aria-current","location");else button.removeAttribute("aria-current");}
    const section=sections.get(key);
    section.scrollIntoView({block:"start",behavior:"instant"});
    if(focus)section.focus({preventScroll:true});
  }
  function addSection(key,label,content){
    const id=`game-${game.id}-${key}`,button=h("button",{type:"button","aria-controls":`${id}-section`,onClick:()=>scrollToSection(key,{focus:true})},label);
    const section=h("section",{id:`${id}-section`,class:`game-scroll-section game-tab-${key}`,"aria-label":label,tabindex:"-1"},content);
    sections.set(key,section);buttons.set(key,button);nav.append(button);
  }
  function showComposer(){if(!composer){window.location.hash="/cuentas";return;}composer.open=true;composer.scrollIntoView({block:"start",behavior:"instant"});composer.querySelector("textarea")?.focus({preventScroll:true});}
  const description=h("p",{class:`game-description${(game.description||"").length>420?" is-collapsed":""}`,id:`game-description-${game.id}`},game.description||"Steam todavía no publicó una descripción para este juego.");
  const expandDescription=(game.description||"").length>420?h("button",{class:"review-read-more",type:"button","aria-expanded":"false","aria-controls":description.id,onClick:()=>{const collapsed=description.classList.toggle("is-collapsed");expandDescription.setAttribute("aria-expanded",String(!collapsed));expandDescription.textContent=collapsed?"Leer más sobre el juego":"Mostrar menos";}},"Leer más sobre el juego"):null;
  const about=h("section",{class:"game-about"},sectionHead("Sobre el juego"),description,expandDescription,
    h("details",{class:"game-tags-fold"},h("summary",null,"Géneros, características y plataformas",icon("chevronDown",14)),
      h("div",{class:"game-feature-tags"},[...(game.genres||[]),...(game.tags||[])].map(tag=>h("span",{class:"chip"},tag.name))),
      game.platforms?.length?h("p",{class:"game-platforms"},"Disponible en ",h("strong",null,game.platforms.join(" · "))):null));
  const experience=!isDeveloper()?h("section",{class:"game-simple-experience"},h("h2",null,"Tu opinión"),
    isLoggedIn()?h("div",null,h("p",null,"¿Ya lo jugaste? Dale tu nota."),starRating(game.id,{onChange:async()=>{await refreshRatings();scorePanel.refresh();}}),
      h("details",{class:"game-private-feedback"},h("summary",null,"Mejorar mis recomendaciones",icon("chevronDown",13)),h("p",null,"Contá qué te gustó. Esta experiencia es privada."),
        h("button",{class:"btn btn-sm",type:"button",onClick:async()=>{const {openPlayFeedback}=await import("./play-feedback.js");openPlayFeedback(game.id,game.name,scorePanel.refresh);}},icon("gamepad",15),"Contar mi experiencia")),
      h("button",{class:"game-write-review-link",type:"button",onClick:showComposer},"Escribir una reseña pública",icon("chevron",13))):h("a",{class:"btn btn-sm",href:"#/cuentas"},"Iniciar sesión para valorar")):null;
  const progressSummary=h("div",{class:"game-progress-summary"});
  let achievementPanel=null;
  if(game.steam_app_id&&!isDeveloper()) {
    if(state.user?.steam_verified)achievementPanel=steamAchievementPanel(game.steam_app_id,game.name,{onUpdate:data=>{
      mount(progressSummary,data.percentage!=null&&data.total>0?h("button",{class:"game-progress-preview",type:"button",onClick:()=>scrollToSection("logros",{focus:true})},icon("trophy",18),h("span",null,h("strong",null,`${data.unlocked} de ${data.total} logros`),h("small",null,`${data.percentage}% desbloqueado · Ver progreso`)),icon("chevron",14)):null);
    }});
    else achievementPanel=emptyState("Tus logros de Steam","Iniciá sesión con Steam para consultar tu progreso real.",h("a",{class:"btn btn-primary",href:"#/cuentas"},"Conectar con Steam"));
  }
  addSection("resumen","Resumen",h("div",{class:"game-overview-layout"},h("div",{class:"game-overview-main"},about),
    h("aside",{class:"game-overview-side"},scorePanel,experience,progressSummary,
      isDeveloper()?h("a",{class:"btn",href:`#/dev/juego/${game.id}`},icon("chart",16),"Ver analítica"):null)));
  addSection("capturas","Capturas",gameGallery(game));
  if(achievementPanel)addSection("logros","Logros",achievementPanel);
  addSection("resenas","Reseñas",h("div",{class:"game-community"},sectionHead("Opiniones de jugadores","Reseñas reales de Steam y GameTrack."),composer,reviewBody,more));
  const related=similar.length?h("section",{class:"game-scroll-related"},sectionHead("Juegos parecidos"),gameGrid(similar.map(item=>item.game))):null;
  mount(root,h("div",{class:"game-breadcrumb"},h("a",{href:query?.get("volver")==="que-jugamos"?"#/que-jugamos?modo=plan":"#/catalogo"},icon("arrowLeft",14),query?.get("volver")==="que-jugamos"?"Volver a los resultados":"Explorar juegos")),
    game.steam_sync_status==="unavailable"?h("p",{class:"notice notice-warn"},"Steam no pudo actualizar la ficha. Mostramos los últimos datos guardados."):null,
    h("header",{class:"game-showcase"},h("div",{class:"game-showcase-media","aria-hidden":"true"},cover(game).firstChild.cloneNode(true)),
      h("div",{class:"game-showcase-content"},h("h1",null,game.name),
        h("div",{class:"game-showcase-meta"},game.developer&&h("span",null,game.developer),game.released&&h("span",null,new Date(game.released+"T00:00:00").getFullYear()),game.metacritic!=null&&h("span",null,`Metacritic ${game.metacritic}/100`)),
        h("div",{class:"game-showcase-actions"},isLoggedIn()&&!isDeveloper()?saveToListButton(game):h("a",{href:"#/cuentas",class:"btn btn-primary"},"Guardar en mis listas"),
          game.steam_app_id&&h("a",{href:`https://store.steampowered.com/app/${game.steam_app_id}/`,target:"_blank",rel:"noopener noreferrer",class:"game-steam-link"},"Ver en Steam ↗")))),nav,h("div",{class:"game-tab-content"},[...sections.values()],related));
  buttons.get("resumen").setAttribute("aria-current","location");
  if(composer&&query?.get("resena")==="editar"){composer.open=true;requestAnimationFrame(()=>{if(root.isConnected)composer.scrollIntoView({block:"start",behavior:"instant"});});}
  else if(query?.get("seccion")&&sections.has(query.get("seccion")))requestAnimationFrame(()=>{if(root.isConnected)scrollToSection(query.get("seccion"));});
  return root;
}

function gameGallery(game) {
  const root=h("section",{class:"game-gallery","aria-label":`Capturas de ${game.name}`});
  let urls=[...new Set(game.screenshots||[])],index=0,checks=0;
  const failed=new Set(),heading=sectionHead("Dentro del juego","Capturas oficiales de la tienda de Steam.");
  const stage=h("div",{class:"game-gallery-stage"}),thumbs=h("div",{class:"game-gallery-thumbs",role:"group","aria-label":"Elegir una captura"}),counter=h("span",{class:"game-gallery-count","aria-live":"polite"});
  function select(next){index=(next+urls.length)%urls.length;render();}
  function enlarge(){openModal(close=>h("div",{class:"game-lightbox"},modalHead(game.name,`Captura ${index+1} de ${urls.length} · Steam`,close),h("img",{src:urls[index],alt:`Captura ${index+1} de ${game.name}`})),{wide:true});}
  function render(){urls=urls.filter(url=>!failed.has(url));index=Math.min(index,Math.max(0,urls.length-1));
    if(!urls.length){mount(root,heading,h("div",{class:"game-gallery-empty",role:"status"},icon("gamepad",28),h("p",null,failed.size?"No pudimos cargar las capturas de Steam.":"Las capturas todavía no están disponibles en esta ficha."),game.steam_app_id&&h("a",{href:`https://store.steampowered.com/app/${game.steam_app_id}/`,target:"_blank",rel:"noopener noreferrer"},"Ver las imágenes en Steam ↗")));return;}
    counter.textContent=`${index+1} / ${urls.length}`;const current=urls[index];
    mount(stage,h("button",{class:"game-gallery-image",type:"button","aria-label":`Ampliar captura ${index+1}`,onClick:enlarge,onKeydown:event=>{if(event.key==="ArrowRight"||event.key==="ArrowLeft"){event.preventDefault();select(index+(event.key==="ArrowRight"?1:-1));stage.querySelector(".game-gallery-image")?.focus();}}},
      h("img",{src:current,alt:`Captura ${index+1} de ${game.name}`,decoding:"async",onError:()=>{failed.add(current);render();}})),
      urls.length>1?h("div",{class:"game-gallery-controls"},h("button",{class:"btn",type:"button","aria-label":"Captura anterior",onClick:()=>select(index-1)},icon("arrowLeft",16)),counter,h("button",{class:"btn",type:"button","aria-label":"Captura siguiente",onClick:()=>select(index+1)},icon("chevron",16))):null,
      h("span",{class:"game-gallery-expand"},"Tocá la imagen para ampliarla"));
    mount(thumbs,urls.map((url,i)=>h("button",{type:"button","aria-label":`Ver captura ${i+1}`,"aria-pressed":String(i===index),onClick:()=>select(i)},h("img",{src:url,alt:"",loading:"lazy",onError:()=>{failed.add(url);render();}}))));mount(root,heading,stage,thumbs);
  }
  render();
  if(!urls.length&&game.steam_app_id)setTimeout(async function poll(){if(!root.isConnected||checks++>=6)return;try{const updated=await api.game(game.id);if(!root.isConnected)return;if(updated.screenshots?.length){urls=updated.screenshots;render();return;}}catch{}if(root.isConnected)setTimeout(poll,10000);},10000);
  return root;
}

function pendingGameView(game){const root=h("div",{class:"game-page game-pending card"},cover(game),h("h1",null,game.name),h("p",null,"La ficha está pendiente de completar con datos oficiales. No mostramos imágenes ni reseñas inventadas."),h("button",{class:"btn",onClick:()=>resolve()},"Ver ficha actualizada"),game.steam_app_id&&h("a",{class:"btn",target:"_blank",rel:"noopener noreferrer",href:`https://store.steampowered.com/app/${game.steam_app_id}/`},"Ver en Steam ↗"),personalScorePanel(game.id,game.name));
  let checks=0;setTimeout(async function poll(){if(!root.isConnected||checks++>=12)return;try{const updated=await api.game(game.id);if(!root.isConnected)return;if(updated.is_enriched){resolve();return;}}catch{}if(root.isConnected)setTimeout(poll,10000);},10000);return root;}

function reviewComposer(game,onSaved){
  if(!isLoggedIn()||isDeveloper())return null;
  const owner=state.user.id,title=h("input",{id:`review-title-${game.id}`,class:"input",maxlength:"200",placeholder:"Un título para tu experiencia (opcional)",disabled:true}),content=h("textarea",{id:`review-content-${game.id}`,class:"textarea",rows:"6",minlength:"10",maxlength:"20000",placeholder:"¿Qué te gustó y qué mejorarías? Contá tu experiencia con el juego.",disabled:true});
  const recommended=h("select",{id:`review-recommend-${game.id}`,class:"input",disabled:true},h("option",{value:""},"Prefiero no indicar"),h("option",{value:"yes"},"Sí, lo recomiendo"),h("option",{value:"no"},"No lo recomiendo"));
  const status=h("p",{class:"game-review-save-status",role:"status"},"Buscando tu reseña guardada…"),preview=h("div"),errorBox=h("p",{role:"alert"}),submit=h("button",{class:"btn btn-primary",type:"submit",disabled:true},"Publicar mi reseña",icon("check",15)),heading=h("span",null,"Escribir mi reseña");
  const root=h("details",{class:"card game-review-composer"},h("summary",null,icon("star",17),heading,icon("chevronDown",15)));
  let timer=null,analysisRevision=0,saving=false,loaded=false,existing=false;
  const field=(label,input)=>h("label",{class:"game-review-field",for:input.id},h("span",null,label),input);
  root.append(h("form",{onSubmit:publish},h("p",{class:"game-composer-help"},"Tu reseña es pública y se guarda en tu perfil. Podés volver a esta ficha para editarla."),field("Título",title),field("Tu reseña",content),field("¿Se lo recomendarías a otro jugador?",recommended),h("details",{class:"game-review-analysis"},h("summary",null,"Ver el análisis de mi texto"),preview),errorBox,h("div",{class:"game-review-save-row"},status,submit)));
  function valid(){submit.disabled=!loaded||saving||content.value.trim().length<10;}
  content.addEventListener("input",()=>{valid();clearTimeout(timer);analysisRevision+=1;timer=setTimeout(analyze,550);});
  async function analyze(){const revision=analysisRevision,text=content.value.trim();if(text.length<10){preview.replaceChildren();return;}try{const result=await api.analyzeText(text);if(revision===analysisRevision&&owner===state.user?.id)mount(preview,analysisCard(result,"Análisis local"));}catch{if(revision===analysisRevision)preview.replaceChildren();}}
  async function loadOwn(){try{const rows=await api.myReviews({game_id:game.id,limit:1});if(owner!==state.user?.id)return;const review=rows[0];if(review){existing=true;title.value=review.title||"";content.value=review.content;recommended.value=review.is_recommended==null?"":review.is_recommended?"yes":"no";heading.textContent="Editar mi reseña";submit.textContent="Guardar cambios";}loaded=true;title.disabled=content.disabled=recommended.disabled=false;status.textContent=existing?"Esta es tu reseña guardada. Los cambios reemplazan la versión anterior.":"Se guardará en Perfil → Mis reseñas.";valid();}catch{mount(status,"No pudimos cargar tu reseña. ",h("button",{class:"btn btn-sm",type:"button",onClick:loadOwn},"Reintentar"));}}
  async function publish(event){event.preventDefault();if(submit.disabled||owner!==state.user?.id)return;saving=true;valid();errorBox.textContent="";submit.textContent="Guardando…";try{const review=await api.publishReview({game_id:game.id,title:title.value.trim()||null,content:content.value.trim(),is_recommended:recommended.value===""?null:recommended.value==="yes"});if(owner!==state.user?.id)return;existing=true;heading.textContent="Editar mi reseña";status.replaceChildren("Guardada en tu perfil. ",h("a",{href:"#/perfil"},"Ver mis reseñas"));onSaved(review);toast("Tu reseña quedó guardada en tu perfil");}catch(error){errorBox.textContent=error.message;}finally{saving=false;submit.textContent=existing?"Guardar cambios":"Publicar mi reseña";valid();}}
  loadOwn();return root;
}

export function analysisCard(analysis,heading){return h("div",{class:"game-analysis-result"},h("p",{class:"eyebrow"},heading),sentimentChip(analysis.sentiment),h("p",{class:"muted"},"Lectura automática del texto; no sustituye la opinión del autor."),(analysis.aspects||[]).map(aspect=>h("div",{class:"game-analysis-aspect"},aspectChip(aspect),aspect.evidence&&h("p",null,`“${aspect.evidence}”`))));}
