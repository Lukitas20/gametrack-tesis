/* Estado de sesión y cachés del catálogo. */

import { api, setToken } from "./api.js";

const STORAGE_KEY = "gametrack.session";

/** Cuentas creadas por el seed, para poder cambiar de rol en la demo. */
export const DEMO_ACCOUNTS = [
  {
    username: "jugador.demo",
    label: "Cuenta Demo Jugador",
    role: "jugador",
    note: "Recomendaciones según tus géneros y valoraciones. Probá IA local y compará estrategias.",
  },
  {
    username: "nuevo.demo",
    label: "Jugador nuevo",
    role: "jugador",
    note: "Perfil del dataset demo sin valoraciones iniciales: permite probar el arranque en frío.",
  },
  {
    username: "dev.demo",
    label: "Cuenta Demo Desarrollador",
    role: "desarrollador",
    note: "Panel de analítica del estudio con más juegos importados de Steam.",
  },
];

export const DEMO_PASSWORD = "demo1234";

const listeners = new Set();

export const state = {
  token: null,
  user: null,
  genres: [],
  tags: [],
  ratings: new Map(), // game_id -> score
};

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  for (const listener of listeners) listener(state);
}

export function isLoggedIn() {
  return Boolean(state.user);
}

/** El identificador de acceso no tiene por qué ser el nombre visible. */
export function displayName(user = state.user) {
  if (!user) return "Mi cuenta";
  if (user.steam_verified && user.steam_username?.trim()) return user.steam_username.trim();
  return user.full_name?.trim() || (user.steam_verified && /^steam_\d+$/.test(user.username) ? "Mi cuenta de Steam" : user.username);
}

export function isDeveloper() {
  return state.user?.role === "desarrollador";
}

function persist() {
  if (state.token) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: state.token }));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

async function loadRatings() {
  state.ratings = new Map();
  if (!state.user || state.user.role === "desarrollador") return;
  try {
    const rows = await api.myRatings();
    state.ratings = new Map(rows.map((row) => [row.game_id, row.score]));
  } catch {
    // Sin historial no se bloquea la sesión.
  }
}

/** Carga el catálogo base una sola vez por sesión. */
export async function loadCatalog() {
  if (state.genres.length) return;
  const [genres, tags] = await Promise.all([api.genres(), api.tags(3)]);
  state.genres = genres;
  state.tags = tags;
}

export async function login(username, password) {
  const data = await api.login(username, password);
  return acceptSession(data);
}

export async function register(data) {
  return acceptSession(await api.register(data));
}

export async function completeSteamLogin() {
  return acceptSession(await api.steamSession());
}

export function loginDestination(user, fallback = "/recomendaciones") {
  const invite = sessionStorage.getItem("gametrack.invite");
  sessionStorage.removeItem("gametrack.invite");
  if (invite && /^[A-Za-z0-9_-]{32,64}$/.test(invite)) return `/invitacion/${invite}`;
  return user.role === "desarrollador" ? "/dev" : fallback;
}

async function acceptSession(data) {
  state.token = data.access_token;
  state.user = data.user;
  setToken(state.token);
  persist();
  await loadRatings();
  emit();
  return data.user;
}

export function logout() {
  state.token = null;
  state.user = null;
  state.ratings = new Map();
  setToken(null);
  persist();
  emit();
}

/** Restaura la sesión guardada al arrancar, si el token sigue siendo válido. */
export async function restore() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      const { token } = JSON.parse(raw);
      setToken(token);
      state.token = token;
      state.user = await api.me();
      await loadRatings();
    } catch {
      logout();
    }
  }
  emit();
}

export async function refreshRatings() {
  await loadRatings();
  emit();
}

export function setRating(gameId, score) {
  state.ratings.set(gameId, score);
  emit();
}

export function ratingFor(gameId) {
  return state.ratings.get(gameId) ?? null;
}

export async function refreshUser() {
  state.user = await api.me();
  emit();
}

export function updateSteamPreferences(preferences) {
  if (!state.user) return;
  state.user = { ...state.user, genres: preferences.genres, preferences_source: preferences.source };
  emit();
}
