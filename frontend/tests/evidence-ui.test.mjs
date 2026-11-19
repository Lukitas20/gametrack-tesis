import test from "node:test";
import assert from "node:assert/strict";
import { evaluationSnapshot } from "../js/evaluation-export.js";
import { audiencePanel } from "../js/views/developer-audience.js";
import { trainedReviewInsights } from "../js/views/developer-trained-insights.js";
import { api } from "../js/api.js";

class TestNode {
  constructor(tag,text="") {this.tag=tag;this.text=text;this.childNodes=[];this.attributes={};this.events={};this.style={};this.isConnected=false;}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  addEventListener(key,value) {this.events[key]=value;}
  appendChild(child) {this.childNodes.push(child);return child;}
  replaceChildren() {this.childNodes=[];}
  get textContent() {return this.text+this.childNodes.map(child=>child.textContent).join("");}
}
const walk=node=>[node,...node.childNodes.flatMap(child=>walk(child))];
const ranking=panel=>walk(panel).find(node=>node.tag==="ol"&&node.className==="audience-ranking");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
// Fixtures aisladas de interfaz: nunca se guardan como usuarios o datos del producto.
const taste=(label,size)=>({key:`tag:${label}`,kind:"tag",label,size,denominator:60,reception:{recommended:size,not_recommended:0,mixed:0,unknown:0},examples:[]});
const audience=(groups,pending=0)=>({gametrack_users:0,steam_reviews:12,games:1,coverage:{},groups:[],minimum_group:5,limitations:[],
  steam_histories:{pending_authors:pending,identity_games_pending:0,selected_authors:60,checked_authors:60-pending,public_histories:60-pending,groups}});
function mockCollection(t,collect) {const previous=api.collectAudience;api.collectAudience=collect;t.after(()=>{api.collectAudience=previous;});}
function retry(panel) {const button=walk(panel).find(node=>node.tag==="button"&&node.textContent==="Volver a intentar");assert.ok(button,"ofrece reintentar la consulta incompleta");return button.events.click();}
function dom(t) {
  const previous=[globalThis.Node,globalThis.document];
  globalThis.Node=TestNode;
  globalThis.document={createElement:tag=>new TestNode(tag),createElementNS:(_,tag)=>new TestNode(tag),createTextNode:text=>new TestNode("#text",text)};
  t.after(()=>{[globalThis.Node,globalThis.document]=previous;});
}
test("la captura conserva el resultado, con juicios y separación pendientes",()=>{
  const response={items:[],score:null};const before=JSON.stringify(response);
  const capture=evaluationSnapshot("recommendations",response,{strategy:"auto"});
  assert.equal(JSON.stringify(response),before);
  assert.equal(capture.response,response);assert.equal(capture.annotation_status,"unjudged");assert.equal(capture.split_status,"not_audited");
});
test("una audiencia sin grupos suficientes informa faltantes sin inventar segmentos",t=>{
  dom(t);
  const panel=audiencePanel({gametrack_users:0,steam_reviews:0,games:1,coverage:{ratings:0},groups:[],limitations:["Muestra observada"]});
  assert.match(panel.textContent,/no hay grupos/i);assert.match(panel.textContent,/Faltan datos/);
});

test("Audiencias publica el top cinco completo sólo tras terminar todos los lotes",async t=>{
  dom(t);
  const first=deferred(),second=deferred();let calls=0;
  mockCollection(t,scope=>{assert.deepEqual(scope,{game_id:4});calls++;return calls===1?first.promise:second.promise;});
  const initial=audience([taste("Grupo inicial incompleto",5)],2),before=JSON.stringify(initial);
  const panel=audiencePanel(initial,{game_id:4});panel.isConnected=true;
  assert.equal(ranking(panel),undefined);assert.doesNotMatch(panel.textContent,/Grupo inicial incompleto/);
  panel.load();panel.load();await flush();assert.equal(calls,1);
  assert.equal(panel.attributes["aria-busy"],"true");assert.match(panel.textContent,/Preparando tu audiencia/);
  first.resolve({status:"more",audience:audience([taste("Grupo intermedio",8)],1)});await flush();
  assert.equal(calls,2);assert.equal(ranking(panel),undefined);assert.doesNotMatch(panel.textContent,/Grupo intermedio/);
  const final=audience([taste("Shooter",8),taste("Cooperativos",10),taste("Competitivos",12),taste("Tácticos",9),taste("Multijugador",11),taste("Fuera del top",5)]);
  const finalBefore=JSON.stringify(final);second.resolve({status:"ready",audience:final});await flush();
  assert.equal(ranking(panel).childNodes.length,5);assert.equal(panel.attributes["aria-busy"],"false");
  assert.match(panel.textContent,/Competitivos lidera los gustos observados/);assert.doesNotMatch(panel.textContent,/Fuera del top|Grupo intermedio|Grupo inicial/);
  assert.equal(JSON.stringify(initial),before);assert.equal(JSON.stringify(final),finalBefore);
  panel.load();await flush();assert.equal(calls,2);
});

