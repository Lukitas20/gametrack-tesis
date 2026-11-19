import { api } from "../api.js";
import { experienceChoices } from "../experience-choices.js";
import { requiresLogin } from "../components.js";
import { navigate } from "../router.js";
import { accountSetupRole, saveAccountSetup, isLoggedIn, loginDestination, refreshUser, state } from "../store.js";
import { h, icon, mount, toast } from "../ui.js";

export const STUDIO_DEMO_COPY = "Prototipo del espacio de estudio · sin suscripción ni cobros.";

export function experienceView({ query = new URLSearchParams() } = {}) {
  if (!isLoggedIn()) return requiresLogin("Iniciá sesión para configurar tu experiencia.");
  const user = state.user;
  const requested = query.get("tipo");
  let selectedRole = accountSetupRole(user, requested);
  const needsChoice = !selectedRole;
  let selectedGameId = user.analysis_game_id || null;
  let busy = false;
  let searchRevision = 0;
  let genresChanged = false;
  const selectedGenres = new Set((user.genres || []).map(genre => genre.id));
  const studio = h("input", { id: "experience-studio", class: "input", value: user.studio || "", maxlength: "120", autocomplete: "organization", placeholder: "Tu estudio o tu nombre como desarrollador" });
  const roleBody = h("div", { class: "experience-body" });
  const error = h("p", { class: "experience-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Continuar", icon("chevron", 16));
  const form = h("form", { class: "experience-form", onSubmit: async event => {
    event.preventDefault();
    if (busy || !selectedRole) return;
    if (selectedRole === "jugador" && genresChanged && !selectedGenres.size && user.genres.length) {
      error.textContent = "Elegí al menos un género para actualizar tus gustos.";
      error.hidden = false; return;
    }
    if (selectedRole === "desarrollador" && !studio.value.trim()) {
      studio.setCustomValidity("Indicá el nombre del estudio o del desarrollador independiente.");
      studio.reportValidity(); return;
    }
    busy = true; error.hidden = true; form.setAttribute("aria-busy", "true");
    const controls = [...form.querySelectorAll("button, input")];
    controls.forEach(control => { control.disabled = true; });
    try {
      const next = await saveAccountSetup({ role: selectedRole, complete_onboarding: true,
        ...(selectedRole === "desarrollador" ? { studio: studio.value.trim(), analysis_game_id: selectedGameId } : {}) });
      if (selectedRole === "jugador" && genresChanged && selectedGenres.size) {
        await api.setPreferences([...selectedGenres]);
        await refreshUser();
      }
      toast(selectedRole === "desarrollador" ? "Tu espacio de estudio está listo" : "Tu perfil de jugador está listo");
      navigate(loginDestination(next));
    } catch (failure) {
      error.textContent = failure.message; error.hidden = false;
    } finally {
      busy = false; form.removeAttribute("aria-busy");
      controls.forEach(control => { control.disabled = false; });
    }
  } });
  studio.addEventListener("input", () => studio.setCustomValidity(""));

  function renderPlayer() {
    const genres = h("div", { class: "experience-genres", "aria-label": "Tus géneros favoritos" });
    const status = h("p", { class: "muted", role: "status" }, "Cargando géneros…");
    async function load() {
      mount(status, "Cargando géneros…");
      try {
        const items = await api.genres();
        mount(genres, items.map(genre => h("button", { class: "chip", type: "button", "aria-pressed": String(selectedGenres.has(genre.id)), onClick: event => {
          genresChanged = true;
          if (selectedGenres.has(genre.id)) selectedGenres.delete(genre.id); else selectedGenres.add(genre.id);
          event.currentTarget.setAttribute("aria-pressed", String(selectedGenres.has(genre.id)));
        }, disabled: busy }, genre.name)));
        mount(status, items.length ? "Podés ajustar tus gustos más adelante desde tu perfil." : "Todavía no hay géneros disponibles. Podés continuar y elegirlos después.");
      } catch {
        mount(status, "No pudimos cargar los géneros. Podés continuar o ", h("button", { type: "button", class: "btn btn-sm", onClick: load }, "Reintentar"));
      }
    }
    mount(roleBody, h("h2", null, "Empecemos por tus gustos"),
      h("p", { class: "muted" }, user.preferences_source === "steam" ? "Partimos de tus juegos de Steam. Podés ajustar la selección." : "Elegí los géneros que te gustan para personalizar las recomendaciones. También podés hacerlo después."),
      genres, status);
    load();
  }

  function renderStudio() {
    studio.required = true;
    const input = h("input", { id: "experience-game", class: "input", type: "search", placeholder: "Nombre de un juego o desarrollador", maxlength: "200" });
    const results = h("div", { class: "experience-results", "aria-live": "polite" });
    const picked = h("div", { class: "experience-picked", "aria-live": "polite" });
    function showPicked(game) {
      mount(picked, h("span", null, icon("check", 15), game.name), h("button", { type: "button", class: "btn btn-sm btn-ghost", onClick: () => { selectedGameId = null; picked.replaceChildren(); } }, "Quitar"));
    }
    if (selectedGameId) {
      const id = selectedGameId;
      api.game(id).then(game => { if (selectedGameId === id) showPicked(game); }).catch(() => { if (selectedGameId === id) selectedGameId = null; });
    }
    async function search() {
      const revision = ++searchRevision;
      const search = input.value.trim();
      if (!search) { results.replaceChildren(); return; }
      mount(results, h("p", { role: "status" }, "Buscando juegos…"));
      try {
        const page = await api.games({ search, limit: 8 });
        if (revision !== searchRevision) return;
        mount(results, page.items.length ? page.items.map(game => h("button", { type: "button", class: "experience-game", onClick: () => {
          selectedGameId = game.id; showPicked(game); results.replaceChildren();
        } }, h("strong", null, game.name), h("small", null, game.developer || "Desarrollador sin informar"), icon("plus", 16)))
          : h("p", { class: "muted" }, "No encontramos juegos con ese nombre. Podés elegir uno más adelante."));
      } catch (failure) {
        if (revision === searchRevision) mount(results, h("p", { role: "alert" }, failure.message), h("button", { type: "button", class: "btn btn-sm", onClick: search }, "Reintentar"));
      }
    }
    input.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); search(); } });
    mount(roleBody, h("p", { class: "experience-demo" }, icon("info", 18), STUDIO_DEMO_COPY),
      h("label", { for: studio.id }, "Nombre del estudio o desarrollador independiente"), studio,
      h("h2", null, "¿Qué juego querés analizar primero?"),
      h("p", { class: "muted" }, "Elegí un juego propio o de referencia (opcional)."),
      h("label", { for: input.id }, "Buscar en el catálogo"),
      h("div", { class: "experience-search" }, input, h("button", { type: "button", class: "btn", onClick: search }, icon("search", 16), "Buscar")), picked, results,
      );
  }

  function renderRole() {
    searchRevision++;
    studio.required = false;
    if (selectedRole === "desarrollador") renderStudio();
    else if (selectedRole === "jugador") renderPlayer();
    else roleBody.replaceChildren();
    submit.textContent = !selectedRole ? "Continuar" : selectedRole === "desarrollador" ? "Abrir mi espacio de estudio" : "Ir a mis recomendaciones";
  }
  if (needsChoice) {
    form.append(experienceChoices({ value: selectedRole, onChange: role => { selectedRole = role; error.hidden = true; renderRole(); } }));
  }
  form.append(roleBody, error, submit);
  renderRole();
  return h("div", { class: "experience-page" },
    h("div", { class: "view-head" }, h("div", null, h("p", { class: "eyebrow" }, "TU CUENTA DE GAMETRACK"),
      h("h1", null, user.onboarding_step === "choose" ? "Elegí tu tipo de cuenta" : selectedRole === "desarrollador" ? "Configurá tu estudio" : "Personalizá tu perfil de jugador"),
      user.onboarding_step === "choose" && h("p", { class: "muted" }, "El tipo de cuenta queda definido al continuar."))),
    form);
}
