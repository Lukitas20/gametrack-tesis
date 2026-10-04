/* Perfil: datos personales, géneros preferidos y cuenta de Steam vinculada. */

import { api } from "../api.js";
import { openOnboarding } from "./onboarding.js";
import { isLoggedIn, refreshUser, state, displayName, updateSteamPreferences } from "../store.js";
import { steamDashboard } from "./steam-profile.js";
import { h, icon, initials, mount, toast } from "../ui.js";
import { requiresLogin } from "../components.js";

function field(label, input) {
  return h(
    "label",
    { class: "field", style: { display: "block", marginBottom: "var(--s-4)" } },
    h("span", { class: "muted", style: { display: "block", marginBottom: "var(--s-2)", fontSize: "var(--fs-sm)" } }, label),
    input,
  );
}

function dataSection(user) {
  const fullName = h("input", { class: "input", value: user.full_name || "" });
  const email = h("input", { class: "input", type: "email", value: user.email || "" });

  return h(
    "section",
    { class: "card", style: { marginTop: "var(--s-6)" } },
    h("h2", { style: { marginBottom: "var(--s-4)" } }, "Tus datos"),
    field("Nombre visible", fullName),
    field("Email", email),
    h(
      "button",
      {
        class: "btn btn-primary",
        onClick: async () => {
          try {
            await api.updateProfile({
              full_name: fullName.value.trim() || null,
              email: email.value.trim() || null,
            });
            await refreshUser();
            await renderAgain();
            toast("Datos actualizados");
          } catch (error) {
            toast(error.message, "error");
          }
        },
      },
      "Guardar",
    ),
  );
}

