/* Router por hash: funciona sirviendo archivos estáticos, sin reescrituras. */

const routes = [];
let notFound = null;
let onNavigate = null;

/**
 * @param {string} pattern Ruta con parámetros, p. ej. "/juego/:id".
 * @param {Function} handler Recibe ({ params, query }) y devuelve un nodo DOM.
 */
export function route(pattern, handler) {
  const names = [];
  const regex = new RegExp(
    `^${pattern.replace(/:[^/]+/g, (match) => {
      names.push(match.slice(1));
      return "([^/]+)";
    })}$`,
  );
  routes.push({ regex, names, handler });
}

export function setNotFound(handler) {
  notFound = handler;
}

export function setNavigateHook(hook) {
  onNavigate = hook;
}

export function navigate(path, { replace = false } = {}) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    resolve();
    return;
  }
  if (replace) window.history.replaceState(null, "", target);
  else window.location.hash = path;
  if (replace) resolve();
}

export function currentPath() {
  const raw = window.location.hash.slice(1) || "/";
  return raw.split("?")[0];
}

function currentQuery() {
  const raw = window.location.hash.slice(1);
  const index = raw.indexOf("?");
  return new URLSearchParams(index === -1 ? "" : raw.slice(index + 1));
}

export async function resolve() {
  const path = currentPath();
  const query = currentQuery();

  for (const { regex, names, handler } of routes) {
    const match = path.match(regex);
    if (!match) continue;
    const params = Object.fromEntries(names.map((name, index) => [name, match[index + 1]]));
    if (onNavigate) onNavigate(path);
    await handler({ params, query });
    return;
  }

  if (onNavigate) onNavigate(path);
  if (notFound) await notFound({ path });
}

export function start() {
  window.addEventListener("hashchange", () => {
    resolve();
    window.scrollTo({ top: 0 });
  });
  resolve();
}
