/* Onboarding de preferencias.
 *
 * Es la pieza que hace visible el arranque en frío: un usuario sin historial
 * no tiene señal de comportamiento, pero al elegir géneros el motor puede
 * recomendar por contenido en lugar de caer en popularidad.
 */

import { api } from "../api.js";
import { navigate } from "../router.js";
import { loadCatalog, refreshUser, state } from "../store.js";
import { h, icon, openModal, modalHead, toast } from "../ui.js";

export function openOnboarding({ onDone = null } = {}) {
  openModal((close) => {
    const selected = new Set();
    const container = h("div");

    function render() {
      const chips = state.genres.map((genre) =>
        h(
          "button",
          {
            class: "chip",
            "aria-pressed": String(selected.has(genre.id)),
            style: { padding: "var(--s-2) var(--s-3)", fontSize: "var(--fs-sm)" },
            onClick: () => {
              if (selected.has(genre.id)) selected.delete(genre.id);
              else selected.add(genre.id);
              render();
            },
          },
          selected.has(genre.id) ? icon("check", 12) : null,
          genre.name,
        ),
      );

      container.replaceChildren(
        modalHead(
          "¿Qué géneros te gustan?",
          "Elegí al menos uno. Con esto ya podemos recomendarte por contenido, sin necesidad de que hayas valorado nada.",
          close,
        ),
        h("div", { class: "row", style: { gap: "var(--s-2)" } }, chips),
        h(
          "div",
          { class: "row", style: { marginTop: "var(--s-6)", justifyContent: "flex-end" } },
          h("button", { class: "btn btn-ghost", onClick: close }, "Después"),
          h(
            "button",
            {
              class: "btn btn-primary",
              disabled: selected.size === 0,
              onClick: async () => {
                try {
                  await api.setPreferences([...selected]);
                  await refreshUser();
                  toast("Preferencias guardadas");
                  close();
                  if (onDone) onDone();
                  else navigate("/recomendaciones");
                } catch (error) {
                  toast(error.message, "error");
                }
              },
            },
            `Guardar${selected.size ? ` (${selected.size})` : ""}`,
          ),
        ),
      );
    }

    loadCatalog().then(render);
    render();
    return container;
  });
}

/** Aviso que aparece en la vista de recomendaciones durante el cold start. */
export function coldStartNotice(response) {
  const hasPreferences = response.items.some((item) => item.source === "contenido");

  return h(
    "div",
    { class: "notice" },
    icon("info", 16),
    h(
      "div",
      null,
      h("strong", null, "Arranque en frío. "),
      `Todavía no valoraste ningún juego (${response.history_size} de ${3} necesarios para el filtrado colaborativo), `,
      hasPreferences
        ? "así que las sugerencias salen de los géneros que elegiste, por similitud de contenido."
        : "y tampoco declaraste preferencias, así que se recurre al piso: los mejor valorados del catálogo.",
      " ",
      h(
        "button",
        {
          class: "btn btn-sm btn-primary",
          style: { marginTop: "var(--s-3)" },
          onClick: () => openOnboarding({ onDone: () => navigate("/recomendaciones") }),
        },
        icon("sparkles", 13),
        hasPreferences ? "Cambiar mis géneros" : "Elegir mis géneros",
      ),
    ),
  );
}
