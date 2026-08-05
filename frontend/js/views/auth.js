/* Selector de cuenta para la demostración, con acceso manual como alternativa. */

import { navigate } from "../router.js";
import { DEMO_ACCOUNTS, DEMO_PASSWORD, login, state } from "../store.js";
import { h, icon, initials, toast } from "../ui.js";

async function enter(username, password = DEMO_PASSWORD) {
  const user = await login(username, password);
  toast(`Sesión iniciada como ${user.username}`);
  navigate(user.role === "desarrollador" ? "/dev" : "/inicio");
}

export function accountsView() {
  const wrap = h("div", { style: { maxWidth: "620px", margin: "0 auto" } });

  const cards = DEMO_ACCOUNTS.map((account) =>
    h(
      "button",
      {
        class: "account-card",
        onClick: async () => {
          try {
            await enter(account.username);
          } catch (error) {
            toast(error.message, "error");
          }
        },
      },
      // Las iniciales salen del usuario, no del nombre visible: dos cuentas
      // "Cuenta Demo …" darían la misma sigla, y además así coincide con el
      // avatar de la cabecera.
      h("span", { class: "avatar" }, initials(account.username)),
      h(
        "span",
        { style: { flex: "1", minWidth: "0" } },
        h(
          "span",
          { class: "row", style: { gap: "var(--s-2)" } },
          h("span", { class: "account-name" }, account.label),
          h(
            "span",
            { class: "badge-role" },
            icon(account.role === "desarrollador" ? "chart" : "user", 10),
            account.role,
          ),
          state.user?.username === account.username &&
            h("span", { class: "chip chip-accent" }, "Actual"),
        ),
        h(
          "span",
          { class: "account-note", style: { display: "block", marginTop: "2px" } },
          `${account.username} · ${account.note}`,
        ),
      ),
      icon("chevron", 16, "muted"),
    ),
  );

  // --- Acceso manual ---
  const username = h("input", { class: "input", placeholder: "usuario", autocomplete: "username" });
  const password = h("input", {
    class: "input",
    type: "password",
    placeholder: "contraseña",
    autocomplete: "current-password",
  });

  const form = h(
    "form",
    {
      onSubmit: async (event) => {
        event.preventDefault();
        try {
          await enter(username.value.trim(), password.value);
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
    h(
      "div",
      { class: "row", style: { gap: "var(--s-2)", alignItems: "stretch" } },
      username,
      password,
      h("button", { class: "btn btn-primary", type: "submit" }, "Entrar"),
    ),
  );

  wrap.append(
    h(
      "div",
      { class: "view-head", style: { display: "block", textAlign: "center", marginBottom: "var(--s-8)" } },
      h("p", { class: "eyebrow" }, "GameTrack"),
      h("h1", null, "Elegí un perfil"),
      h(
        "p",
        { style: { margin: "var(--s-3) auto 0" } },
        "Cada cuenta demuestra un escenario distinto de la plataforma. La contraseña de todas es ",
        h("code", null, DEMO_PASSWORD),
        ".",
      ),
    ),
    h("div", { style: { display: "grid", gap: "var(--s-3)" } }, cards),
    h("div", { class: "section" }, h("p", { class: "eyebrow" }, "O ingresá con tu cuenta"), form),
  );

  return wrap;
}
