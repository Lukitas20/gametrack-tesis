/* Esqueleto de la aplicación: cabecera, navegación, cambio de rol y rutas. */

import { navigate, resolve, route, setNavigateHook, setNotFound, start } from "./router.js";
import {
  DEMO_ACCOUNTS,
  DEMO_PASSWORD,
  isDeveloper,
  isLoggedIn,
  login,
  logout,
  restore,
  state,
  subscribe,
} from "./store.js";
import { clear, emptyState, h, icon, initials, spinnerBlock, toast } from "./ui.js";
import { accountsView } from "./views/auth.js";
import { catalogView } from "./views/catalog.js";
import { developerGameView, developerView } from "./views/developer.js";
import { gameView } from "./views/game.js";
import { listsView } from "./views/lists.js";
import { openQuiz } from "./views/quiz.js";
import { recommendationsView } from "./views/recommendations.js";

const THEME_KEY = "gametrack.theme";

const main = document.getElementById("view");
const navSlot = document.getElementById("nav");
const actionsSlot = document.getElementById("actions");

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

const PLAYER_NAV = [
  ["/recomendaciones", "Para vos"],
  ["/catalogo", "Catálogo"],
  ["/listas", "Mis listas"],
];

const DEVELOPER_NAV = [
  ["/dev", "Panel"],
  ["/catalogo", "Catálogo"],
];

function renderNav(activePath = null) {
  const path = activePath || window.location.hash.slice(1).split("?")[0] || "/";
  const items = isDeveloper() ? DEVELOPER_NAV : PLAYER_NAV;

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
    h("div", { class: "menu-label" }, "Cambiar de perfil"),
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
              navigate(user.role === "desarrollador" ? "/dev" : "/recomendaciones");
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
      "Ver todos los perfiles",
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
    { class: "btn btn-sm", onClick: openQuiz, title: "Asistente de decisión" },
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
      class: "btn btn-sm",
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
          h(
            "span",
            {
              class: "avatar",
              style: { width: "20px", height: "20px", fontSize: "9px" },
            },
            initials(state.user.username),
          ),
          h("span", { class: "account-trigger-label" }, state.user.username),
        ]
      : [icon("user", 15), "Elegir perfil"],
    icon("chevronDown", 13),
  );
  accountWrap.appendChild(accountButton);

  actionsSlot.append(quizButton, themeButton, accountWrap);
}

/* ------------------------------------------------------------------ *
 * Montaje de vistas
 * ------------------------------------------------------------------ */

/** Envuelve un handler de vista: muestra carga, captura errores y monta. */
function view(loader) {
  return async (context) => {
    main.replaceChildren(spinnerBlock());
    try {
      const node = await loader(context);
      main.replaceChildren(node);
    } catch (error) {
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

route("/", view(async () => {
  // La raíz manda a cada rol a su lugar.
  if (!isLoggedIn()) return accountsView();
  navigate(isDeveloper() ? "/dev" : "/recomendaciones", { replace: true });
  return h("div");
}));

route("/cuentas", view(async () => accountsView()));
route("/catalogo", view(catalogView));
route("/recomendaciones", view(recommendationsView));
route("/listas", view(listsView));
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
  renderNav(path);
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
  main.replaceChildren(spinnerBlock("Iniciando GameTrack…"));

  await restore();

  if (!isLoggedIn() && !window.location.hash) {
    navigate("/cuentas", { replace: true });
  }
  start();
}

boot();
