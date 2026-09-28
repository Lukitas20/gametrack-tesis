/* Esqueleto de la aplicación: cabecera, navegación, cambio de rol y rutas. */

import {
  currentPath,
  navigate,
  resolve,
  route,
  setNavigateHook,
  setNotFound,
  start,
} from "./router.js";
import {
  DEMO_ACCOUNTS,
  DEMO_PASSWORD,
  completeSteamLogin,
  displayName,
  loginDestination,
  isDeveloper,
  isLoggedIn,
  login,
  logout,
  restore,
  state,
  subscribe,
} from "./store.js";
import { clear, emptyState, h, icon, initials, magicLoader, toast } from "./ui.js";
import { accountsView } from "./views/auth.js";
import { catalogView } from "./views/catalog.js";
import { developerGameView, developerView } from "./views/developer.js";
import { gameView } from "./views/game.js";
import { listsView } from "./views/lists.js";
import { friendsView } from "./views/friends.js";
import { quizView, startNewQuiz } from "./views/quiz.js";
import { profileView } from "./views/profile.js";
import { inviteView } from "./views/invite.js";
import { ratingsView } from "./views/ratings.js";
import { recommendationsView } from "./views/recommendations.js";

const THEME_KEY = "gametrack.theme";

const main = document.getElementById("view");
const brandLink = document.getElementById("brand-link");
const navSlot = document.getElementById("nav");
const actionsSlot = document.getElementById("actions");
const headerSearchSlot = document.getElementById("header-search");

/* ------------------------------------------------------------------ *
 * Tema
 * ------------------------------------------------------------------ */

function currentTheme() {
  return document.documentElement.dataset.theme || null;
}

function applyTheme(theme) {
  if (theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  } else {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem(THEME_KEY);
  }
  renderActions();
}

function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored) document.documentElement.dataset.theme = stored;
}

function prefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function isDark() {
  const theme = currentTheme();
  return theme ? theme === "dark" : prefersDark();
}

/* ------------------------------------------------------------------ *
 * Navegación
 * ------------------------------------------------------------------ */

/** A dónde lleva "el inicio" según el rol. Antes esta decisión estaba
 * repetida en tres lugares (la raíz, el cambio de perfil y el ítem de nav);
 * ahora sale de acá, que es también lo que apunta el logo del header. */
function homePath() {
  return isDeveloper() ? "/dev" : "/recomendaciones";
}

// El catálogo no tiene su propio ítem de nav: se entra por la barra de
// búsqueda del header (ver renderHeaderSearch), que es persistente y no se
// reconstruye en cada navegación como esta lista.
//
// "Para vos" mantiene visible el acceso al recomendador desde cualquier vista.
const PLAYER_NAV = [
  ["/recomendaciones", "Para vos"],
  ["/listas", "Mis listas"],
  ["/amigos", "Amigos"],
  ["/valoraciones", "Mis valoraciones"],
  ["/perfil", "Perfil"],
];

const DEVELOPER_NAV = [
  ["/dev", "Panel"],
  ["/perfil", "Perfil"],
];

function renderNav(activePath = null) {
  const path = activePath || window.location.hash.slice(1).split("?")[0] || "/";
  const items = isDeveloper() ? DEVELOPER_NAV : PLAYER_NAV;

  // El logo apunta al inicio del rol actual en vez de a "/", así la URL que
  // queda es la canónica (`#/recomendaciones`) y no una que redirige.
  brandLink.setAttribute("href", `#${homePath()}`);

  clear(navSlot);
  for (const [href, label] of items) {
    const isActive = path === href || (href !== "/" && path.startsWith(`${href}/`));
    navSlot.appendChild(
      h(
        "a",
        {
          class: "nav-link",
          href: `#${href}`,
          "aria-current": isActive ? "page" : null,
        },
        label,
      ),
    );
  }
}

/* ------------------------------------------------------------------ *
 * Barra de búsqueda del header
 * ------------------------------------------------------------------ */

/**
 * Vive fuera de #nav a propósito: #nav se reconstruye en cada navegación
 * (ver renderNav), y un input ahí perdería el foco en cada tecla si escribir
 * dispara una navegación. Este input se crea una sola vez y sobrevive.
 */
let headerSearchInput = null;
let headerSearchTimer = null;

