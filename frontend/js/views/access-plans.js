import { h, icon } from "../ui.js";

// Modelo de negocio, secciones 10.2.1 y 10.2.2: propuesta, no suscripciones activas.
export const ACCESS_PLANS = [
  { id: "gratis", role: "jugador", name: "Jugador gratuito", price: "Gratis", period: "Sin suscripción",
    description: "Descubrí qué jugar y organizá tu biblioteca.", symbol: "gamepad",
    features: ["Recomendaciones y GameTrackScore explicado", "Biblioteca, valoraciones y conexión con Steam", "Asistente individual y selección con un amigo", "Hasta 5 listas personalizadas, además de las básicas"], cta: "Continuar gratis" },
  { id: "premium", role: "jugador", name: "Jugador Premium", price: "USD 3", period: "/ mes · precio propuesto",
    description: "Más herramientas para elegir y jugar en grupo.", symbol: "sparkles",
    features: ["Todo lo propuesto para el plan gratuito", "Comparación personalizada entre dos juegos", "Grupos de hasta 5; Premium sólo para quien organiza", "Priorización de pendientes y hasta 50 listas personalizadas"], cta: "Continuar con Premium" },
  { id: "estudio", role: "desarrollador", name: "Plan Estudio", price: "USD 15", period: "/ mes por estudio · precio propuesto",
    description: "Entendé la recepción de tus juegos y tus referencias.", symbol: "chart",
    features: ["Informes de recepción y análisis por aspectos", "Temas frecuentes y fragmentos de evidencia", "Comparaciones y asistente para estudios", "Juegos del catálogo sin cargos por título"], cta: "Continuar como estudio" },
];

export function accessPlan(role, requested) {
  return ACCESS_PLANS.find(plan => plan.role === role && plan.id === requested)
    || ACCESS_PLANS.find(plan => plan.role === role);
}

export function accessPath(role, { plan, register = false } = {}) {
  const selected = accessPlan(role, plan);
  const query = new URLSearchParams({ plan: selected.id });
  if (register) query.set("modo", "registro");
  return `/acceso/${role === "desarrollador" ? "estudios" : "jugadores"}?${query}`;
}

export function accessHeader() {
  return h("header", { class: "auth-header" },
    h("a", { class: "auth-brand", href: "#/cuentas" }, h("img", { src: "/assets/brand/logo.png", width: "42", height: "42", alt: "" }), "GameTrack"),
    h("a", { class: "auth-explore", href: "#/catalogo" }, "Explorar catálogo", icon("chevron", 15)));
}

export function plansView({ query = new URLSearchParams() } = {}) {
  const register = query.get("modo") === "registro";
  return h("div", { class: "auth-page access-plans-page" }, accessHeader(),
    h("section", { class: "access-plans", "aria-labelledby": "access-title" },
      h("div", { class: "access-intro" }, h("p", { class: "auth-kicker" }, "JUGADORES Y ESTUDIOS"),
        h("h1", { id: "access-title" }, "Elegí tu espacio en GameTrack"),
        h("p", null, "Recomendaciones para jugadores. Análisis de videojuegos para desarrolladores y estudios.")),
      h("div", { class: "access-plan-grid" }, ACCESS_PLANS.map(plan =>
        h("article", { class: `access-plan access-plan-${plan.id}` },
          h("p", { class: "access-audience" }, icon(plan.symbol, 19), plan.role === "jugador" ? "PARA JUGADORES" : "PARA DESARROLLADORES"),
          h("h2", null, plan.name), h("p", { class: "access-description" }, plan.description),
          h("div", { class: "access-price" }, h("strong", null, plan.price), h("span", null, plan.period)),
          h("ul", null, plan.features.map(feature => h("li", null, icon("check", 15), h("span", null, feature)))),
          h("a", { class: "btn btn-primary access-plan-cta", href: `#${accessPath(plan.role, { plan: plan.id, register })}` }, plan.cta, icon("chevron", 17))))),
      h("p", { class: "access-proposal-note" }, "Precios propuestos, antes de impuestos. Elegir un plan no contrata una suscripción ni genera cobros."),
      h("nav", { class: "access-returning", "aria-label": "Acceso para cuentas existentes" },
        h("span", null, "¿Ya tenés cuenta?"),
        h("a", { href: `#${accessPath("jugador")}` }, "Ingresar como jugador", icon("chevron", 14)),
        h("a", { href: `#${accessPath("desarrollador")}` }, "Ingresar como estudio", icon("chevron", 14)))),
    h("footer", { class: "auth-footer" }, "El tipo de cuenta se define al registrarte."));
}
