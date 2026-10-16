import test from "node:test";
import assert from "node:assert/strict";
import { loginDestination, studioDestination, hasStudio } from "../js/store.js";

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
  assert.equal(hasStudio({ role: "jugador", developer_enabled: true }), true);
  assert.equal(hasStudio({ role: "desarrollador" }), true);
  assert.equal(hasStudio({ role: "jugador", developer_enabled: false }), false);
});
