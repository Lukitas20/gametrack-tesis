import { h, mount } from "../ui.js";
import { api } from "../api.js";

const number = value => Number(value || 0).toLocaleString("es-AR");
const date = value => new Date(value).toLocaleString("es-AR", {timeZone:"America/Argentina/Buenos_Aires"});
export function audiencePanel(initial, scope) {
  const root=h("section", {class:"dev-audience", "aria-label":"Audiencias observadas"});
  let data=initial, running=false, started=false, notice="", failed=false, expanded=false;
  const missing=()=>data?.steam_histories?.pending_authors || data?.steam_histories?.identity_games_pending;
  function render() {
    if (!data) {mount(root,h("p",null,"No se pudo obtener la cobertura de audiencias para este informe."));return;}
    const s=data.steam_histories || {}, groups=s.groups || [], coverageNames={ratings:"Valoraciones",reviews:"Reseñas propias",feedback:"Experiencias",public_lists:"Listas públicas",preferences:"Preferencias guardadas"};
    const gtGroups=data.groups.filter(group=>!group.suppressed && group.unit==="usuarios");
    const cards=kind=>groups.filter(g=>g.kind===kind).slice(0,expanded?undefined:12).map(g=>h("article",{class:"dev-aspect-card"},
      h("h3",null,g.label),h("strong",null,`${number(g.size)} de ${number(g.denominator)} autores con historial público consultado`),
      h("p",null,g.criterion),
      h("h4",null,"Recepción de los juegos del informe"),
      h("p",null,`${number(g.reception.recommended)} sólo recomiendan · ${number(g.reception.not_recommended)} sólo desaconsejan · ${number(g.reception.mixed)} con votos distintos entre títulos · ${number(g.reception.unknown)} sin voto guardado.`),
      g.examples.length>0 && h("div",null,h("h4",null,"Otros juegos que recomendaron en común"),h("ul",null,g.examples.map(game=>h("li",null,h("a",{href:`#/juego/${game.game_id}`},game.name),` · ${number(game.authors)} autores de este grupo`))))));
    mount(root,h("h2",null,"Qué otros juegos recomienda esta audiencia"),
      h("p",null,`Agrupamos a los autores de las ${number(data.steam_reviews)} reseñas Steam guardadas para ${number(data.games)} ${data.games===1?"juego":"juegos"}, según sus recomendaciones públicas de otros juegos.`),
      h("p",{class:"dev-data-note"},"Cada grupo requiere al menos dos juegos distintos recomendados con el mismo rasgo y cinco autores. Excluimos los juegos de este informe. Los grupos pueden superponerse."),
      h("div",{class:"dev-toolbar-actions"},scope && h("button",{class:"btn",type:"button",disabled:running,onClick:()=>collect()},running?"Consultando historiales…":missing()?"Continuar consulta de historiales":"Comprobar datos disponibles")),
      h("p",{class:"dev-caption",role:failed?"alert":"status","aria-live":"polite"},notice || (missing()?"Al abrir esta solapa consultamos los historiales públicos por lotes. Los grupos aparecen a medida que hay evidencia suficiente.":"Consulta de la muestra completada; los resultados se conservan durante 24 horas.")),
      h("details",{class:"dev-method"},h("summary",null,`Cobertura: ${number(s.public_histories)} historiales públicos de ${number(s.selected_authors)} autores seleccionados`),
      h("ul",null,
        h("li",null,`${number(s.identified_reviews)} de ${number(data.steam_reviews)} reseñas con autor identificado; ${number(s.identified_authors)} autores distintos identificados.`),
        h("li",null,`${number(s.checked_authors)} de ${number(s.selected_authors)} autores de la muestra consultados; hasta ${number(s.sample_limit)} por informe.`),
        h("li",null,`${number(s.public_histories)} historiales públicos · ${number(s.private_histories)} privados · ${number(s.unavailable_histories)} no disponibles · ${number(s.pending_authors)} pendientes.`),
        h("li",null,`${number(s.authors_with_two_other_recommendations)} autores con al menos dos recomendaciones de otros juegos observadas.`),
        h("li",null,`${number(s.recommendations_with_metadata)} de ${number(s.other_recommendations)} recomendaciones de otros juegos tienen géneros o etiquetas guardados para agrupar.`),
        h("li",null,`Consultamos hasta ${number(s.page_limit)} páginas por autor; ${number(s.partial_histories)} historiales públicos siguen incompletos.`)),
      s.newest_checked_at && h("p",{class:"dev-caption"},`Consultas entre ${date(s.oldest_checked_at)} y ${date(s.newest_checked_at)} (Argentina).`)),
      groups.some(g=>g.kind==="tag") && h("div",null,h("h3",null,"Grupos por rasgos de los juegos recomendados"),h("div",{class:"dev-aspect-grid"},cards("tag"))),
      groups.some(g=>g.kind==="genre") && h("div",null,h("h3",null,"Grupos por géneros recomendados"),h("div",{class:"dev-aspect-grid"},cards("genre"))),
      ["tag","genre"].some(kind=>groups.filter(g=>g.kind===kind).length>12) && h("button",{class:"btn",type:"button",onClick:()=>{expanded=!expanded;render();}},expanded?"Mostrar los 12 grupos más numerosos por categoría":"Mostrar todos los grupos observados"),
      !groups.length && h("p",{class:"dev-data-note"},"Todavía no hay grupos con al menos cinco autores y dos recomendaciones comparables por autor. Faltan datos; no se estiman tamaños."),
      h("details",{class:"dev-method"},h("summary",null,"Usuarios de GameTrack, contados por separado"),
        h("p",null,`${number(data.gametrack_users)} usuarios con interacciones en los juegos del informe.`),
        data.gametrack_users>0 && h("ul",null,Object.entries(data.coverage).map(([key,n])=>h("li",null,`${coverageNames[key]||key}: ${number(n)} usuarios.`))),
        data.gametrack_users<data.minimum_group && h("p",null,"Faltan interacciones suficientes de usuarios de GameTrack para publicar sus grupos."),
        gtGroups.map(g=>h("p",null,`${g.label}: ${number(g.size)} de ${number(g.denominator)} usuarios. ${g.criterion} Fuente: ${g.source}.`))),
      h("details",{class:"dev-method"},h("summary",null,"Procedencia y alcance"),h("p",null,s.source || "Sin historiales consultados."),h("p",null,s.selection || ""),
        h("p",null,"La recuperación de autores antiguos consulta reseñas en español y sólo acepta coincidencias con el identificador original. Los historiales públicos pueden incluir otros idiomas. No usamos horas jugadas para inferir satisfacción."),
        data.limitations.map(text=>h("p",null,text))));
  }
  async function collect() {
    if(running || !scope) return;
    running=true;failed=false;notice="Consultando recomendaciones públicas de otros juegos…";render();
    try {
      for(let step=0;step<16;step++) {
        if(!root.isConnected) break;
        const result=await api.collectAudience(scope);data=result.audience;
        notice=result.status==="busy"?"Hay otra consulta en curso. Los resultados guardados ya están disponibles; podés volver a consultar en unos momentos.":`Consultados ${number(data.steam_histories.checked_authors)} historiales de ${number(data.steam_histories.selected_authors)} autores de la muestra.`;
        render();
        if(result.status!=="more" || !root.isConnected) break;
      }
      if(missing()) notice+=" Podés continuar la consulta para ampliar la cobertura.";
    } catch(error) {failed=true;notice=`No pudimos completar la consulta: ${error.message}. Podés reintentar; se conserva lo ya consultado.`;}
    finally {running=false;render();}
  }
  root.load=()=>{if(!started){started=true;queueMicrotask(()=>{if(root.isConnected && missing()) collect();else if(!root.isConnected) started=false;});}};
  render();return root;
}
