import test from "node:test";
import assert from "node:assert/strict";
import { evaluationSnapshot } from "../js/evaluation-export.js";
import { audiencePanel } from "../js/views/developer-audience.js";
import { trainedReviewInsights } from "../js/views/developer-trained-insights.js";

class TestNode {
  constructor(tag,text="") {this.tag=tag;this.text=text;this.childNodes=[];this.attributes={};}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  addEventListener() {}
  appendChild(child) {this.childNodes.push(child);return child;}
  get textContent() {return this.text+this.childNodes.map(child=>child.textContent).join("");}
}
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
  assert.match(panel.textContent,/No hay grupos/);assert.match(panel.textContent,/Faltan datos/);
});
test("disponibilidad e intervención del clasificador tienen etiquetas distintas",t=>{
  dom(t);
  const available=trainedReviewInsights({model:{status:"ready"},sampled:0,total:0,topics:[],used:false});
  assert.match(available.textContent,/Disponible · sin aporte/);assert.doesNotMatch(available.textContent,/Modelo utilizado/);
  const used=trainedReviewInsights({model:{status:"ready"},sampled:1,total:1,topics:[],used:true});
  assert.match(used.textContent,/Modelo utilizado en este informe/);
});
