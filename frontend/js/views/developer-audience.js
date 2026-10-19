import { h, mount } from "../ui.js";
import { api } from "../api.js";
import { buildAudienceRanking } from "./developer-audience-data.js";

const number = value => Number(value || 0).toLocaleString("es-AR");
const pending = data => Number(data?.steam_histories?.pending_authors || 0) > 0
  || Number(data?.steam_histories?.identity_games_pending || 0) > 0;

export function audiencePanel(initial, scope) {
  const root = h("section", { class: "dev-audience", "aria-label": "Audiencias observadas" });
  let data = initial, running = false, started = false;
  let phase = !data || pending(data) ? "waiting" : "ready", notice = "";

  function render() {
    const loading = phase === "loading" || (phase === "waiting" && Boolean(scope));
    root.setAttribute("aria-busy", String(loading));
    const header = h("header", { class: "audience-heading" },
      h("span", { class: "audience-eyebrow" }, "Audiencias"),
      h("h2", null, "Los gustos que más se repiten"),
      h("p", null, "Las cinco categorías con más usuarios en la audiencia observada de Steam."));

    if (phase !== "ready") {
      mount(root, header, loading
        ? h("div", { class: "audience-loading", role: "status", "aria-live": "polite" },
          h("span", { class: "audience-loader", "aria-hidden": "true" }),
          h("h3", null, "Preparando tu audiencia"),
          h("p", null, "Reunimos los datos para mostrarte el ranking completo. La primera consulta puede tardar unos minutos."))
        : h("div", { class: "audience-state", role: "status", "aria-live": "polite" },
          h("h3", null, "Todavía no pudimos completar el ranking"),
          h("p", null, notice || "No pudimos cargar esta audiencia. Volvé a abrir el informe para reintentar."),
          scope && h("button", { class: "btn", type: "button", onClick: collect }, "Volver a intentar")));
      return;
    }

    const { categories, reflection } = buildAudienceRanking(data);
    const leadingSize = categories[0]?.size;
    mount(root, header,
      categories.length ? h("ol", { class: "audience-ranking", "aria-label": "Categorías con más usuarios" },
        categories.map(group => h("li", { class: `audience-rank${group.size === leadingSize ? " is-leading" : ""}`, value: group.rank },
          h("span", { class: "audience-position", "aria-label": `Puesto ${group.rank}` }, String(group.rank).padStart(2, "0")),
          h("div", { class: "audience-category" },
            h("span", { class: "audience-kind" }, group.kind === "genre" ? "Género" : "Rasgo"),
            h("h3", null, group.label)),
          h("div", { class: "audience-count" }, h("strong", null, number(group.size)), h("span", null, "usuarios")),
          h("div", { class: "audience-bar", "aria-hidden": "true" },
            h("span", { style: { width: `${group.relativeSize * 100}%` } })))))
        : h("div", { class: "audience-state" }, h("h3", null, "Todavía no hay grupos suficientes"),
          h("p", null, "Faltan datos para identificar gustos compartidos. El ranking aparecerá cuando haya categorías con al menos cinco usuarios.")),
      reflection && h("article", { class: "audience-reflection" },
        h("span", { class: "audience-reflection-label" }, "Lectura de tu audiencia"),
        h("h3", null, reflection.title), h("p", null, reflection.text),
        reflection.example && h("p", { class: "audience-example" }, `Una referencia entre quienes prefieren ${reflection.example.category}: `,
          h("a", { href: `#/juego/${reflection.example.game_id}` }, reflection.example.name), ".")),
      categories.length > 0 && h("p", { class: "audience-footnote" },
        "Basado en recomendaciones públicas de Steam. Una persona puede aparecer en varias categorías; esta muestra no representa a todo el mercado."));
  }

  async function collect() {
    if (running || !scope) return;
    running = true; phase = "loading"; notice = ""; render();
    try {
      // Conservamos cada lote para continuar si falla, pero publicamos una sola vista al terminar.
      for (let step = 0; step < 64; step++) {
        if (!root.isConnected) { started = false; return; }
        const result = await api.collectAudience(scope);
        if (!root.isConnected) { started = false; return; }
        if (!result?.audience?.steam_histories) throw new Error("Informe incompleto");
        data = result.audience;
        if (result.status === "ready" && !pending(data)) { phase = "ready"; return; }
        if (result.status === "busy") {
          notice = "Hay otra consulta de audiencias en curso. Volvé a intentar en unos momentos.";
          return;
        }
        if (result.status !== "more") break;
      }
      notice = "La consulta necesita un poco más de tiempo. Podés continuar sin perder los datos reunidos.";
    } catch {
      notice = "No pudimos terminar de reunir los datos. Volvé a intentar; el avance ya quedó guardado.";
    } finally {
      running = false;
      if (phase !== "ready") phase = "incomplete";
      render();
    }
  }

  root.load = () => {
    if (started || phase === "ready") return;
    started = true;
    queueMicrotask(() => {
      if (root.isConnected) collect();
      else started = false;
    });
  };
  render();
  return root;
}
