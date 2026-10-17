/* Exporta la respuesta recibida sin recalcular ni completar juicios. */
export function evaluationSnapshot(kind, response, context = {}) {
  return {schema:"gametrack-evaluation-capture-v1", captured_at:new Date().toISOString(),
    kind, context, response, annotation_status:"unjudged", split_status:"not_audited"};
}
export function exportEvaluation(kind, response, context = {}) {
  const snapshot = evaluationSnapshot(kind, response, context);
  const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:"application/json"}));
  const link = document.createElement("a");
  link.href=url;link.download=`gametrack-${kind}-${Date.now()}.json`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
