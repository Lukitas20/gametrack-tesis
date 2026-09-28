/* La amistad aceptada habilita cruzar perfiles, sin publicar el historial. */
import { api } from "../api.js";
import { requiresLogin } from "../components.js";
import { isLoggedIn, isDeveloper, state } from "../store.js";
import { h, icon, initials, emptyState, spinnerBlock, toast, openModal, modalHead } from "../ui.js";
import { steamFriendsSection } from "./steam-friends.js";
import { startNewQuiz } from "./quiz.js";
import { openInviteDialog } from "./invite.js";

export async function friendsView() {
  if (!isLoggedIn()) return requiresLogin("Iniciá sesión para agregar amigos y descubrir qué jugar juntos.");
  if (isDeveloper()) return emptyState("Amigos es una función para jugadores", "Entrá con un perfil de jugador para cruzar gustos.");
  const content = h("div");
  const username = h("input", { class: "input", placeholder: "Nombre de usuario exacto", required: true,
    maxlength: "50", autocomplete: "off", id: "friend-username" });
  const send = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus", 14), "Enviar solicitud");
  let revision = 0;
  const owner = state.user.id;
  const steam = state.user.steam_verified ? steamFriendsSection(state.user, { onChange: load }) : null;

  function person(user) {
    return h("div", { class: "friend-person" },
      h("span", { class: "avatar", "aria-hidden": "true" }, initials(user.username)),
      h("div", null, h("strong", null, user.full_name || user.username),
        h("p", { class: "muted" }, `@${user.username}`)));
  }

  function action(label, operation, primary = false) {
    const button = h("button", { class: `btn btn-sm ${primary ? "btn-primary" : ""}`, onClick: async () => {
      button.disabled = true;
      try { await operation(); await load(); }
      catch (error) { toast(error.message, "error"); button.disabled = false; }
    } }, label);
    return button;
  }

  function remove(user) {
    openModal(close => h("div", null,
      modalHead(`Quitar a @${user.username}`, "Ya no podrán incluirse en recomendaciones cruzadas hasta aceptar una nueva solicitud.", close),
      h("div", { class: "discovery-actions" },
        h("button", { class: "btn", onClick: close }, "Conservar amistad"),
        action("Quitar amigo", async () => { await api.removeFriend(user.id); close(); }, true))));
  }

  async function load() {
    const current = ++revision;
    steam?.refresh();
    content.replaceChildren(spinnerBlock("Cargando amigos…"));
    try {
      const data = await api.friends();
      if (current !== revision || state.user?.id !== owner) return;
      content.replaceChildren(
        h("section", { class: "section" }, h("h2", null, `Tus amigos · ${data.friends.length}`),
          data.friends.length ? h("div", { class: "friend-list" }, data.friends.map(user =>
            h("article", { class: "friend-row" }, person(user), h("div", { class: "row", style: { gap: "8px" } },
              h("button", { class: "btn btn-primary btn-sm", onClick: startNewQuiz }, "Jugar juntos"),
              h("button", { class: "btn btn-ghost btn-sm", onClick: () => remove(user), "aria-label": `Quitar a ${user.username}` }, "Quitar")))))
            : emptyState("Todavía no hay amigos", "Enviá una solicitud por nombre de usuario. Cuando la acepten, podrás elegirlos en ¿Qué jugamos?")),
        h("section", { class: "section" }, h("h2", null, `Solicitudes recibidas · ${data.incoming.length}`),
          h("div", { class: "friend-list" }, data.incoming.map(request => h("article", { class: "friend-row" },
            person(request.user), h("div", { class: "row", style: { gap: "8px" } },
              action("Aceptar", () => api.acceptFriend(request.id), true),
              action("Rechazar", () => api.cancelFriendRequest(request.id)))))),
          !data.incoming.length ? h("p", { class: "muted" }, "No hay solicitudes pendientes.") : null),
        h("section", { class: "section" }, h("h2", null, `Solicitudes enviadas · ${data.outgoing.length}`),
          h("div", { class: "friend-list" }, data.outgoing.map(request => h("article", { class: "friend-row" },
            person(request.user), action("Cancelar solicitud", () => api.cancelFriendRequest(request.id))))),
          !data.outgoing.length ? h("p", { class: "muted" }, "No hay solicitudes pendientes.") : null));
    } catch (error) {
      if (current !== revision) return;
      content.replaceChildren(emptyState("No pudimos cargar los amigos", error.message,
        h("button", { class: "btn", onClick: load }, "Reintentar")));
    }
  }

  load();
  return h("div", { class: "friends-shell" },
    h("div", { class: "view-head" }, h("div", null, h("p", { class: "eyebrow" }, "Mejor en compañía"),
      h("h1", null, "Amigos"), h("p", null, "Crucen sus gustos para encontrar una próxima partida en común.")),
      h("div", { class: "row", style: { gap: "8px" } },
        h("button", { class: "btn btn-sm", onClick: () => openInviteDialog() }, "Invitar con un enlace"))),
    h("section", { class: "card" },
      h("p", null, "Tu usuario: ", h("strong", null, state.user.username)),
      h("form", { class: "friend-form", onSubmit: async event => {
        event.preventDefault();
        if (!username.value.trim() || send.disabled) return;
        send.disabled = true;
        try { await api.requestFriend(username.value.trim()); username.value = ""; toast("Solicitud enviada"); await load(); }
        catch (error) { toast(error.message, "error"); }
        finally { send.disabled = false; }
      } }, h("label", { for: "friend-username" }, "Agregar un amigo"),
      h("div", { class: "discovery-actions" }, username, send)),
      h("p", { class: "discovery-control-note" }, "Aceptar una amistad permite incluir tus gustos en recomendaciones del grupo. Se muestra una afinidad estimada, sin publicar tu historial completo.")),
    content, steam?.element);
}
