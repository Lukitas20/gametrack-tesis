import test from "node:test";
import assert from "node:assert/strict";
import { evaluationSnapshot } from "../js/evaluation-export.js";
import { audiencePanel } from "../js/views/developer-audience.js";
import { trainedReviewInsights } from "../js/views/developer-trained-insights.js";
import { api } from "../js/api.js";

class TestNode {
  constructor(tag,text="") {this.tag=tag;this.text=text;this.childNodes=[];this.attributes={};}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  addEventListener() {}
  appendChild(child) {this.childNodes.push(child);return child;}
  replaceChildren() {this.childNodes=[];}
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
  assert.match(panel.textContent,/no hay grupos/i);assert.match(panel.textContent,/Faltan datos/);
});

test("audiencias consulta por lotes al abrir y muestra gustos Steam aunque no haya usuarios GameTrack",async t=>{
  dom(t);
  // Fixture aislado de interfaz; no se guarda ni se publica como observación.
  const result={gametrack_users:0,steam_reviews:12,games:1,coverage:{},groups:[],minimum_group:5,limitations:[],
    steam_histories:{pending_authors:1,identity_games_pending:0,selected_authors:6,checked_authors:5,public_histories:5,
      groups:[{kind:"tag",label:"Mundo abierto",size:5,denominator:5,criterion:"Dos juegos distintos fuera del informe",
        reception:{recommended:4,not_recommended:1,mixed:0,unknown:0},examples:[]}]}};
  const previous=api.collectAudience;let calls=0;
  t.after(()=>{api.collectAudience=previous;});
  api.collectAudience=async scope=>{
    assert.deepEqual(scope,{game_id:4});calls++;
    return {status:calls===1?"more":"ready",audience:{...result,steam_histories:{...result.steam_histories,pending_authors:calls===1?1:0}}};
  };
  const panel=audiencePanel(result,{game_id:4});panel.isConnected=true;
  panel.load();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,2);
  assert.match(panel.textContent,/Mundo abierto/);assert.match(panel.textContent,/5 de 5 autores/);
  assert.match(panel.textContent,/4 sólo recomiendan/);assert.doesNotMatch(panel.textContent,/Reseñas que recomiendan/);
  panel.load();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,2);
});
test("disponibilidad e intervención del clasificador tienen etiquetas distintas",t=>{
  dom(t);
  const available=trainedReviewInsights({model:{status:"ready"},sampled:0,total:0,topics:[],used:false});
  assert.match(available.textContent,/Disponible · sin aporte/);assert.doesNotMatch(available.textContent,/Modelo utilizado/);
  const used=trainedReviewInsights({model:{status:"ready"},sampled:1,total:1,topics:[],used:true});
  assert.match(used.textContent,/Modelo utilizado en este informe/);
});
