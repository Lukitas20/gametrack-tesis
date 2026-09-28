/* Perfil: datos personales, géneros preferidos y cuenta de Steam vinculada. */

import { api } from "../api.js";
import { openOnboarding } from "./onboarding.js";
import { refreshUser, state } from "../store.js";
import { steamDashboard } from "./steam-profile.js";
import { h, icon, initials, mount, toast } from "../ui.js";
import { requiresLogin } from "../components.js";
import { isLoggedIn } from "../store.js";

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
  return h(
    "section",
    { class: "card", style: { marginTop: "var(--s-4)" } },
    h(
      "div",
      { class: "row", style: { justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" } },
      h("h2", null, "Géneros preferidos"),
      h(
        "button",
        { class: "btn btn-sm", onClick: () => openOnboarding({ onDone: renderAgain }) },
        icon("sparkles", 13),
        "Cambiar",
      ),
    ),
    user.genres.length
      ? h(
          "div",
          { class: "row", style: { gap: "var(--s-2)" } },
          user.genres.map((genre) => h("span", { class: "chip" }, genre.name)),
        )
      : h("p", { class: "muted" }, "Todavía no elegiste géneros. Alimentan las recomendaciones por contenido antes de que tengas historial."),
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
  return h("section", { class: "card", style: { marginTop: "var(--s-4)" } },
    h("h2", null, "Cuenta de Steam"),
    h("p", { class: "muted", style: { margin: "var(--s-3) 0" } },
      verified ? `Cuenta verificada: ${user.steam_username || user.steam_id}`
        : "Verificá tu cuenta en Steam para iniciar sesión sin otra contraseña. Tus datos actuales se conservan."),
    verified ? h("span", { class: "chip" }, icon("check", 14), "Verificada con Steam") : button);
}
let currentView = null;

async function renderAgain() {
  if (currentView) currentView.replaceChildren(...(await buildBody()).childNodes);
}

async function buildBody() {
  const user = state.user;
  const wrap = h("div");
  mount(wrap,
    h(
      "div",
      { class: "profile-hero" },
      h(
        "div",
        { class: "profile-identity" },
        user.steam_avatar_url || user.avatar_url
          ? h("img", { src: user.steam_avatar_url || user.avatar_url, width: "78", height: "78", alt: "Tu avatar" })
          : h("span", { class: "avatar" }, initials(user.steam_username || user.username)),
        h(
          "div",
          null,
          h("p", { class: "eyebrow" }, "MI PERFIL"),
          h("h1", null, user.full_name || user.steam_username || user.username),
          h("p", { class: "muted" }, `@${user.username} · ${user.role}`),
          user.steam_verified && h("a", { class: "steam-caption", href: `https://steamcommunity.com/profiles/${user.steam_id}/`, target: "_blank", rel: "noopener noreferrer" }, "Steam verificado · Ver mi perfil ↗"),
        ),
      ),
    ),
    user.steam_verified && user.role === "jugador" ? steamDashboard(user) : null,
    h("div", { class: "profile-settings" }, dataSection(user), genresSection(user)),
    user.role === "jugador" && !user.steam_verified ? steamSection(user) : null,
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

  currentView = h("div");
  currentView.append(...(await buildBody()).childNodes);
  return currentView;
}
