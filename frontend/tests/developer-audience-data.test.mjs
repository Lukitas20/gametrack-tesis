import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { buildAudienceRanking } from "../js/views/developer-audience-data.js";

// Isolated unit fixtures are never stored as product observations or model data.
const group = (key, size, extra = {}) => ({key:`tag:${key}`, kind:"tag", label:key, size, examples:[], ...extra});
const report = groups => ({minimum_group:5, steam_histories:{groups}});

test("el top cinco usa los grupos reales de Steam y conserva el informe", async () => {
  const evidence = JSON.parse(await readFile(new URL("../../docs/evidence/audience-report-cloud-20261017.json", import.meta.url), "utf8"));
  const original = JSON.stringify(evidence.report);
  const {categories, reflection} = buildAudienceRanking(evidence.report);
  assert.deepEqual(categories.map(({label, size}) => [label, size]), [
    ["Acción", 33], ["Aventura", 27], ["Indie", 18], ["Rol", 17], ["Multijugador", 15],
  ]);
  assert.deepEqual(categories.map(category => category.rank), [1, 2, 3, 4, 5]);
  assert.equal(reflection.title, "Acción lidera los gustos observados");
  assert.match(reflection.text, /Aventura e Indie/);
  assert.equal(reflection.example.name, "Resident Evil 2");
  assert.equal(reflection.example.category, "Acción");
  categories[0].examples[0].name = "test mutation";
  categories[0].reception.recommended = 0;
  assert.equal(JSON.stringify(evidence.report), original);
});

test("ordena los empates por clave con puestos compartidos y sin mezclar GameTrack", () => {
  const input = {...report([group("Zulu", 8), group("Delta", 12), group("Beta", 12), group("Eta", 7), group("Alpha", 8), group("Extra", 6)]),
    groups:[{key:"manual", label:"No es Steam", size:100, unit:"usuarios"}]};
  const before = JSON.stringify(input);
  const {categories, reflection} = buildAudienceRanking(input);
  assert.deepEqual(categories.map(category => category.label), ["Beta", "Delta", "Alpha", "Zulu", "Eta"]);
  assert.deepEqual(categories.map(category => category.rank), [1, 1, 3, 3, 5]);
  assert.deepEqual(categories.map(category => category.position), [1, 2, 3, 4, 5]);
  assert.equal(categories[2].relativeSize, 8 / 12);
  assert.equal(reflection.title, "Beta y Delta comparten el primer puesto");
  assert.match(reflection.text, /No hay una única categoría dominante/);
  assert.equal(JSON.stringify(input), before);
});

test("los grupos bajo el mínimo, suprimidos o inválidos no se publican", () => {
  const {categories, reflection} = buildAudienceRanking(report([
    group("Pequeño", 4), group("Oculto", 20, {suppressed:true}), group("Texto", "15"),
    group("Fracción", 5.5), group("Infinito", Infinity), group("Tipo ajeno", 20, {kind:"rating"}),
    group("", 15), null,
  ]));
  assert.deepEqual(categories, []);
  assert.equal(reflection, null);
  assert.deepEqual(buildAudienceRanking(null), {categories:[], reflection:null});
});

test("respeta un umbral de privacidad mayor y elimina claves repetidas sin sumar usuarios", () => {
  const {categories} = buildAudienceRanking({minimum_group:8, steam_histories:{groups:[group("A", 7), group("B", 9), group("B", 10)]}});
  assert.equal(categories.length, 1);
  assert.equal(categories[0].size, 10);
});

test("una categoría aislada no fabrica ejemplos ni conclusiones de mercado", () => {
  const {reflection} = buildAudienceRanking(report([group("RPG", 5, {examples:[
    {game_id:7, name:"Por debajo del mínimo", authors:4}, {game_id:8, name:"Excede el grupo", authors:6},
    {game_id:null, name:"Sin ficha", authors:5},
  ]})]));
  assert.equal(reflection.example, null);
  assert.match(reflection.text, /única categoría con evidencia suficiente/);
  assert.doesNotMatch(JSON.stringify(reflection), /ventas|compradores|probabilidad|IA/);
});

test("los empates que exceden el top cinco no producen un falso líder único", () => {
  const {categories, reflection} = buildAudienceRanking(report(["A", "B", "C", "D", "E", "F"].map(key => group(key, 9))));
  assert.equal(categories.length, 5);
  assert.ok(categories.every(category => category.rank === 1));
  assert.equal(reflection.title, "Varios gustos comparten el primer puesto");
});
