/* Fixtures aislados de interfaz: no son usuarios ni evaluaciones del producto. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { expandableText, infoDetails } from "../js/ui.js";
import { api } from "../js/api.js";
import { state } from "../js/store.js";
import { personalScorePanel } from "../js/views/gametrack-score.js";
import { audiencePanel } from "../js/views/developer-audience.js";
import { catalogView } from "../js/views/catalog.js";

class Element {
  constructor(tag,text="") {this.tag=tag;this.text=text;this.childNodes=[];this.attributes={};this.events={};this.style={};this.dataset={};this.isConnected=false;}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  getAttribute(key) {return this.attributes[key]??null;}
  get id() {return this.attributes.id;}
  addEventListener(name,fn) {this.events[name]=fn;}
  appendChild(child) {this.childNodes.push(child);return child;}
  append(...children) {this.childNodes.push(...children);}
  replaceChildren(...children) {this.childNodes=children;}
  get children() {return this.childNodes.filter(n=>n.tag!=="#text");}
  get classList() {return {toggle:(name)=>{const classes=new Set((this.className||"").split(/\s+/));const enabled=!classes.has(name);if(enabled)classes.add(name);else classes.delete(name);this.className=[...classes].join(" ");return enabled;},add:()=>{},remove:()=>{}};}
  querySelector(selector) {return walk(this).find(n=>selector.startsWith(".")?(n.className||"").split(/\s+/).includes(selector.slice(1)):selector==="[data-more]"?n.dataset.more!==undefined:false)||null;}
  get textContent() {return this.text+this.childNodes.map(n=>n.textContent??"").join("");}
  set textContent(value) {this.text=String(value);this.childNodes=[];}
}
const walk=node=>[node,...node.childNodes.flatMap(child=>walk(child))];
function dom(t) {
  const previous=[globalThis.Node,globalThis.document,globalThis.window];
  globalThis.Node=Element;
  globalThis.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),createTextNode:text=>new Element("#text",text)};
  globalThis.window={location:{origin:"http://unit.test"}};
  t.after(()=>{[globalThis.Node,globalThis.document,globalThis.window]=previous;});
}

test("el texto largo se conserva íntegro y se puede expandir y contraer con estado accesible",t=>{
  dom(t);const content="<texto literal>\n"+"una opinión ".repeat(45);
  const root=expandableText(content),[body,button]=root.children;
  assert.equal(body.textContent,content);assert.equal(button.getAttribute("aria-controls"),body.id);
  assert.equal(button.getAttribute("aria-expanded"),"false");
  button.events.click();assert.equal(button.getAttribute("aria-expanded"),"true");assert.equal(button.textContent,"Mostrar menos");
  assert.equal(body.textContent,content);assert.ok(!body.className.includes("is-collapsed"));
  button.events.click();assert.equal(button.getAttribute("aria-expanded"),"false");assert.equal(body.textContent,content);
  assert.equal(expandableText("Breve").children.length,1);
});

test("la ayuda se ofrece en un desplegable nativo cerrado sin descartar su contenido",t=>{
  dom(t);const help=infoDetails("Fuente y alcance",new Element("p","Muestra observada"));
  assert.equal(help.tag,"details");assert.equal(help.children[0].tag,"summary");
  assert.equal(help.getAttribute("open"),null);assert.match(help.textContent,/Muestra observada/);
});

test("la afinidad conserva el resultado del servidor y presenta una sola acción de explicación",async t=>{
  dom(t);const oldUser=state.user,oldScore=api.gameTrackScore;
  state.user={id:1,role:"jugador"};const score=Object.freeze({value:71,evidence:"steam",confidence:"low",confidence_message:"Provisional"});
  api.gameTrackScore=async()=>score;t.after(()=>{state.user=oldUser;api.gameTrackScore=oldScore;});
  const panel=personalScorePanel(1,"Fixture",{showFeedback:false});await new Promise(resolve=>setImmediate(resolve));
  const buttons=walk(panel).filter(n=>n.tag==="button");assert.equal(buttons.length,1);
  assert.match(buttons[0].textContent,/Entender mi afinidad/);assert.match(panel.textContent,/71/);
  assert.match(panel.textContent,/provisional/i);assert.ok(!panel.textContent.includes("¿Por qué este puntaje?"));
  assert.deepEqual(score,{value:71,evidence:"steam",confidence:"low",confidence_message:"Provisional"});
});

test("Audiencias muestra cinco categorías y reflexión sin los paneles técnicos ni fracciones, y reutiliza la caché completa",async t=>{
  dom(t);
  const evidence=JSON.parse(await readFile(new URL("../../docs/evidence/audience-report-cloud-20261017.json",import.meta.url),"utf8"));
  const previous=api.collectAudience;let calls=0;api.collectAudience=async()=>{calls++;throw new Error("No debe consultar una caché completa");};
  t.after(()=>{api.collectAudience=previous;});
  const report=evidence.report,before=JSON.stringify(report),panel=audiencePanel(report,{studio:"CD PROJEKT RED"});
  const rows=walk(panel).filter(node=>node.tag==="li"&&node.className?.includes("audience-rank"));
  assert.equal(rows.length,5);assert.equal(rows[0].textContent,"01GéneroAcción55%de la audiencia");assert.match(rows[4].textContent,/Multijugador25%de la audiencia/);
  assert.ok(Math.abs(parseFloat(walk(rows[0]).find(node=>node.className==="audience-bar").childNodes[0].style.width)-55)<0.001);
  assert.doesNotMatch(rows.map(row=>row.textContent).join(""),/33usuarios|15usuarios/);
  assert.match(panel.textContent,/Acción lidera los gustos observados/);assert.match(panel.textContent,/Aventura e Indie/);
  assert.ok(!panel.textContent.includes("0 sin voto guardado"));
  assert.ok(!walk(panel).some(n=>n.tag==="details"||n.tag==="button"));
  assert.doesNotMatch(panel.textContent,/\d+ de \d+|Cobertura|Procedencia y alcance|Usuarios de GameTrack|autores seleccionados|Consultados \d+/);
  assert.ok(!panel.textContent.includes(report.steam_histories.groups[0].criterion));
  panel.isConnected=true;panel.load();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,0);
  assert.equal(JSON.stringify(report),before);assert.match(panel.textContent,/no representa a todo el mercado/);
  assert.match(panel.textContent,/Una persona puede aparecer en varias categorías/);assert.match(panel.textContent,/no tienen que sumar 100%/);
});

test("el catálogo usa tres selectores y conserva el filtro de característica en la consulta",async t=>{
  dom(t);const previous=[state.genres,state.tags,api.games,api.steamCatalogStatus,globalThis.setTimeout];
  state.genres=[{slug:"accion",name:"Acción"}];state.tags=[{slug:"coop",name:"Cooperativo"},{slug:"provider-key",name:"#category_internal"}];
  const calls=[];api.games=async filters=>{calls.push({...filters});return {items:[],total:0};};
  api.steamCatalogStatus=async()=>({worker_mode:"scheduled",entry_counts:{},total_games:0});
  const timer=globalThis.setTimeout;globalThis.setTimeout=(fn,ms,...args)=>{const result=timer(fn,ms,...args);if(ms>=15000)result.unref();return result;};
  t.after(()=>{[state.genres,state.tags,api.games,api.steamCatalogStatus,globalThis.setTimeout]=previous;});
  const root=await catalogView({query:new URLSearchParams("etiqueta=coop")});
  const selects=walk(root).filter(n=>n.tag==="select");assert.equal(selects.length,3);
  const filter=selects.find(n=>n.getAttribute("aria-label")==="Filtrar por característica");
  assert.ok(!filter.textContent.includes("#category_internal"));
  assert.equal(filter.children.find(n=>n.getAttribute("value")==="coop").getAttribute("selected"),"");
  await new Promise(resolve=>timer(resolve,580));assert.equal(calls[0].tag,"coop");
  filter.events.change({target:{value:""}});await new Promise(resolve=>timer(resolve,580));assert.equal(calls.at(-1).tag,"");
});