function genresSection(user) {
  const fromSteam = user.preferences_source === "steam";
  const useSteam = user.steam_verified && h("button", {
    class: "btn btn-sm btn-ghost",
    onClick: async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api.suggestSteamPreferences();
        await refreshUser();
        await renderAgain();
        toast("Gustos actualizados según tus juegos más jugados en Steam");
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
    },
  }, icon("refresh", 13), fromSteam ? "Actualizar desde Steam" : "Usar mis juegos de Steam");
  return h(
    "section",
    { class: "profile-genres" },
    h(
      "div",
      { class: "row", style: { justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" } },
      h("h2", null, "Mis gustos"),
      h("div", { class: "row", style: { gap: "var(--s-2)", flexWrap: "wrap" } }, useSteam, h(
        "button",
        { class: "btn btn-sm", onClick: () => openOnboarding({ onDone: renderAgain }) },
        icon("sparkles", 13),
        "Modificar",
      )),
    ),
    fromSteam && h("p", { class: "steam-caption", style: { marginBottom: "var(--s-3)" } }, "Asignados según tus juegos más jugados en Steam. Podés modificarlos cuando quieras."),
    user.genres.length
      ? h(
          "div",
          { class: "row", style: { gap: "var(--s-2)" } },
          user.genres.map((genre) => h("span", { class: "chip" }, genre.name)),
        )
      : h("p", { class: "muted" }, user.steam_verified ? "Tus juegos con tiempo registrado nos ayudan a identificar tus gustos. Si Steam no comparte tu biblioteca, también podés elegirlos." : "Elegí tus géneros para encontrar juegos más afines a vos."),
  );
}

function steamSection(user) {
  const verified = user.steam_verified;
  const button = h("button", {
    class: "btn btn-primary",
    onClick: async () => {
      button.disabled = true;
      try {
        const { url } = await api.startSteamLink();
        window.location.assign(url);
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
    },
  }, user.steam_id ? "Verificar con Steam" : "Vincular con Steam");
  return h("section", { class: "profile-steam-connect" },
    h("span", {class:"profile-connect-icon"},icon("trophy",26)),
    h("div", null,h("p", {class:"eyebrow"},"TODO TU PROGRESO"),h("h2", null, "Traé tu mundo de Steam"),
    h("p", { class: "muted", style: { margin: "var(--s-3) 0" } },
      verified ? `Cuenta verificada: ${user.steam_username || user.steam_id}`
        : "Tu biblioteca, horas, amigos y logros desbloqueados, en un solo lugar.")),
    verified ? h("span", { class: "chip" }, icon("check", 14), "Verificada con Steam") : button);
}
function profileActivity(user, heroMedia) {
  const root=h("section",{class:"profile-activity","aria-label":"Tu actividad en GameTrack"});
  root.append(h("p",{class:"steam-caption",role:"status"},"Cargando tu actividad…"));
  api.myRatings().then(rows => {
    if (state.user?.id !== user.id) return;
    const ratings=[...rows].sort((a,b) => new Date(b.created_at)-new Date(a.created_at));
    const favorite=[...rows].sort((a,b) => b.score-a.score).find(row => row.game.background_image);
    if (favorite) mount(heroMedia,h("img",{src:favorite.game.background_image,alt:"",onError:() => heroMedia.replaceChildren()}));
    const metrics=[["star",rows.length,"Valoraciones"],["heart",rows.filter(row => row.score >= 4).length,"Con buena nota"],["check",rows.filter(row => row.status === "completado").length,"Marcados completados"]];
    mount(root,h("div",{class:"profile-stat-strip"},metrics.map(([symbol,value,label]) => h("div",null,icon(symbol,18),h("strong",null,value),h("span",null,label)))),
      h("div",{class:"profile-section-heading"},h("div",null,h("p",{class:"eyebrow"},"TU HISTORIA EN GAMETRACK"),h("h2",null,"Últimos juegos valorados")),h("a",{href:"#/valoraciones",class:"btn btn-ghost btn-sm"},"Ver todos",icon("chevron",14))),
      ratings.length ? h("div",{class:"profile-game-rail"},ratings.slice(0,4).map(row => h("a",{href:`#/juego/${row.game_id}`,class:"profile-activity-game"},
        row.game.background_image ? h("img",{src:row.game.background_image,alt:"",loading:"lazy",onError:event => event.target.remove()}) : h("span",{class:"profile-cover-fallback"},icon("gamepad",34)),
        h("div",null,h("strong",null,row.game.name),h("span",null,icon("star",12),`${row.score}/5 en GameTrack`)))))
        : h("div",{class:"profile-activity-empty"},icon("gamepad",28),h("h3",null,"Tu próximo juego empieza acá"),h("p",null,"Valorá tus juegos para construir tu perfil y mejorar las recomendaciones."),h("a",{class:"btn btn-sm",href:"#/catalogo"},"Explorar juegos")));
  }).catch(() => mount(root,h("p",{class:"steam-caption"},"No pudimos cargar tu actividad."),h("a",{href:"#/valoraciones"},"Ver mis valoraciones")));
  return root;
}

function myReviewsSection(user) {
  const root=h("section",{class:"profile-written-reviews","aria-label":"Mis reseñas"});
  const body=h("div",{class:"profile-review-grid"});
  const status=h("p",{class:"steam-caption",role:"status"},"Cargando tus reseñas…");
  let offset=0;
  const more=h("button",{class:"btn btn-sm",onClick:load,hidden:true},"Ver más reseñas");
  root.append(h("div",{class:"profile-section-heading"},h("div",null,h("p",{class:"eyebrow"},"TU VOZ EN GAMETRACK"),h("h2",null,"Mis reseñas"))),
    h("p",{class:"steam-caption"},"Las reseñas que escribís se guardan acá y también aparecen en la ficha de cada juego."),body,status,more);
  async function load(){more.disabled=true;try{const rows=await api.myReviews({limit:6,offset});if(state.user?.id!==user.id)return;
    rows.forEach(review=>body.append(h("article",{class:"profile-written-review"},
      h("a",{class:"profile-review-game",href:`#/juego/${review.game_id}`},review.game.background_image?h("img",{src:review.game.background_image,alt:"",loading:"lazy",onError:event=>event.target.remove()}):icon("gamepad",25),h("strong",null,review.game.name)),
      h("h3",null,review.title||"Mi experiencia"),h("p",null,review.content),
      h("div",{class:"profile-review-footer"},h("small",null,new Date(review.created_at).toLocaleDateString("es-AR")),h("a",{class:"btn btn-ghost btn-sm",href:`#/juego/${review.game_id}?resena=editar`},"Editar mi reseña",icon("chevron",13))))));
    offset+=rows.length;more.hidden=rows.length<6;status.replaceChildren();
    if(offset===0)mount(status,"Todavía no escribiste una reseña. ",h("a",{href:"#/catalogo"},"Elegir un juego"));
  }catch{mount(status,"No pudimos cargar tus reseñas. ",h("button",{class:"btn btn-sm",onClick:load},"Reintentar"));}finally{more.disabled=false;}}
  load();return root;
}
let currentView = null;

async function renderAgain() {
  if (currentView) currentView.replaceChildren(...(await buildBody()).childNodes);
}

async function buildBody() {
  if (state.user.steam_verified) await refreshUser();
  const user = state.user;
  const wrap = h("div",{class:"player-profile"});
  const heroMedia=h("div",{class:"profile-hero-media","aria-hidden":"true"});
  const dashboard=user.steam_verified && user.role === "jugador" ? steamDashboard(user) : null;
  const tastes = genresSection(user);
  if (dashboard) dashboard.onData = data => {
    const recent=[...data.library.items].sort((a,b) => (b.last_played || 0)-(a.last_played || 0) || (b.minutes || 0)-(a.minutes || 0))[0];
    if (recent?.cover) mount(heroMedia,h("img",{src:recent.cover,alt:"",onError:() => heroMedia.replaceChildren()}));
    if (data.preferences && state.user?.id === user.id) {
      updateSteamPreferences(data.preferences);
      tastes.replaceChildren(...genresSection(state.user).childNodes);
    }
  };
  mount(wrap,
    h(
      "div",
      { class: "profile-hero" },
      heroMedia,
      h(
        "div",
        { class: "profile-identity" },
        user.steam_avatar_url || user.avatar_url
          ? h("img", { src: user.steam_avatar_url || user.avatar_url, width: "78", height: "78", alt: "Tu avatar" })
          : h("span", { class: "avatar" }, initials(displayName(user))),
        h(
          "div",
          null,
          h("p", { class: "eyebrow" }, "TU ESPACIO"),
          h("h1", null, displayName(user)),
          h("p", { class: "profile-status" },h("span",{class:"profile-status-dot"}),user.steam_verified ? "Steam conectado" : user.role === "jugador" ? "Jugador de GameTrack" : "Desarrollador"),
          user.steam_verified && h("a", { class: "steam-caption", href: `https://steamcommunity.com/profiles/${user.steam_id}/`, target: "_blank", rel: "noopener noreferrer" }, "Steam verificado · Ver mi perfil ↗"),
        ),
      ),
      h("div",{class:"profile-hero-actions"},
        h("a",{href:"#/que-jugamos",class:"btn btn-primary"},icon("gamepad",18),"¿Qué jugamos?",icon("chevron",14)),
        dashboard ? h("button",{class:"btn profile-secondary-action",onClick:() => {dashboard.showSection("achievements");dashboard.scrollIntoView({block:"start",behavior:"instant"});}},icon("trophy",16),"Mis logros")
          : h("a",{href:"#/listas",class:"btn profile-secondary-action"},icon("list",16),"Mis listas")),
    ),
    tastes,
    dashboard || (user.role === "jugador" ? profileActivity(user,heroMedia) : null),
    user.role === "jugador" ? myReviewsSection(user) : null,
    user.role === "jugador" && !user.steam_verified ? steamSection(user) : null,
    h("details",{class:"profile-settings-fold"},h("summary",null,icon("user",16),"Ajustes del perfil",icon("chevronDown",14)),h("div",{class:"profile-settings"},dataSection(user))),
  );
  return wrap;
}

export async function profileView() {
  if (!isLoggedIn()) {
    return h(
      "div",
      null,
      h("div", { class: "view-head" }, h("h1", null, "Perfil")),
      requiresLogin("Iniciá sesión para ver tu perfil."),
    );
  }

  currentView = h("div",{class:"profile-page player-profile"});
  currentView.append(...(await buildBody()).childNodes);
  return currentView;
}
