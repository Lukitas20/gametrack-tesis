import test from "node:test";
import assert from "node:assert/strict";
import { experienceView } from "../js/views/experience.js";
import { api } from "../js/api.js";
import { state } from "../js/store.js";

// DOM mínimo aislado: no crea cuentas ni consulta servicios.
class TestNode {
  constructor(tag, text = "") {
    this.tag = tag;
    this.text = text;
    this.attributes = {};
    this.childNodes = [];
    this.listeners = {};
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  appendChild(child) { this.childNodes.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  replaceChildren(...children) { this.childNodes = [...children]; }
  get textContent() { return this.text + this.childNodes.map(child => child.textContent).join(""); }
  set textContent(value) { this.text = value; this.childNodes = []; }
}

function nodes(root) { return [root, ...root.childNodes.flatMap(nodes)]; }
function radios(root) { return nodes(root).filter(node => node.attributes.type === "radio"); }

test("la bienvenida solicita el tipo de usuario sólo cuando todavía no fue elegido", async t => {
  const previousDocument = globalThis.document;
  const previousNode = globalThis.Node;
  const previousUser = state.user;
  globalThis.Node = TestNode;
  globalThis.document = {
    createElement: tag => new TestNode(tag),
    createElementNS: (_, tag) => new TestNode(tag),
    createTextNode: text => new TestNode("#text", text),
  };
  t.mock.method(api, "genres", async () => []);
  t.after(() => {
    globalThis.document = previousDocument;
    globalThis.Node = previousNode;
    state.user = previousUser;
  });

  for (const [label, role, step, query, heading] of [
    ["registro jugador", "jugador", "player", "", "Empecemos por tus gustos"],
    ["registro estudio", "desarrollador", "studio", "", "¿Qué juego querés analizar primero?"],
    ["Steam con jugador elegido", "jugador", "choose", "tipo=jugador", "Empecemos por tus gustos"],
    ["Steam con estudio elegido", "jugador", "choose", "tipo=desarrollador", "¿Qué juego querés analizar primero?"],
    ["configuración de estudio desde el perfil", "jugador", null, "tipo=desarrollador", "¿Qué juego querés analizar primero?"],
  ]) {
    await t.test(label, () => {
      state.user = { role, onboarding_step: step, genres: [] };
      const view = experienceView({ query: new URLSearchParams(query) });
      assert.equal(radios(view).length, 0, "no debe repetirse la elección");
      assert.ok(nodes(view).some(node => node.tag === "h2" && node.textContent === heading));
    });
  }

  for (const query of ["", "tipo=invalido"]) {
    await t.test(`Steam sin elección previa (${query || "sin parámetro"})`, () => {
      state.user = { role: "jugador", onboarding_step: "choose", genres: [] };
      const view = experienceView({ query: new URLSearchParams(query) });
      assert.equal(radios(view).length, 2);
      assert.equal(nodes(view).some(node => node.tag === "h2"), false);
      radios(view).find(node => node.attributes.value === "desarrollador")
        .listeners.change({ target: { checked: true } });
      assert.ok(nodes(view).some(node => node.tag === "h2" && node.textContent === "¿Qué juego querés analizar primero?"));
    });
  }
});
