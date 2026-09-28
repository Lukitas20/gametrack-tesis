import { navigate } from "../router.js";
import { DEMO_ACCOUNTS, DEMO_PASSWORD, login, register, loginDestination, displayName } from "../store.js";
import { h, icon, toast } from "../ui.js";

function steamIcon() {
  return h("span", { "aria-hidden": "true", html: '<svg viewBox="0 0 24 24" width="25" height="25" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="16.5" cy="7.5" r="5"/><circle cx="16.5" cy="7.5" r="2.7"/><circle cx="6.5" cy="17.5" r="3.7"/><path d="m9.8 19 8-6.7M6.8 13.8l4.8-6M0 13l7.6 3.2a1.9 1.9 0 0 1-1.5 3.5L0 17"/></svg>' });
}

export function accountsView({ query = new URLSearchParams() } = {}) {
  const invite = query.get("invitacion");
  if (invite && /^[A-Za-z0-9_-]{32,64}$/.test(invite)) sessionStorage.setItem("gametrack.invite", invite);
  let mode = query.get("modo") === "registro" ? "register" : "login";
  let busy = false;
  const panel = h("section", { class: "auth-panel", "aria-label": "Acceso a GameTrack" });
  function renderForm() {
    const creating = mode === "register";
    const error = h("p", { class: "auth-error", role: "alert", hidden: true });
    const report = (message) => { error.textContent = message; error.hidden = false; };
    const errors = {
      cancelled: "Cancelaste el acceso con Steam. Podés volver a intentarlo.",
      invalid: "No pudimos verificar el acceso con Steam. Volvé a intentarlo.",
      expired: "El intento de acceso venció. Iniciá nuevamente con Steam.",
      unavailable: "Steam no respondió a tiempo. Intentá otra vez en unos minutos.",
      conflict: "Esta cuenta tiene una vinculación anterior. Entrá con tu usuario y verificá Steam desde tu perfil.",
      linked: "Esa cuenta de Steam ya está vinculada a otro usuario de GameTrack.",
      inactive: "Esta cuenta está desactivada.",
    };
    if (query.has("steam_error")) report(errors[query.get("steam_error")] || errors.invalid);
    const tabs = h("div", { class: "auth-tabs", role: "group", "aria-label": "Tipo de acceso" });
    for (const [value, label] of [["login", "Iniciar sesión"], ["register", "Crear cuenta"]]) {
      tabs.append(h("button", { type: "button", class: mode === value ? "active" : "", "aria-pressed": String(mode === value), onClick: () => {
        if (busy || mode === value) return;
        mode = value; query.delete("steam_error");
        history.replaceState(null, "", `#/cuentas${mode === "register" ? "?modo=registro" : ""}`);
        renderForm(); panel.querySelector("h2").focus();
      } }, label));
    }
    const username = h("input", { id: "auth-username", name: "username", placeholder: "Tu nombre de usuario", autocomplete: "username", required: true, minlength: creating ? "3" : null, maxlength: "50", autocapitalize: "none", spellcheck: "false" });
    const email = h("input", { id: "auth-email", name: "email", type: "email", placeholder: "vos@ejemplo.com", autocomplete: "email", required: true, maxlength: "255" });
    const password = h("input", { id: "auth-password", name: "password", type: "password", placeholder: creating ? "Creá una contraseña" : "Tu contraseña", autocomplete: creating ? "new-password" : "current-password", required: true, minlength: creating ? "8" : null, maxlength: "128", "aria-describedby": creating ? "auth-password-help" : null });
    const reveal = h("button", { class: "auth-reveal", type: "button", "aria-label": "Mostrar contraseña", "aria-pressed": "false", onClick: () => {
      const show = password.type === "password"; password.type = show ? "text" : "password";
      reveal.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
      reveal.setAttribute("aria-pressed", String(show)); reveal.textContent = show ? "Ocultar" : "Mostrar";
    } }, "Mostrar");
    const confirm = h("input", { id: "auth-confirm", name: "confirm", type: "password", placeholder: "Repetí tu contraseña", autocomplete: "new-password", required: true, maxlength: "128" });
    const strength = h("div", { class: "auth-strength", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"));
    password.addEventListener("input", () => {
      const n = password.value.length;
      strength.dataset.level = String(n >= 16 ? 4 : n >= 12 ? 3 : n >= 8 ? 2 : n ? 1 : 0);
      confirm.setCustomValidity("");
    });
    confirm.addEventListener("input", () => confirm.setCustomValidity(""));
    const field = (label, input, extra) => h("div", { class: "auth-field" }, h("label", { for: input.id }, label), extra || input);
    const submit = h("button", { class: "auth-submit", type: "submit" }, creating ? "Crear mi cuenta" : "Iniciar sesión", icon("chevron", 18));
    const form = h("form", { class: "auth-form", "aria-labelledby": "auth-title", onSubmit: async (event) => {
      event.preventDefault(); if (busy) return;
      if (creating && password.value !== confirm.value) { confirm.setCustomValidity("Las contraseñas no coinciden."); confirm.reportValidity(); return; }
      error.hidden = true; busy = true; form.setAttribute("aria-busy", "true");
      panel.querySelectorAll("button, input").forEach(el => { el.disabled = true; });
      submit.textContent = creating ? "Creando tu cuenta…" : "Entrando…";
      try {
        const user = creating ? await register({ username: username.value.trim(), email: email.value.trim(), password: password.value }) : await login(username.value.trim(), password.value);
        toast(creating ? `¡Bienvenido a GameTrack, ${displayName(user)}!` : `¡Hola, ${displayName(user)}!`);
        navigate(loginDestination(user));
      } catch (failure) { report(failure instanceof TypeError ? "No pudimos conectar con GameTrack. Revisá tu conexión e intentá de nuevo." : failure.message); }
      finally {
        busy = false; form.removeAttribute("aria-busy");
        panel.querySelectorAll("button, input").forEach(el => { el.disabled = false; });
        submit.replaceChildren(creating ? "Crear mi cuenta" : "Iniciar sesión", icon("chevron", 18));
      }
    } }, field("Usuario", username), creating && field("Email", email),
      field("Contraseña", password, h("div", { class: "auth-password-wrap" }, password, reveal)),
      creating && h("div", null, strength, h("p", { class: "auth-field-help", id: "auth-password-help" }, "Usá al menos 8 caracteres; una frase larga es mejor.")),
      creating && field("Confirmá tu contraseña", confirm), submit);
    const demos = h("details", { class: "auth-demo" }, h("summary", null, "Probar una cuenta demo", icon("chevronDown", 13)), h("p", null, "Explorá con un perfil de demostración:"));
    for (const account of DEMO_ACCOUNTS) demos.append(h("button", { type: "button", onClick: async (event) => {
      if (busy) return; busy = true; const button = event.currentTarget; button.disabled = true;
      try { const user = await login(account.username, DEMO_PASSWORD); navigate(loginDestination(user)); }
      catch (failure) { report(failure.message); } finally { busy = false; button.disabled = false; }
    } }, account.role === "desarrollador" ? "Desarrollador" : account.username === "nuevo.demo" ? "Jugador nuevo" : "Jugador", icon("chevron", 13)));
    panel.replaceChildren(tabs,
      h("div", { class: "auth-form-heading" },
        h("h2", { id: "auth-title", tabindex: "-1" }, creating ? "Creá tu cuenta" : "Bienvenido de nuevo")),
      h("a", { class: "auth-steam", href: "/api/v1/auth/steam/start", onClick: e => { if (busy) e.preventDefault(); } }, steamIcon(), h("span", null, "Continuar con Steam"), icon("chevron", 17)),
      h("div", { class: "auth-divider" }, h("span", null, "o con tu usuario")),
      error, form, demos);

  }
  renderForm();
  return h("div", { class: "auth-page auth-minimal" },
    h("header", { class: "auth-header" },
      h("a", { class: "auth-brand", href: "#/cuentas" }, h("img", { src: "/assets/brand/logo.png", width: "42", height: "42", alt: "" }), "GameTrack"),
      h("a", { class: "auth-explore", href: "#/catalogo" }, "Explorar catálogo", icon("chevron", 15))),
    h("div", { class: "auth-layout" }, panel),
    h("footer", { class: "auth-footer" }, "Tu próxima partida empieza acá."));
}
