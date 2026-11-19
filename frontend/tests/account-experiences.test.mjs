import test from "node:test";
import assert from "node:assert/strict";
import { loginDestination, studioDestination, isDeveloper, accountSetupRole, canAccessRoute } from "../js/store.js";

const saved = new Map();
globalThis.sessionStorage = {
  getItem: key => saved.get(key) ?? null,
  removeItem: key => saved.delete(key),
};

test("la configuración inicial precede al destino de login y conserva la invitación", () => {
  const invite = "a".repeat(43);
  saved.set("gametrack.invite", invite);
  assert.equal(loginDestination({ role: "jugador", onboarding_step: "choose" }), "/bienvenida");
  assert.equal(saved.get("gametrack.invite"), invite);
  assert.equal(loginDestination({ role: "desarrollador", analysis_game_id: 42 }), "/dev/juego/42");
  assert.equal(saved.get("gametrack.invite"), invite);
  assert.equal(loginDestination({ role: "jugador" }), `/invitacion/${invite}`);
  assert.equal(saved.has("gametrack.invite"), false);
});

test("los destinos y la capacidad profesional son independientes del plan", () => {
  assert.equal(loginDestination({ role: "jugador" }), "/recomendaciones");
  assert.equal(loginDestination({ role: "jugador" }, "/perfil"), "/perfil");
  assert.equal(loginDestination({ role: "desarrollador" }), "/dev");
  assert.equal(studioDestination({ analysis_game_id: 7 }), "/dev/juego/7");
  assert.equal(isDeveloper({ role: "jugador", developer_enabled: true }), false);
  assert.equal(isDeveloper({ role: "desarrollador" }), true);
  assert.equal(isDeveloper({ role: "jugador", developer_enabled: false }), false);
});

test("el tipo elegido no cambia mediante parámetros ni accesos directos", () => {
  const player = { role: "jugador", onboarding_step: "player", developer_enabled: true };
  const developer = { role: "desarrollador" };
  assert.equal(accountSetupRole(player, "desarrollador"), "jugador");
  assert.equal(accountSetupRole(developer, "jugador"), "desarrollador");
  assert.equal(accountSetupRole({ role: "jugador", onboarding_step: "choose" }, "desarrollador"), "desarrollador");
  for (const path of ["/dev", "/dev/juego/42"]) {
    assert.equal(canAccessRoute(path, player), false);
    assert.equal(canAccessRoute(path, developer), true);
  }
  for (const path of ["/recomendaciones", "/listas", "/amigos", "/valoraciones", "/que-jugamos", "/invitacion/abc"]) {
    assert.equal(canAccessRoute(path, player), true);
    assert.equal(canAccessRoute(path, developer), false);
  }
  for (const user of [player, developer]) for (const path of ["/catalogo", "/juego/42", "/perfil"]) assert.equal(canAccessRoute(path, user), true);
});