function renderHeaderSearch() {
  headerSearchInput = h("input", {
    type: "search",
    placeholder: "Buscar juegos…",
    "aria-label": "Buscar juegos",
    onFocus: () => {
      if (currentPath() !== "/catalogo") navigate("/catalogo");
    },
    onInput: (event) => {
      clearTimeout(headerSearchTimer);
      const value = event.target.value;
      headerSearchTimer = setTimeout(() => {
        const params = new URLSearchParams(window.location.hash.split("?")[1] || "");
        if (value) params.set("q", value);
        else params.delete("q");
        const query = params.toString();
        navigate(`/catalogo${query ? `?${query}` : ""}`, { replace: true });
      }, 280);
    },
  });

  clear(headerSearchSlot);
  headerSearchSlot.appendChild(h("div", { class: "header-search-wrap" }, icon("search", 15), headerSearchInput));
}

/** Mantiene el input sincronizado si se llega a /catalogo por otra vía (un
 * link con "?q=", el botón atrás). No lo pisa si el usuario lo está
 * escribiendo en este momento: perdería la tecla que acaba de tipear. */
function syncHeaderSearch() {
  if (!headerSearchInput || document.activeElement === headerSearchInput) return;
  const params = new URLSearchParams(window.location.hash.split("?")[1] || "");
  headerSearchInput.value = currentPath() === "/catalogo" ? params.get("q") || "" : "";
}

/* ------------------------------------------------------------------ *
 * Acciones de la cabecera
 * ------------------------------------------------------------------ */

let openMenu = null;

function closeMenu() {
  if (openMenu) {
    openMenu.remove();
    openMenu = null;
  }
}

document.addEventListener("click", (event) => {
  if (openMenu && !openMenu.parentElement?.contains(event.target)) closeMenu();
});

function accountMenu() {
  const menu = h("div", { class: "menu", role: "menu" });

  menu.append(
    h("div", { class: "menu-label" }, "Perfiles de demostración"),
    ...DEMO_ACCOUNTS.map((account) =>
      h(
        "button",
        {
          class: "menu-item",
          role: "menuitem",
          "aria-current": String(state.user?.username === account.username),
          onClick: async () => {
            closeMenu();
            try {
              const user = await login(account.username, DEMO_PASSWORD);
              toast(`Ahora sos ${user.username}`);
              navigate(homePath());
            } catch (error) {
              toast(error.message, "error");
            }
          },
        },
        h("span", { class: "avatar" }, initials(account.username)),
        h(
          "span",
          { style: { flex: "1", minWidth: "0" } },
          h("span", { style: { display: "block", fontWeight: "600" } }, account.label),
          h(
            "span",
            { class: "account-note", style: { display: "block" } },
            account.username,
          ),
        ),
        state.user?.username === account.username ? icon("check", 14) : null,
      ),
    ),
    h("div", { class: "menu-sep" }),
    h(
      "a",
      { class: "menu-item", href: "#/cuentas", onClick: closeMenu },
      icon("user", 15),
      "Acceso y registro",
    ),
    h(
      "a",
      { class: "menu-item", href: "/docs", target: "_blank", rel: "noopener" },
      icon("chart", 15),
      "Documentación de la API",
    ),
  );

  if (isLoggedIn()) {
    menu.append(
      h("div", { class: "menu-sep" }),
      h(
        "button",
        {
          class: "menu-item",
          onClick: () => {
            closeMenu();
            logout();
            toast("Sesión cerrada");
            navigate("/cuentas");
          },
        },
        icon("logout", 15),
        "Cerrar sesión",
      ),
    );
  }

  return menu;
}

function renderActions() {
  clear(actionsSlot);

  const quizButton = h(
    "button",
    {
      class: "btn btn-sm btn-primary quiz-launch",
      // Entrar por el encabezado es pedir una recomendación nueva, así que
      // descarta la tirada anterior en vez de volver a mostrarla.
      onClick: startNewQuiz,
      title: "Asistente de decisión",
      "aria-label": "¿Qué jugamos hoy? Empezar una recomendación",
    },
    icon("dice", 15),
    h("span", { class: "quiz-label" }, "¿Qué jugamos hoy?"),
  );

  const themeButton = h(
    "button",
    {
      class: "btn btn-icon btn-ghost",
      "aria-label": isDark() ? "Cambiar a tema claro" : "Cambiar a tema oscuro",
      onClick: () => applyTheme(isDark() ? "light" : "dark"),
    },
    icon(isDark() ? "sun" : "moon", 16),
  );

  const accountWrap = h("div", { class: "menu-wrap" });
  const accountButton = h(
    "button",
    {
      class: "btn btn-sm account-trigger",
      title: isLoggedIn() ? displayName() : "Iniciar sesión",
      "aria-label": isLoggedIn() ? `Cuenta de ${displayName()}` : "Iniciar sesión",
      "aria-haspopup": "menu",
      onClick: (event) => {
        event.stopPropagation();
        if (openMenu) {
          closeMenu();
          return;
        }
        openMenu = accountMenu();
        accountWrap.appendChild(openMenu);
      },
    },
    isLoggedIn()
      ? [
          (state.user.steam_verified && state.user.steam_avatar_url) || state.user.avatar_url
            ? h("img", { class: "avatar account-avatar", src: (state.user.steam_verified && state.user.steam_avatar_url) || state.user.avatar_url, alt: "", onError: event => event.currentTarget.replaceWith(h("span", { class: "avatar account-avatar" }, initials(displayName()))) })
            : h(
            "span",
            {
              class: "avatar",
              style: { width: "20px", height: "20px", fontSize: "9px" },
            },
            initials(displayName()),
          ),
          h("span", { class: "account-trigger-label" }, displayName()),
        ]
      : [icon("user", 15), "Iniciar sesión"],
    icon("chevronDown", 13),
  );
  accountWrap.appendChild(accountButton);

  actionsSlot.append(quizButton, themeButton, accountWrap);
}

