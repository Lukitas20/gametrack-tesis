/* La misma lista y acciones de Steam en Perfil y en Amigos. */
import { api } from "../api.js";
import { state } from "../store.js";
import { h, icon, initials, mount, emptyState, spinnerBlock, toast } from "../ui.js";
import { openInviteDialog } from "./invite.js";

export function steamFriendsList(data, { onChange = () => {}, heading = "Amigos de Steam" } = {}) {
  const owner = state.user?.id;
  const friends = data.friends;
  const results = h("div", { "aria-live": "polite" });
  let search = "", shown = 12;
  const query = h("input", { class: "input", type: "search", placeholder: "Buscar amigos de Steam…", "aria-label": "Buscar amigos de Steam", onInput: event => { search = event.target.value; shown = 12; render(); } });
  const notices = {
    private: "Tu lista es privada en Steam. Podés cambiarlo desde la privacidad de tu perfil.",
    unavailable: friends.updated_at ? "Steam no respondió. Mostramos la última lista guardada." : "No pudimos consultar tus amigos. Volvé a intentarlo en un momento.",
    not_configured: "La consulta de amigos de Steam todavía no está habilitada.",
  };
  function card(friend) {
    const relation = friend.relationship.state;
    let action;
    if (!friend.account) action = h("button", { class: "btn btn-sm", onClick: () => openInviteDialog(friend.name) }, icon("plus", 13), "Invitar a GameTrack");
    else if (relation === "accepted") action = h("span", { class: "chip" }, icon("check", 13), "Amigos en GameTrack");
    else if (relation === "outgoing") action = h("span", { class: "chip" }, icon("clock", 13), "Solicitud enviada");
    else action = h("button", { class: "btn btn-sm btn-primary", onClick: async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        if (relation === "incoming") await api.acceptFriend(friend.relationship.request_id);
        else await api.requestFriend(friend.account.username);
        if (state.user?.id !== owner) return;
        toast(relation === "incoming" ? "¡Ya son amigos!" : "Solicitud enviada");
        await onChange();
      } catch (error) { toast(error.message, "error"); button.disabled = false; }
    } }, relation === "incoming" ? "Aceptar solicitud" : "Agregar amigo");
    const avatar = h("span", { class: "avatar steam-friend-avatar" }, initials(friend.name));
    return h("article", { class: "steam-friend" },
      h("a", { class: "steam-friend-person", href: friend.profile_url, target: "_blank", rel: "noopener noreferrer" },
        friend.avatar ? h("img", { src: friend.avatar, width: "46", height: "46", alt: "", loading: "lazy", onError: event => event.currentTarget.replaceWith(avatar) }) : avatar,
        h("div", null, h("strong", null, friend.name), h("p", { class: "steam-caption", dataset: { online: String(friend.online === true) } },
          friend.playing ? `Jugando a ${friend.playing}` : friend.online === true ? "En línea en Steam" : friend.online === false ? "Desconectado en Steam" : "Estado no disponible"))),
      h("p", { class: "steam-friend-account" }, friend.account ? "También está en GameTrack" : "Todavía no vinculó Steam en GameTrack"),
      h("div", { class: "steam-friend-action" }, action));
  }
  function render() {
    const people = friends.items.filter(friend => `${friend.name} ${friend.account?.username || ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
    mount(results, h("p", { class: "steam-result-count" }, `${people.length} amigos`),
      people.length ? h("div", { class: "steam-friend-grid" }, people.slice(0, shown).map(card)) : emptyState(
        search ? "No encontramos ese amigo" : friends.status === "ok" ? "Tu lista de Steam está vacía" : "Lista no disponible",
        search ? "Probá con otro nombre." : "Podés invitar a tus amigos con tu enlace igualmente."),
      people.length > shown && h("button", { class: "btn steam-more", onClick: () => { shown += 12; render(); } }, "Mostrar más amigos"));
  }
  render();
  return h("div", { class: "steam-friends-shared" },
    h("div", { class: "steam-section-head" }, h("div", null, h("p", { class: "eyebrow" }, "TU COMUNIDAD"), h("h2", null, heading), h("p", { class: "muted" }, "Los de siempre, también acá. Sumalos a GameTrack para descubrir qué jugar juntos.")),
      h("button", { class: "btn btn-sm", onClick: () => openInviteDialog() }, icon("plus", 14), "Mi enlace de invitación")),
    notices[friends.status] && h("p", { class: "steam-notice", role: "status" }, notices[friends.status], friends.status === "private" && h("a", { href: "https://steamcommunity.com/my/edit/settings", target: "_blank", rel: "noopener noreferrer" }, " Revisar privacidad ↗")),
    friends.partial_profiles && h("p", { class: "steam-notice" }, "Algunos perfiles de Steam no tienen nombre o avatar disponible."),
    query, results);
}

export function steamFriendsSection(user, { onChange } = {}) {
  const body = h("div");
  const element = h("section", { class: "friends-steam-section", "aria-label": "Amigos de Steam" }, body);
  const owner = user.id;
  let revision = 0, data = null;
  const refreshButton = h("button", { class: "btn btn-sm", onClick: () => refresh(true) }, icon("refresh", 14), "Sincronizar Steam");
  async function refresh(force = false) {
    const current = ++revision;
    refreshButton.disabled = true;
    if (!data) mount(body, spinnerBlock("Cargando amigos de Steam…"));
    try {
      data = await (force ? api.syncSteamProfile() : api.steamProfile());
      if (current !== revision || state.user?.id !== owner) return;
      mount(body, steamFriendsList(data, { onChange: onChange || (() => refresh()) }));
    } catch (error) {
      if (current !== revision || state.user?.id !== owner) return;
      mount(body, emptyState("No pudimos cargar los amigos de Steam", error.message,
        h("button", { class: "btn", onClick: () => refresh() }, "Reintentar")));
    } finally { if (current === revision) refreshButton.disabled = false; }
  }
  element.append(h("div", { class: "steam-friends-footer" }, refreshButton,
    h("span", { class: "muted" }, "Las solicitudes se aceptan en GameTrack.")));
  return { element, refresh };
}