test("una respuesta ready con datos pendientes no publica el ranking",async t=>{
  dom(t);let calls=0;
  mockCollection(t,async()=>{calls++;return {status:"ready",audience:audience([taste("Parcial",10)],1)};});
  const panel=audiencePanel(audience([],1),{game_id:4});panel.isConnected=true;panel.load();await flush();
  assert.equal(calls,1);assert.equal(ranking(panel),undefined);assert.doesNotMatch(panel.textContent,/Parcial/);
  assert.match(panel.textContent,/necesita un poco más de tiempo/);assert.equal(panel.attributes["aria-busy"],"false");
  assert.ok(walk(panel).some(node=>node.tag==="button"&&node.textContent==="Volver a intentar"));
});

for(const failure of ["error","busy"]) test(`Audiencias no publica datos parciales ante ${failure} y permite reanudar`,async t=>{
  dom(t);const second=deferred(),last=deferred();let calls=0;
  mockCollection(t,()=>{calls++;if(calls===1)return Promise.resolve({status:"more",audience:audience([taste("Parcial",8)],1)});return calls===2?second.promise:last.promise;});
  const panel=audiencePanel(audience([],2),{game_id:4});panel.isConnected=true;panel.load();await flush();
  assert.equal(calls,2);assert.equal(ranking(panel),undefined);
  if(failure==="error")second.reject(new Error("network fixture"));else second.resolve({status:"busy",audience:audience([taste("Ocupado",10)],1)});
  await flush();assert.equal(ranking(panel),undefined);assert.doesNotMatch(panel.textContent,/Parcial|Ocupado|network fixture/);
  assert.equal(panel.attributes["aria-busy"],"false");
  const retrying=retry(panel);await flush();assert.equal(calls,3);assert.equal(ranking(panel),undefined);assert.equal(panel.attributes["aria-busy"],"true");
  last.resolve({status:"ready",audience:audience([taste("Final",10)])});await retrying;
  assert.ok(ranking(panel));assert.match(panel.textContent,/Final/);assert.equal(panel.attributes["aria-busy"],"false");
});

test("desconectar el panel descarta la respuesta en vuelo y volver a abrir reanuda",async t=>{
  dom(t);const first=deferred(),second=deferred();let calls=0;
  mockCollection(t,()=>{calls++;return calls===1?first.promise:second.promise;});
  const panel=audiencePanel(audience([],1),{game_id:4});panel.isConnected=true;panel.load();await flush();
  panel.isConnected=false;first.resolve({status:"ready",audience:audience([taste("Respuesta vieja",10)])});await flush();
  assert.equal(ranking(panel),undefined);assert.doesNotMatch(panel.textContent,/Respuesta vieja/);
  panel.isConnected=true;panel.load();await flush();assert.equal(calls,2);assert.equal(ranking(panel),undefined);
  second.resolve({status:"ready",audience:audience([taste("Respuesta actual",12)])});await flush();
  assert.ok(ranking(panel));assert.match(panel.textContent,/Respuesta actual/);assert.doesNotMatch(panel.textContent,/Respuesta vieja/);
});
test("disponibilidad e intervención del clasificador tienen etiquetas distintas",t=>{
  dom(t);
  const available=trainedReviewInsights({model:{status:"ready"},sampled:0,total:0,topics:[],used:false});
  assert.match(available.textContent,/Disponible · sin aporte/);assert.doesNotMatch(available.textContent,/Modelo utilizado/);
  const used=trainedReviewInsights({model:{status:"ready"},sampled:1,total:1,topics:[],used:true});
  assert.match(used.textContent,/Modelo utilizado en este informe/);
});
