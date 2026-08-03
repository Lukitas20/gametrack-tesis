/* Utilidades de interfaz: construcción de DOM, íconos, avisos y formato. */

/**
 * Constructor de elementos.
 *
 * Los hijos de tipo string se insertan como texto, nunca como HTML: el
 * contenido que viene de la base (reseñas, nombres de usuario) no puede
 * inyectar marcado.
 */
export function h(tag, props = null, ...children) {
  const element = document.createElement(tag);

  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;

      if (key === "class") element.className = value;
      else if (key === "dataset") Object.assign(element.dataset, value);
      else if (key === "style") Object.assign(element.style, value);
      else if (key.startsWith("on") && typeof value === "function") {
        element.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "html") element.innerHTML = value;
      else if (key === "for") element.htmlFor = value;
      else element.setAttribute(key, value === true ? "" : value);
    }
  }

  append(element, children);
  return element;
}

function append(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(parent, child);
    else if (child instanceof Node) parent.appendChild(child);
    else parent.appendChild(document.createTextNode(String(child)));
  }
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/* ------------------------------------------------------------------ *
 * Íconos
 * ------------------------------------------------------------------ */

const PATHS = {
  search: "M11 11l4 4M13 8a5 5 0 11-10 0 5 5 0 0110 0z",
  star: "M8 1.6l2 4.1 4.5.6-3.3 3.2.8 4.5L8 11.9l-4 2.1.8-4.5L1.5 6.3l4.5-.6z",
  sparkles: "M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4z M13 11l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z",
  chart: "M2 14h12M4.5 11.5v-4M8 11.5v-7M11.5 11.5v-2.5",
  user: "M8 8.5a3 3 0 100-6 3 3 0 000 6zM2.5 14.5c0-2.6 2.5-4.2 5.5-4.2s5.5 1.6 5.5 4.2",
  sun: "M8 11a3 3 0 100-6 3 3 0 000 6zM8 1v1.6M8 13.4V15M2.6 2.6l1.1 1.1M12.3 12.3l1.1 1.1M1 8h1.6M13.4 8H15M2.6 13.4l1.1-1.1M12.3 3.7l1.1-1.1",
  moon: "M13.5 9.6A5.8 5.8 0 016.4 2.5a5.8 5.8 0 107.1 7.1z",
  check: "M3 8.5l3.2 3.2L13 4.9",
  x: "M4 4l8 8M12 4l-8 8",
  plus: "M8 3v10M3 8h10",
  info: "M8 7.2v4.3M8 4.8v.6M14 8a6 6 0 11-12 0 6 6 0 0112 0z",
  alert: "M8 6v3.4M8 11.4v.4M7 2.6L1.6 12.2a1 1 0 00.9 1.5h11a1 1 0 00.9-1.5L9 2.6a1.2 1.2 0 00-2 0z",
  chevron: "M6 4l4 4-4 4",
  chevronDown: "M4 6l4 4 4-4",
  dice: "M5.5 6h.01M10.5 10h.01M8 8h.01M3 4.4C3 3.6 3.6 3 4.4 3h7.2c.8 0 1.4.6 1.4 1.4v7.2c0 .8-.6 1.4-1.4 1.4H4.4c-.8 0-1.4-.6-1.4-1.4z",
  list: "M5.5 4.5H14M5.5 8H14M5.5 11.5H14M2.4 4.5h.01M2.4 8h.01M2.4 11.5h.01",
  heart: "M8 13.5S2.5 10.2 2.5 6.4a2.9 2.9 0 015.5-1.3 2.9 2.9 0 015.5 1.3c0 3.8-5.5 7.1-5.5 7.1z",
  clock: "M8 4.6V8l2.2 1.6M14 8a6 6 0 11-12 0 6 6 0 0112 0z",
  quote: "M5.5 5.5C4 6 3 7.4 3 9.2c0 1.1.8 1.8 1.7 1.8.9 0 1.6-.6 1.6-1.5S5.7 8 4.9 8c0-.9.6-1.6 1.4-1.9zM11.5 5.5C10 6 9 7.4 9 9.2c0 1.1.8 1.8 1.7 1.8.9 0 1.6-.6 1.6-1.5s-.6-1.5-1.4-1.5c0-.9.6-1.6 1.4-1.9z",
  refresh: "M13.5 8a5.5 5.5 0 01-9.4 3.9M2.5 8a5.5 5.5 0 019.4-3.9M2.5 5v3h3M13.5 11V8h-3",
  logout: "M6 14H3.5A1.5 1.5 0 012 12.5v-9A1.5 1.5 0 013.5 2H6M10.5 11L14 8l-3.5-3M14 8H6",
  arrowLeft: "M13 8H3M7 4L3 8l4 4",
  trending: "M2 11.5l4-4 2.5 2.5L14 4.5M14 4.5h-3.5M14 4.5V8",
};

