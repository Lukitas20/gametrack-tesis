import test from "node:test";
import assert from "node:assert/strict";
import { accountsView } from "../js/views/auth.js";
import { ACCESS_PLANS, accessPath, accessPlan, plansView } from "../js/views/access-plans.js";

// Sólo DOM en memoria; no crea cuentas ni solicitudes a la aplicación publicada.
class TestNode {
  constructor(tag, text = "") { this.tag = tag; this.text = text; this.attributes = {}; this.childNodes = []; this.listeners = {}; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  appendChild(child) { this.childNodes.push(child); return child; }
  append(...children) { this.childNodes.push(...children); }
  replaceChildren(...children) { this.childNodes = children; }
  get textContent() { return this.text + this.childNodes.map(child => child.textContent).join(""); }
}
const nodes = node => [node, ...node.childNodes.flatMap(nodes)];
const links = node => nodes(node).filter(item => item.tag === "a").map(item => item.attributes.href);

test("los planes encaminan a accesos separados, sin mezclar tipo y propuesta comercial", () => {
  assert.equal(ACCESS_PLANS.length, 3);
  assert.match(accessPath("jugador", { plan: "gratis" }), /^\/acceso\/jugadores\?/);
  assert.match(accessPath("jugador", { plan: "premium" }), /^\/acceso\/jugadores\?/);
  assert.match(accessPath("desarrollador", { plan: "estudio" }), /^\/acceso\/estudios\?/);
  assert.equal(accessPlan("jugador", "estudio").id, "gratis");
  assert.equal(accessPlan("desarrollador", "premium").id, "estudio");
  assert.ok(accessPath("desarrollador", { register: true }).includes("modo=registro"));
});

test("el acceso de estudios no ofrece Steam ni un selector de tipo; jugadores sí ofrecen Steam", t => {
  const previousDocument = globalThis.document, previousNode = globalThis.Node;
  globalThis.Node = TestNode;
  globalThis.document = { createElement: tag => new TestNode(tag), createElementNS: (_, tag) => new TestNode(tag), createTextNode: text => new TestNode("#text", text) };
  t.after(() => { globalThis.document = previousDocument; globalThis.Node = previousNode; });
  for (const mode of ["", "registro"]) {
    const studio = accountsView({ role: "desarrollador", query: new URLSearchParams({ modo: mode, plan: "premium" }) });
    const player = accountsView({ role: "jugador", query: new URLSearchParams({ modo: mode, plan: "estudio" }) });
    assert.equal(links(studio).includes("/api/v1/auth/steam/start"), false);
    assert.equal(links(player).includes("/api/v1/auth/steam/start"), true);
    assert.equal(nodes(studio).some(node => node.attributes.type === "radio"), false);
    assert.equal(nodes(player).some(node => node.attributes.type === "radio"), false);
    for (const view of [studio, player]) {
      assert.equal(nodes(view).some(node => node.className === "auth-plan-summary"), false);
      assert.equal(nodes(view).some(node => node.tag === "a" && node.textContent === "Soy desarrollador / estudio"), false);
      assert.ok(links(view).includes("#/cuentas"), "se conserva el regreso a los accesos");
    }
  }
  const overview = plansView();
  assert.equal(nodes(overview).filter(node => node.tag === "article").length, 3);
  assert.ok(overview.textContent.includes("no contrata una suscripción ni genera cobros"));
  assert.ok(overview.textContent.includes("USD 3") && overview.textContent.includes("USD 15"));
  const legacy = accountsView();
  assert.equal(nodes(legacy).some(node => node.tag === "form"), false);
});

test("una invitación se conserva al pasar por la selección de planes", t => {
  const previousDocument=globalThis.document, previousNode=globalThis.Node, previousStorage=globalThis.sessionStorage;
  const saved=new Map();
  globalThis.Node=TestNode;
  globalThis.document={createElement:tag=>new TestNode(tag),createElementNS:(_,tag)=>new TestNode(tag),createTextNode:text=>new TestNode("#text",text)};
  globalThis.sessionStorage={setItem:(key,value)=>saved.set(key,value)};
  t.after(()=>{globalThis.document=previousDocument;globalThis.Node=previousNode;globalThis.sessionStorage=previousStorage;});
  const invite="a".repeat(43);
  const view=accountsView({query:new URLSearchParams({invitacion:invite,modo:"registro"})});
  assert.equal(saved.get("gametrack.invite"),invite);
  assert.ok(links(view).filter(link=>link.includes("modo=registro")).length===3);
});
