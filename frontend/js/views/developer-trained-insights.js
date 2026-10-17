/* El modelo aporta temas y evidencia al informe existente, sin otra vista. */
import { h, icon } from "../ui.js";

const count = value => Number(value || 0).toLocaleString("es-AR");

export function trainedReviewInsights(data, { title = "Temas de las reseñas", onAsk = null, compact = false } = {}) {
  if (!data) return null;
  const ready = data.model?.status === "ready";
  const topics = (data.topics || []).filter(topic => topic.mentions > 0).slice(0, compact ? 3 : 5);
  const sampled = Number(data.sampled || 0), total = Number(data.total || 0);
  const scopeNote = sampled < total
    ? `${count(sampled)} reseñas más recientes de ${count(total)} guardadas.`
    : `${count(sampled)} reseñas guardadas en este informe.`;
  const method = h("details", { class: "dev-method dev-trained-method" },
    h("summary", null, "Cómo leer este análisis"),
    ready && h("p", null, data.model.examples && data.model.games
      ? `Modelo local entrenado con ${count(data.model.examples)} reseñas reales de Steam de ${count(data.model.games)} juegos.`
      : "Modelo local entrenado con reseñas reales de Steam."),
    h("p", null, "Los temas se aprendieron con etiquetas automáticas sobre textos reales. Una mención no indica por sí sola una crítica ni explica por qué alguien no recomienda el juego."),
    ready && h("p",null,`${count(data.predicted)} textos con al menos una predicción habilitada en esta muestra. Disponibilidad del modelo y aporte a este informe son cosas distintas.`),
    (data.limitations || []).map(text => h("p", null, text)));
  return h("section", { class: `dev-trained-insights${compact ? " is-compact" : ""}`, "aria-label": title },
    h("div", { class: "dev-trained-heading" },
      h("span", { class: "dev-trained-mark", "aria-hidden": "true" }, icon("sparkles", 19)),
      h("div", null, h("h3", null, title), h("p", { class: "dev-caption" }, ready
        ? sampled ? scopeNote : "No hay reseñas disponibles en español para este análisis."
        : data.model?.message || "El modelo entrenado no está disponible. Las métricas del informe siguen disponibles.")),
      h("span", { class: "dev-badge" }, data.used ? "Modelo utilizado en este informe" : ready ? "Disponible · sin aporte en este informe" : "Modelo no disponible")),
    ready && topics.length > 0 && h("div", { class: "dev-trained-topics" }, topics.map(topic =>
      h("details", { class: "dev-trained-topic" },
        h("summary", null, h("span", null, topic.label), h("span", { class: "dev-trained-count" }, `${count(topic.mentions)} ${topic.mentions === 1 ? "mención" : "menciones"}`), icon("chevronDown", 14)),
        h("div", { class: "dev-trained-evidence" },
          h("p", { class: "dev-caption" }, `${count(topic.not_recommended)} menciones en reseñas que no recomiendan el juego. Leé los fragmentos para interpretar el motivo.`),
          (topic.examples || []).slice(0, 2).map(example => h("figure", null,
            h("blockquote", null, example.quote),
            h("figcaption", null, example.game, " · ", example.source === "steam" ? "Steam" : "GameTrack",
              example.recommended === true ? " · Recomienda" : example.recommended === false ? " · No recomienda" : ""))))))),
    ready && sampled > 0 && !topics.length && h("p", { class: "dev-caption" }, "No se detectaron temas con evidencia suficiente en esta muestra."),
    h("div", { class: "dev-trained-footer" }, method,
      ready && sampled > 0 && onAsk && h("button", { class: "btn btn-sm", type: "button", onClick: () => onAsk("¿Qué temas detecta la IA en estas reseñas?") }, "Consultar al asistente", icon("chevron", 14))));
}