export function icon(name, size = 16, extraClass = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (extraClass) svg.setAttribute("class", extraClass);

  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", PATHS[name] || PATHS.info);
  if (name === "star" || name === "heart") {
    path.setAttribute("fill", "currentColor");
    path.setAttribute("stroke-width", "1");
  }
  svg.appendChild(path);
  return svg;
}

/* ------------------------------------------------------------------ *
 * Formato
 * ------------------------------------------------------------------ */

export const SENTIMENT_LABEL = {
  positivo: "Positivo",
  neutro: "Neutro",
  negativo: "Negativo",
};

export const ASPECT_LABEL = {
  jugabilidad: "Jugabilidad",
  graficos: "Gráficos",
  historia: "Historia",
  optimizacion: "Optimización",
};

export const STRATEGY_LABEL = {
  auto: "Automático",
  hibrido: "Híbrido",
  contenido: "Contenido",
  colaborativo: "Colaborativo",
  popularidad: "Popularidad",
};

export const SOURCE_LABEL = {
  hibrido: "Híbrido",
  contenido: "Basado en contenido",
  colaborativo: "Filtrado colaborativo",
  popularidad: "Popularidad",
};

export function signed(value, decimals = 2) {
  const number = Number(value ?? 0);
  return `${number >= 0 ? "+" : "−"}${Math.abs(number).toFixed(decimals)}`;
}

export function formatYear(released) {
  return released ? released.slice(0, 4) : "—";
}

export function pct(value, total) {
  if (!total) return "0%";
  return `${Math.round((value / total) * 100)}%`;
}

export function initials(text) {
  return (text || "?")
    .split(/[\s.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

/* Degradados decorativos para las portadas que el dataset no trae. No
 * codifican ningún dato, así que no comparten paleta con los gráficos: se
 * eligen de una lista fija indexada por el nombre para que un juego siempre
 * tenga el mismo. */
const COVER_GRADIENTS = [
  ["#1e3a5f", "#2d5f8a"],
  ["#3d2b52", "#6b4a7d"],
  ["#1f4740", "#357f6a"],
  ["#5a2f2a", "#8a5043"],
  ["#2b3a52", "#4a5f80"],
  ["#4a3b1f", "#7d6535"],
  ["#3a2140", "#66416b"],
  ["#1f3a3d", "#356a6e"],
];

export function coverGradient(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) % 100000;
  }
  const [from, to] = COVER_GRADIENTS[hash % COVER_GRADIENTS.length];
  return `linear-gradient(140deg, ${from}, ${to})`;
}

/* ------------------------------------------------------------------ *
 * Avisos y modales
 * ------------------------------------------------------------------ */

let toastStack = null;

export function toast(message, kind = "info") {
  if (!toastStack) {
    toastStack = h("div", { class: "toast-stack", role: "status", "aria-live": "polite" });
    document.body.appendChild(toastStack);
  }
  const node = h(
    "div",
    { class: "toast" },
    icon(kind === "error" ? "alert" : "check", 15),
    message,
  );
  toastStack.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity .25s";
    setTimeout(() => node.remove(), 260);
  }, 2600);
}

/**
 * Abre un modal. `render` recibe una función `close` para cerrarse a sí mismo.
 */
export function openModal(render, { wide = false } = {}) {
  const overlay = h("div", { class: "overlay" });

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    document.body.style.overflow = "";
  }

  function onKey(event) {
    if (event.key === "Escape") close();
  }

  const modal = h("div", {
    class: wide ? "modal modal-wide" : "modal",
    role: "dialog",
    "aria-modal": "true",
  });
  modal.appendChild(render(close));

  overlay.appendChild(modal);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener("keydown", onKey);
  document.body.style.overflow = "hidden";
  document.body.appendChild(overlay);

  // El foco entra al modal para que el teclado no quede detrás.
  const focusable = modal.querySelector("input, textarea, button, select");
  if (focusable) focusable.focus();

  return close;
}

export function modalHead(title, subtitle, close) {
  return h(
    "div",
    { class: "modal-head" },
    h("div", null, h("h2", null, title), subtitle && h("p", { class: "muted", style: { fontSize: "var(--fs-sm)", marginTop: "4px" } }, subtitle)),
    h("button", { class: "btn btn-icon btn-ghost", onClick: close, "aria-label": "Cerrar" }, icon("x", 16)),
  );
}

export function emptyState(title, message, action = null) {
  return h("div", { class: "empty" }, h("h3", null, title), h("p", null, message), action);
}

export function spinnerBlock(message = "Cargando…") {
  return h(
    "div",
    { class: "empty row", style: { justifyContent: "center" } },
    h("div", { class: "spinner" }),
    h("span", null, message),
  );
}
