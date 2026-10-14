import { test } from 'node:test';
import assert from 'node:assert/strict';
import { aspectComparisons, comparisonHighlights, reviewMetrics } from '../js/views/developer-comparison-data.js';

const report = (n, positive, negative, aspects = [], localVotes = 0, localRating = null) => ({
  juego: { cantidad_ratings_local: localVotes, rating_local_promedio: localRating, rating_promedio: 4.9, cantidad_ratings: 50000 },
  resenas: { analizadas: n, pendientes: 4, distribucion: { positivo: positive, negativo: negative, neutro: n - positive - negative }, sentimiento_neto: n ? (positive - negative) / n : 0 }, aspectos: aspects,
});
const aspect = (key, n, net) => ({ aspecto: key, menciones: n, sentimiento_neto: net });

test('an empty analysis does not look neutral or fully positive', () => {
  const metrics = reviewMetrics(report(0, 0, 0));
  assert.equal(metrics.positive, null); assert.equal(metrics.net, null);
  assert.equal(metrics.coverage, 0); assert.equal(metrics.saved, 4);
});
test('imported popularity cannot become GameTrack votes or its local rating', () => {
  assert.equal(reviewMetrics(report(20, 12, 3)).localRating, null);
  assert.equal(reviewMetrics(report(20, 12, 3)).localVotes, 0);
  assert.equal(reviewMetrics(report(20, 12, 3, [], 2, 3.5)).localRating, 3.5);
});
test('percentages use analyzed texts, not all saved or imported votes', () => {
  const metrics = reviewMetrics(report(20, 12, 3));
  assert.equal(metrics.positive, 60); assert.equal(metrics.negative, 15);
  assert.equal(metrics.net, .45); assert.equal(metrics.coverage, 100 * 20 / 24);
});
test('missing or tiny aspects cannot create a comparative advantage', () => {
  const left = report(10, 5, 3, [aspect('historia', 3, -.8), aspect('graficos', 10, .5)]);
  const right = report(30, 20, 4, [aspect('historia', 20, .8)]);
  const rows = aspectComparisons(left, right);
  assert.equal(rows.find(r => r.key === 'graficos').delta, null);
  assert.equal(rows.find(r => r.key === 'historia').comparable, false);
  assert.deepEqual(comparisonHighlights(left, right), []);
});
test('deltas compare the same aspect in the documented direction', () => {
  const left = report(15, 5, 8, [aspect('historia', 5, -.6), aspect('optimizacion', 8, .5)]);
  const right = report(20, 12, 4, [aspect('historia', 6, .6), aspect('optimizacion', 10, -.3)]);
  const rows = comparisonHighlights(left, right);
  assert.equal(rows[0].key, 'historia'); assert.equal(rows[0].delta, 1.2);
  assert.equal(rows[1].key, 'optimizacion'); assert.equal(rows[1].delta, -.8);
});
test('a difference at the threshold survives floating point rounding', () => {
  const left = report(10, 5, 2, [aspect('historia', 5, .2)]);
  const right = report(10, 5, 2, [aspect('historia', 5, .35)]);
  assert.equal(comparisonHighlights(left, right)[0].delta, .15);
});