/* ------------------------------------------------------------------ *
 * Montaje de vistas
 * ------------------------------------------------------------------ */

/** Envuelve un handler de vista: muestra carga, captura errores y monta. */
let navigationRevision = 0;

function view(loader) {
  return async (context) => {
    const revision = navigationRevision;
    const ownerId = state.user?.id ?? null;
    const isCurrent = () => revision === navigationRevision && (state.user?.id ?? null) === ownerId;
    // Es el loader de cada cambio de pantalla (incluida la ficha de un
    // juego, que puede tardar un par de segundos si hay que enriquecerla
    // desde Steam): el lugar con más chances de que alguien lo vea.
    main.replaceChildren(magicLoader("Cargando…"));
    try {
      const node = await loader(context);
      if (!isCurrent()) return;
      main.replaceChildren(node);
    } catch (error) {
      if (!isCurrent()) return;
      console.error(error);
      main.replaceChildren(
        emptyState(
          "Algo se rompió",
          error.message || "No pudimos cargar esta vista.",
          h("button", { class: "btn", onClick: () => resolve() }, "Reintentar"),
        ),
      );
    }
  };
}

// La raíz manda a cada rol a su lugar. La rama con sesión NO va envuelta en
// `view()` a propósito: `view` siempre monta lo que devuelve el handler, y
// como `navigate(..., { replace: true })` resuelve la ruta destino sin
// esperar, el `<div>` vacío que se devolvía acá se montaba ENCIMA de la
// vista recién renderizada y dejaba la pantalla en blanco hasta que la
// petición de la vista destino terminaba. Sin envoltorio no hay nada que
// pisar: redirige y punto.
route("/", async (context) => {
  if (!isLoggedIn()) {
    navigate("/cuentas", { replace: true });
    return;
  }
  navigate(homePath(), { replace: true });
});

route("/cuentas", view(async (context) => accountsView(context)));
route("/catalogo", view(catalogView));
route("/recomendaciones", view(recommendationsView));
route("/listas", view(listsView));
route("/amigos", view(friendsView));
route("/valoraciones", view(ratingsView));
route("/que-jugamos", view(quizView));
route("/perfil", view(profileView));
route("/invitacion/:token", view(inviteView));
route("/juego/:id", view(gameView));
route("/dev", view(developerView));
route("/dev/juego/:id", view(developerGameView));

setNotFound(
  view(async ({ path }) =>
    emptyState(
      "Esta página no existe",
      `No hay nada en ${path}.`,
      h("a", { class: "btn btn-primary", href: "#/" }, "Volver al inicio"),
    ),
  ),
);

setNavigateHook((path) => {
  navigationRevision += 1;
  document.body.classList.toggle("auth-active", path === "/cuentas");
  renderNav(path);
  syncHeaderSearch();
  closeMenu();
});

/* ------------------------------------------------------------------ *
 * Arranque
 * ------------------------------------------------------------------ */

subscribe(() => {
  renderNav();
  renderActions();
});

async function boot() {
  initTheme();
  renderActions();
  renderNav();
  renderHeaderSearch();
  main.replaceChildren(magicLoader("Iniciando GameTrack…"));

  await restore();

  if (window.location.hash === "#/steam-complete") {
    try {
      await completeSteamLogin();
      history.replaceState(null, "", `#${loginDestination(state.user, "/perfil")}`);
      toast("¡Listo! Verificamos tu cuenta con Steam.");
    } catch (error) {
      history.replaceState(null, "", "#/cuentas?steam_error=expired");
    }
  }

  if (!isLoggedIn() && !window.location.hash) {
    history.replaceState(null, "", "#/cuentas");
  }
  start();
}

boot();
