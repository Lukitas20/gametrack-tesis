import { api } from "../api.js";
import { navigate } from "../router.js";
import { isLoggedIn, state } from "../store.js";
import { emptyState, h, icon, openModal, modalHead, toast } from "../ui.js";

export async function openInviteDialog(name = "tus amigos") {
  try {
    const invite = await api.createInvite();
    openModal(close => {
      const input = h("input", { class: "input", value: invite.url, readonly: true, "aria-label": "Enlace de invitación", onFocus: event => event.target.select() });
      const copy = h("button", { class: "btn btn-primary", onClick: async () => {
        try { await navigator.clipboard.writeText(invite.url); copy.textContent = "¡Enlace copiado!"; }
        catch { input.focus(); input.select(); toast("Seleccionamos el enlace para que lo copies."); }
      } }, "Copiar enlace");
      return h("div", null, modalHead(`Invitá a ${name}`, "Compartí este enlace. Tu amigo podrá crear una cuenta y enviarte una solicitud para jugar juntos.", close),
        h("div", { class: "steam-invite-link" }, input, copy),
        !invite.public && h("p", { class: "steam-notice" }, icon("info", 16), "GameTrack está abierto sólo en esta computadora. Para que un amigo abra el enlace desde la suya, la web tiene que estar publicada."),
        h("p", { class: "muted", style: { marginTop: "16px", fontSize: "12px" } }, "El enlace dura 30 días. No envía mensajes ni agrega amigos automáticamente."));
    });
  } catch (error) { toast(error.message, "error"); }
}

export async function inviteView({ params }) {
  const token = params.token;
  let invite;
  try { invite = await api.inspectInvite(token); }
  catch (error) { return emptyState("Invitación no disponible", error.message, h("a", { class: "btn", href: "#/cuentas" }, "Ir a GameTrack")); }
  const self = state.user?.username === invite.username;
  const action = !isLoggedIn()
    ? h("a", { class: "btn btn-primary", href: `#/cuentas?invitacion=${encodeURIComponent(token)}`, onClick: () => sessionStorage.setItem("gametrack.invite", token) }, "Crear cuenta o iniciar sesión", icon("chevron", 16))
    : h("button", { class: "btn btn-primary", disabled: self, onClick: async event => {
      const button = event.currentTarget; button.disabled = true;
      try {
        const result = await api.joinInvite(token);
        toast(result.state === "accepted" ? "¡Ya son amigos en GameTrack!" : "Solicitud enviada. Tu amigo debe aceptarla.");
        navigate("/amigos");
      } catch (error) { toast(error.message, "error"); button.disabled = false; }
    } }, self ? "Este es tu enlace de invitación" : `Conectar con ${invite.name}`);
  return h("section", { class: "card invitation-card" },
    h("img", { src: "/assets/brand/mascota.png", width: "140", height: "140", alt: "" }),
    h("p", { class: "eyebrow" }, "La próxima partida, juntos"),
    h("h1", null, `${invite.name} te invita a GameTrack`),
    h("p", { class: "muted" }, "Descubrí juegos, guardá tus favoritos y encontrá qué jugar con tus amigos."), action,
    h("p", { class: "steam-caption" }, "Conectar envía una solicitud o acepta una que tu amigo ya te envió. Abrir el enlace no comparte tu historial."));
}
