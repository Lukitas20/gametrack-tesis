import { h, icon } from "./ui.js";

/** Radios nativos: elección accesible con teclado, sin mezclar perfiles y planes. */
export function experienceChoices({ value = null, onChange, form = null } = {}) {
  const group = h("fieldset", { class: "experience-choices" },
    h("legend", null, "¿Cómo querés usar GameTrack?"));
  for (const [role, title, description, symbol] of [
    ["jugador", "Como jugador", "Descubrí juegos, organizá tu biblioteca y recibí recomendaciones.", "gamepad"],
    ["desarrollador", "Como desarrollador / estudio", "Analizá reseñas, recepción y juegos de referencia.", "chart"],
  ]) {
    group.append(h("label", { class: "experience-choice" },
      h("input", { type: "radio", name: "experience", value: role, checked: role === value, required: true, form,
        onChange: event => { if (event.target.checked) onChange?.(role); } }),
      icon(symbol, 22), h("span", null, h("strong", null, title), h("small", null, description))));
  }
  return group;
}
