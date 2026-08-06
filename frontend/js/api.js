/* Cliente de la API de GameTrack.
 *
 * El frontend lo sirve la misma aplicación FastAPI, así que las rutas son
 * relativas y no hay origen que configurar.
 */

const BASE = "/api/v1";

let token = null;

export function setToken(value) {
  token = value;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, auth = true, params } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== null && value !== undefined && value !== "") {
        url.searchParams.set(key, value);
      }
    }
  }

  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return null;

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    // FastAPI devuelve `detail` como texto o como lista de errores de validación.
    const detail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg).join(", ")
      : payload?.detail;
    throw new ApiError(response.status, detail);
  }
  return payload;
}

export const api = {
  // --- Autenticación ---
  login: (username, password) =>
    request("/auth/login", { method: "POST", body: { username, password }, auth: false }),
  register: (data) => request("/auth/register", { method: "POST", body: data, auth: false }),
  me: () => request("/auth/me"),
  updateProfile: (data) => request("/auth/me", { method: "PUT", body: data }),
  setPreferences: (genreIds) =>
    request("/auth/me/preferences", { method: "PUT", body: { genre_ids: genreIds } }),

  // --- Catálogo ---
  genres: () => request("/genres", { auth: false }),
  tags: (minGames = 3) => request("/tags", { auth: false, params: { min_games: minGames } }),
  games: (params) => request("/games", { auth: false, params }),
  home: (limit = 8) => request("/home", { auth: false, params: { limit } }),
  game: (id) => request(`/games/${id}`, { auth: false }),
  similar: (id, limit = 6) => request(`/games/${id}/similar`, { auth: false, params: { limit } }),
  reviews: (id, limit = 10) => request(`/games/${id}/reviews`, { auth: false, params: { limit } }),

  // --- Recomendaciones ---
  recommendations: (strategy = "auto", limit = 12) =>
    request("/recommendations", { params: { strategy, limit } }),

  // --- Interacciones ---
  myRatings: () => request("/me/ratings"),
  rate: (gameId, score, extra = {}) =>
    request("/ratings", {
      method: "POST",
      body: { game_id: gameId, score, hours_played: 0, status: "completado", ...extra },
    }),
  publishReview: (data) => request("/reviews", { method: "POST", body: data }),
  analyzeText: (content) =>
    request("/reviews/analyze", { method: "POST", body: { content }, auth: false }),

  // --- Listas ---
  myLists: () => request("/me/lists"),
  myListsSummary: () => request("/me/lists/summary"),
  listsContaining: (gameId) => request(`/me/lists/containing/${gameId}`),
  createList: (data) => request("/me/lists", { method: "POST", body: data }),
  addToList: (listId, gameId) =>
    request(`/me/lists/${listId}/items`, { method: "POST", body: { game_id: gameId } }),
  removeFromList: (listId, gameId) =>
    request(`/me/lists/${listId}/items/${gameId}`, { method: "DELETE" }),

  // --- Steam ---
  linkSteam: (steamId) => request("/steam/link", { method: "POST", body: { steam_id: steamId } }),

  // --- Analítica (rol desarrollador) ---
  studioAnalytics: (studio) => request("/analytics/studio", { params: { studio } }),
  gameAnalytics: (gameId) => request(`/analytics/games/${gameId}`),
  overview: () => request("/analytics/overview"),
  processReviews: (reanalyze = false) =>
    request("/analytics/process", { method: "POST", params: { reanalyze } }),
};
