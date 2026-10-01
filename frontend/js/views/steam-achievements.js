import { api } from "../api.js";
import { state } from "../store.js";
import { h, icon, mount } from "../ui.js";

const number = value => new Intl.NumberFormat("es-AR", {maximumFractionDigits:1}).format(value);
const date = value => new Date(value * 1000).toLocaleDateString("es-AR");

export function achievementProgress(data, compact = false) {
  const available = data?.percentage != null && data?.total > 0;
  return h("div", {class:`achievement-progress${compact ? " compact" : ""}`},
    h("div", {class:"achievement-progress-label"}, h("span", null, icon("trophy",14), available ? `${data.unlocked} de ${data.total} logros` : "Logros de Steam"),
      h("strong", null, available ? `${number(data.percentage)}%` : "—")),
    available && h("progress", {max:100, value:data.percentage, "aria-label":`${number(data.percentage)}% de logros desbloqueados`}),
    !compact && h("p", null, available && data.unlocked === data.total ? "Todos los logros desbloqueados." : "Logros desbloqueados · no indica el avance de la historia."));
}

export function steamAchievementPanel(appid, name, {onUpdate = () => {}, autoLoad = true} = {}) {
  const owner = state.user?.id;
  const panel = h("section", {class:"achievement-panel", "aria-label":`Logros de ${name}`});
  const body = h("div", {"aria-live":"polite"});
  let data = null, loading = false, revision = 0, filter = "all", shown = 12;
  const refresh = h("button", {class:"btn btn-ghost btn-sm", onClick:() => load(true), "aria-label":"Actualizar logros"}, icon("refresh",14), "Actualizar");
  const notices = {
    private: "Steam no comparte los logros de tu perfil. Podés revisar la privacidad de tus detalles de juegos.",
    unsupported: "Este juego no tiene logros de Steam disponibles.",
    not_configured: "La consulta de logros de Steam no está disponible por el momento.",
    unavailable: "Steam no respondió con todos los datos. Podés intentar de nuevo.",
  };
  function render() {
    if (!data) {
      mount(body, h("div", {class:"achievement-loading",role:"status"},icon("trophy",28), loading ? "Consultando tus logros en Steam…" : "Consultá tus logros para seguir tu progreso."));
      return;
    }
    const notice = notices[data.status];
    const hasProgress = data.percentage != null && data.total > 0;
    const filters = h("div", {class:"achievement-filters",role:"group","aria-label":"Filtrar logros"});
    for (const [key,label] of [["all","Todos"],["unlocked","Desbloqueados"],["locked","Pendientes"]]) {
      filters.append(h("button", {"aria-pressed":String(key === filter),onClick:() => {filter=key;shown=12;render();}},label));
    }
    const items = (data.items || []).filter(item => filter === "all" || item.unlocked === (filter === "unlocked"));
    const list = h("div", {class:"achievement-list"}, items.slice(0,shown).map(item => {
      const picture = h("span", {class:"achievement-icon","aria-hidden":"true"},icon(item.unlocked ? "trophy" : "lock",22));
      if (item.icon) picture.replaceChildren(h("img",{src:item.icon,alt:"",loading:"lazy",onError:() => mount(picture,icon("trophy",22))}));
      return h("article",{class:`achievement-row${item.unlocked ? " unlocked" : ""}`},picture,
        h("div",null,h("h4",null,item.name),item.description && h("p",null,item.description),
          h("small",null,item.unlocked ? `Desbloqueado${item.unlocked_at ? ` · ${date(item.unlocked_at)}` : ""}` : item.hidden ? "Se revela cuando lo desbloquees" : "Pendiente")),
        item.unlocked && h("span",{class:"achievement-check","aria-label":"Desbloqueado"},icon("check",14)));
    }));
    mount(body,
      notice && h("p",{class:"achievement-notice",role:"status"},icon("info",15),data.status === "unavailable" && hasProgress ? `Mostramos la última consulta del ${date(data.updated_at)}. Steam no respondió ahora.` : notice),
      hasProgress && achievementProgress(data),
      hasProgress && filters, hasProgress && list,
      hasProgress && !items.length && h("p",{class:"achievement-empty"},"No hay logros en este filtro."),
      hasProgress && items.length > shown && h("button",{class:"btn btn-ghost btn-sm",onClick:() => {shown+=12;render();}},"Ver más logros"),
      h("div",{class:"achievement-footer"},h("a",{href:data.community_url,target:"_blank",rel:"noopener noreferrer"},"Ver en Steam ↗"),
        data.updated_at && h("span",null,`Consultado ${date(data.updated_at)}`)),
      data.status === "private" && h("a",{class:"steam-caption",href:"https://steamcommunity.com/my/edit/settings",target:"_blank",rel:"noopener noreferrer"},"Revisar privacidad ↗"));
  }
  async function load(force=false) {
    if (loading) return;
    if (force && data?.next_refresh_at > Date.now()/1000) {
      body.querySelector('.achievement-cooldown')?.remove();
      body.prepend(h("p",{class:"achievement-notice achievement-cooldown",role:"status"},`Podés actualizar en ${Math.ceil(data.next_refresh_at-Date.now()/1000)} segundos.`));
      return;
    }
    const request=++revision; loading=true;refresh.disabled=true;render();
    try {
      const result=await (force ? api.syncSteamAchievements(appid) : api.steamAchievements(appid));
      if (owner !== state.user?.id || request !== revision) return;
      data=result;onUpdate(result);
    } catch (error) {
      if (owner !== state.user?.id || request !== revision) return;
      mount(body,h("p",{class:"achievement-notice",role:"status"},error.status === 404 ? "Este juego no está disponible en tu biblioteca de Steam." : error.message),
        h("button",{class:"btn btn-sm",onClick:() => load()},"Reintentar"));
      loading=false;refresh.disabled=false;return;
    }
    loading=false;refresh.disabled=false;render();
  }
  panel.append(h("header",{class:"achievement-heading"},h("div",null,h("p",{class:"eyebrow"},"TU PROGRESO"),h("h3",null,name || "Logros de Steam")),refresh),body);
  if (autoLoad) load(); else {render();body.append(h("button",{class:"btn btn-sm",onClick:() => load()},"Consultar mis logros"));}
  return panel;
}
