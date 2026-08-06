/* Perfil: datos personales, géneros preferidos y cuenta de Steam vinculada. */

import { api } from "../api.js";
import { openOnboarding } from "./onboarding.js";
import { refreshUser, state } from "../store.js";
import { h, icon, initials, toast } from "../ui.js";
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
  if (user.steam_id) {
    return h(
      "section",
      { class: "card", style: { marginTop: "var(--s-4)" } },
      h("h2", { style: { marginBottom: "var(--s-3)" } }, "Cuenta de Steam"),
      h(
        "div",
        { class: "row", style: { gap: "var(--s-3)", alignItems: "center" } },
        user.steam_avatar_url
          ? h("img", { src: user.steam_avatar_url, alt: "", style: { width: "40px", height: "40px", borderRadius: "50%" } })
          : h("span", { class: "avatar" }, initials(user.steam_username || user.steam_id)),
        h(
          "div",
          null,
          h("span", { style: { display: "block", fontWeight: "600" } }, user.steam_username || "Cuenta vinculada"),
          h("span", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, user.steam_id),
        ),
      ),
    );
  }

  const steamId = h("input", {
    class: "input",
    placeholder: "SteamID64 (17 dígitos)",
    pattern: "\\d{17,20}",
  });

  return h(
    "section",
    { class: "card", style: { marginTop: "var(--s-4)" } },
    h("h2", { style: { marginBottom: "var(--s-2)" } }, "Vincular cuenta de Steam"),
    h(
      "p",
      { class: "muted", style: { marginBottom: "var(--s-3)" } },
      "Vinculá tu SteamID64 para completar tu perfil con tu avatar y nombre de Steam.",
    ),
    h(
      "div",
      { class: "row", style: { gap: "var(--s-2)" } },
      steamId,
      h(
        "button",
        {
          class: "btn btn-primary",
          onClick: async () => {
            try {
              await api.linkSteam(steamId.value.trim());
              await refreshUser();
              toast("Cuenta de Steam vinculada");
              renderAgain();
            } catch (error) {
              toast(error.message, "error");
            }
          },
        },
        "Vincular",
      ),
    ),
  );
}

let currentView = null;

async function renderAgain() {
  if (currentView) currentView.replaceChildren(...(await buildBody()).childNodes);
}

async function buildBody() {
  const user = state.user;
  const wrap = h("div");
  wrap.append(
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        { class: "row", style: { gap: "var(--s-4)", alignItems: "center" } },
        h("span", { class: "avatar", style: { width: "56px", height: "56px", fontSize: "var(--fs-lg)" } }, initials(user.username)),
        h(
          "div",
          null,
          h("h1", null, user.full_name || user.username),
          h("p", { class: "muted" }, `@${user.username} · ${user.role}`),
        ),
      ),
    ),
    dataSection(user),
    genresSection(user),
    user.role === "jugador" ? steamSection(user) : null,
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
