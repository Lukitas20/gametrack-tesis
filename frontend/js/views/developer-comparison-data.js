/* Los porcentajes describen la muestra analizada, no todos los jugadores. */
const finite = value => value !== null && value !== undefined && Number.isFinite(Number(value));

export function reviewMetrics(report) {
  const s = report.resenas, n = Number(s.analizadas || 0);
  const saved = n + Number(s.pendientes || 0), votes = Number(report.juego.cantidad_ratings_local || 0);
  return {
    saved, analyzed: n, pending: Number(s.pendientes || 0),
    positive: n ? 100 * s.distribucion.positivo / n : null,
    negative: n ? 100 * s.distribucion.negativo / n : null,
    net: n && finite(s.sentimiento_neto) ? Number(s.sentimiento_neto) : null,
    coverage: saved ? 100 * n / saved : null,
    localRating: votes && finite(report.juego.rating_local_promedio) ? Number(report.juego.rating_local_promedio) : null,
    localVotes: votes,
  };
}

export function aspectComparisons(catalog, own = null) {
  const keys = [...new Set(['jugabilidad', 'graficos', 'historia', 'optimizacion',
    ...catalog.aspectos.map(a => a.aspecto), ...(own?.aspectos || []).map(a => a.aspecto)])];
  const find = (report, key) => {
    const a = report?.aspectos.find(a => a.aspecto === key);
    return a?.menciones > 0 && finite(a.sentimiento_neto) ? a : null;
  };
  return keys.map(key => {
    const left = find(catalog, key), right = find(own, key);
    return { key, catalog: left, own: right,
      delta: left && right ? Math.round((Number(right.sentimiento_neto) - Number(left.sentimiento_neto)) * 1000) / 1000 : null,
      comparable: Boolean(left && right && left.menciones >= 5 && right.menciones >= 5) };
  });
}

export function comparisonHighlights(catalog, own) {
  if (!own) return [];
  return aspectComparisons(catalog, own).filter(row => row.comparable && Math.abs(row.delta) >= .15)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 2);
}
