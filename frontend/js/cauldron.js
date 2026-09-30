/* Caldero animado: la carga del asistente "¿Qué jugamos hoy?".
 *
 * Port a JS plano de la pieza diseñada en Claude Design ("Cauldron Potion
 * Animation"). El original es React sobre un motor de composición; acá no
 * hay build step ni dependencias, así que se porta el modelo y no el
 * framework: la escena es una FUNCIÓN PURA DEL TIEMPO que posiciona divs,
 * y eso sobrevive intacto a la traducción. Se conservan tal cual el espacio
 * de coordenadas (1080×1080), el envelope de intensidad, los easings y las
 * constantes de la pieza, así que ajustar el original y re-portar sigue
 * siendo mecánico.
 *
 * El ciclo dura 10 s y recorre cuatro momentos: el caldero en calma, tres
 * ingredientes que caen, el hervor que sube y un estallido que vuelve a la
 * calma. El bucle se corta solo cuando el nodo sale del DOM
 * (``isConnected``), así que cambiar de pantalla no deja un
 * requestAnimationFrame corriendo para siempre.
 */

const LOOP = 10; // segundos del ciclo completo
const CYCLE = 2.5; // divide el bucle en 4 partes iguales
const TWO_PI = Math.PI * 2;
const STAGE = 1080; // lado del espacio de coordenadas original

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const easeInQuad = (t) => t * t;

/** Tween de un tramo: devuelve `from` antes de `start` y `to` después de `end`. */
function animate({ from = 0, to = 1, start = 0, end = 1, ease }) {
  return (t) => {
    if (t <= start) return from;
    if (t >= end) return to;
    return from + (to - from) * ease((t - start) / (end - start));
  };
}

const wave = (T, freq = 1, phase = 0) =>
  (Math.sin(((T * TWO_PI) / CYCLE) * freq + phase) + 1) / 2;

/* Intensidad del brujeo: baja al principio, sube con los ingredientes,
 * estalla cerca del final y vuelve al valor de apertura en T=10 para que la
 * costura del bucle no se note. */
const ENVELOPE = [
  [0, 0.15], [2, 0.15], [2.5, 0.24], [3.7, 0.30], [4.9, 0.38],
  [5, 0.42], [8, 0.85], [8.6, 1.0], [9.4, 0.45], [10, 0.15],
];

function envelopeAt(T) {
  for (let i = 0; i < ENVELOPE.length - 1; i += 1) {
    const [t0, v0] = ENVELOPE[i];
    const [t1, v1] = ENVELOPE[i + 1];
    if (T >= t0 && T <= t1) return v0 + ((v1 - v0) * (T - t0)) / (t1 - t0);
  }
  return ENVELOPE[ENVELOPE.length - 1][1];
}

const INGREDIENTS = [
  { drop: 2.2, x: 468, shape: "diamond", size: 30, color: "oklch(62% 0.18 300)" },
  { drop: 3.4, x: 540, shape: "circle", size: 26, color: "oklch(55% 0.20 322)" },
  { drop: 4.6, x: 612, shape: "leaf", size: 34, color: "#8b6cf0" },
];

const SPARKLES = Array.from({ length: 10 }, (_, i) => ({
  x: 540 + Math.cos((i / 10) * TWO_PI) * (200 + (i % 3) * 40),
  y: 470 + Math.sin((i / 10) * TWO_PI) * (190 + (i % 2) * 50) - 40,
  phase: i * 0.63,
  size: 6 + (i % 3) * 3,
}));

const RUNES = Array.from({ length: 5 }, (_, i) => ({
  x: 470 + i * 35,
  y: 660 + Math.sin(i * 0.9) * 10,
  phase: i * 0.5,
}));

const FIRE_SHOTS = [0.8, 3.9, 6.4, 8.7];
const FIREWORK_ARMS = 8;
const GLOW = "#a855f7"; // mismo violeta que el logo
const STEAM_PERIOD = 5;

/** div absoluto con estilos base; `el.style` se actualiza después por frame. */
function box(parent, style) {
  const node = document.createElement("div");
  node.style.position = "absolute";
  Object.assign(node.style, style);
  parent.appendChild(node);
  return node;
}

/**
 * Construye la escena una sola vez y devuelve la función que la actualiza
 * para un tiempo dado. Separar construcción de actualización es lo que hace
 * barato el bucle: por frame sólo se tocan estilos, nunca el árbol.
 */
function buildScene(root) {
  const breathe = box(root, {
    inset: 0,
    transformOrigin: "540px 560px",
  });

  const steam = [0, 1, 2].map(() =>
    box(breathe, {
      width: "120px",
      height: "60px",
      borderRadius: "50%",
      background: "oklch(75% 0.03 300)",
      filter: "blur(18px)",
    }),
  );

  const fire = [-52, -16, 20, 54].map((dx, i) =>
    box(breathe, {
      background: `linear-gradient(0deg, ${
        i % 2 === 0 ? "oklch(70% 0.19 55)" : "oklch(75% 0.17 80)"
      }, transparent 85%)`,
      borderRadius: "50% 50% 50% 50% / 65% 65% 35% 35%",
      filter: "blur(1px)",
      transformOrigin: "bottom center",
      dataset: undefined,
    }),
  );
  fire.forEach((node) => {
    node.dataset.dx = "";
  });

  // Sombra, patas y cuerpo: estáticos, se dibujan una vez.
  box(breathe, {
    left: `${540 - 190}px`, top: "745px", width: "380px", height: "46px",
    borderRadius: "50%", background: "oklch(3% 0.01 300 / 0.55)", filter: "blur(6px)",
  });
  [-95, 0, 95].forEach((dx) => {
    box(breathe, {
      left: `${540 + dx - 7}px`, top: "715px", width: "14px", height: "55px",
      background: "oklch(16% 0.012 285)", borderRadius: "4px",
      transform: `rotate(${dx === 0 ? 0 : dx > 0 ? 12 : -12}deg)`,
    });
  });
  box(breathe, {
    left: `${540 - 140}px`, top: "565px", width: "280px", height: "190px",
    background: "oklch(26% 0.02 288)", borderRadius: "18% 18% 46% 46%",
    boxShadow:
      "inset 0 -18px 40px rgba(0,0,0,0.5), inset 0 14px 24px rgba(255,255,255,0.06)",
  });

  const runes = RUNES.map((r) =>
    box(breathe, {
      left: `${r.x - 5}px`, top: `${r.y - 5}px`, width: "10px", height: "10px",
      borderRadius: "50%", background: GLOW,
    }),
  );

  // Borde del caldero: por encima del cuerpo y las runas.
  box(breathe, {
    left: `${540 - 155}px`, top: "540px", width: "310px", height: "48px",
    borderRadius: "50%", background: "oklch(34% 0.02 288)",
  });

  const opening = box(breathe, {
    left: `${540 - 118}px`, top: "549px", width: "236px", height: "32px",
    borderRadius: "50%",
    background: `radial-gradient(ellipse at 50% 50%, ${GLOW}, transparent 75%)`,
  });
  const flash = box(breathe, {
    left: `${540 - 118}px`, top: "549px", width: "236px", height: "32px",
    borderRadius: "50%", background: GLOW, filter: "blur(20px)",
  });

  const fireworks = [];
  FIRE_SHOTS.forEach((_, si) => {
    for (let i = 0; i < FIREWORK_ARMS; i += 1) {
      fireworks.push({
        node: box(breathe, { borderRadius: "50%", opacity: "0" }),
        si,
        i,
        color: i % 2 === 0 ? GLOW : "#fbbf24",
      });
    }
  });

  const bubbles = Array.from({ length: 7 }, () =>
    box(breathe, { borderRadius: "50%", background: GLOW }),
  );

  const ingredients = INGREDIENTS.map((item) => {
    const shape = item.shape === "diamond"
      ? { background: item.color, transform: "rotate(45deg)", borderRadius: "4px" }
      : item.shape === "circle"
      ? { background: item.color, borderRadius: "50%" }
      : { background: item.color, borderRadius: "0% 60% 0% 60%", transform: "rotate(-20deg)" };
    return {
      item,
      node: box(breathe, {
        left: `${item.x - item.size / 2}px`,
        width: `${item.size}px`,
        height: `${item.size}px`,
        ...shape,
      }),
      splash: box(breathe, {
        left: `${item.x - 40}px`, top: "540px", width: "80px", height: "24px",
        borderRadius: "50%", border: `2px solid ${GLOW}`, transformOrigin: "center",
      }),
    };
  });

  const sparkles = SPARKLES.map((s) =>
    box(breathe, {
      left: `${s.x - s.size / 2}px`, top: `${s.y - s.size / 2}px`,
      width: `${s.size}px`, height: `${s.size}px`,
      background: GLOW, borderRadius: "2px",
    }),
  );

  return function render(T) {
    const intensity = envelopeAt(T);
    breathe.style.transform = `scale(${
      1 + 0.015 * Math.sin((T * TWO_PI) / 5) + intensity * 0.02
    })`;

    steam.forEach((node, i) => {
      const off = (i * STEAM_PERIOD) / 3;
      const p = ((((T + off) % STEAM_PERIOD) + STEAM_PERIOD) % STEAM_PERIOD) / STEAM_PERIOD;
      const y = 555 - p * 410;
      const x = 540 + Math.sin(p * TWO_PI + i) * 30 + (i - 1) * 46;
      node.style.left = `${x - 60}px`;
      node.style.top = `${y - 30}px`;
      node.style.opacity = String(Math.sin(p * Math.PI) * 0.32 * (0.5 + intensity * 0.6));
      node.style.transform = `scale(${0.6 + p * 0.9})`;
    });

    fire.forEach((node, i) => {
      const dx = [-52, -16, 20, 54][i];
      const flick = wave(T, 3, i * 1.7);
      const h = 46 + flick * 26;
      const w = 26 + flick * 8;
      node.style.left = `${540 + dx - w / 2}px`;
      node.style.top = `${800 - h}px`;
      node.style.width = `${w}px`;
      node.style.height = `${h}px`;
      node.style.opacity = String(0.75 + flick * 0.25);
      node.style.transform = `scaleY(${0.85 + flick * 0.3})`;
    });

    runes.forEach((node, i) => {
      const pulse = wave(T, 1, RUNES[i].phase);
      const b = intensity * (0.4 + pulse * 0.6);
      node.style.opacity = String(0.4 + b * 0.6);
      node.style.boxShadow = `0 0 ${8 + b * 20}px ${3 + b * 5}px ${GLOW}`;
    });

    opening.style.opacity = String(0.55 + intensity * 0.45);
    opening.style.filter = `blur(${1 + intensity * 3}px)`;

    const burst = clamp((intensity - 0.7) / 0.3, 0, 1);
    flash.style.opacity = String(burst * 0.6);
    flash.style.transform = `scale(${1 + burst * 2.4})`;

    fireworks.forEach(({ node, si, i, color }) => {
      const local = T - FIRE_SHOTS[si];
      const life = 0.7;
      if (local < -0.02 || local > life) {
        node.style.opacity = "0";
        return;
      }
      const prog = clamp(local / life, 0, 1);
      const eased = 1 - Math.pow(1 - prog, 3);
      const angle =
        -Math.PI / 2 + (i / (FIREWORK_ARMS - 1) - 0.5) * Math.PI * 1.3 + si * 0.4;
      const dist = 18 + eased * 130;
      const size = 6 * (1 - prog * 0.5);
      node.style.left = `${540 + Math.cos(angle) * dist - size / 2}px`;
      node.style.top = `${555 + Math.sin(angle) * dist - eased * 18 - size / 2}px`;
      node.style.width = `${size}px`;
      node.style.height = `${size}px`;
      node.style.background = color;
      node.style.opacity = String((1 - prog) * 0.9);
      node.style.boxShadow = `0 0 6px ${color}`;
    });

    bubbles.forEach((node, i) => {
      const phase = i * (CYCLE / bubbles.length);
      const local = ((((T + phase) % CYCLE) + CYCLE) % CYCLE) / CYCLE;
      const y = 558 - local * (30 + intensity * 20);
      const x = 540 + (i - 3) * 20 + Math.sin(T * 2 + i) * 4;
      const size = 5 + intensity * 7;
      node.style.left = `${x - size / 2}px`;
      node.style.top = `${y - size / 2}px`;
      node.style.width = `${size}px`;
      node.style.height = `${size}px`;
      node.style.opacity = String(Math.sin(local * Math.PI) * (0.35 + intensity * 0.5));
    });

    ingredients.forEach(({ item, node, splash }) => {
      const start = item.drop - 0.55;
      const y = animate({
        from: -70, to: 556, start, end: item.drop, ease: easeInQuad,
      })(T);
      const enterOp = clamp((T - (start - 0.15)) / 0.15, 0, 1);
      const exitOp = 1 - clamp((T - item.drop) / 0.18, 0, 1);
      node.style.top = `${y}px`;
      node.style.opacity = String(Math.max(0, Math.min(enterOp, exitOp)));

      const splashT = clamp((T - item.drop) / 0.4, 0, 1);
      splash.style.opacity = String((1 - splashT) * (T >= item.drop ? 1 : 0) * 0.7);
      splash.style.transform = `scale(${0.3 + splashT * 1.9})`;
    });

    sparkles.forEach((node, i) => {
      const twinkle = wave(T, 1, SPARKLES[i].phase);
      node.style.opacity = String(clamp(twinkle * (0.15 + intensity * 0.75) * 2, 0, 1));
      node.style.transform = `rotate(45deg) scale(${0.6 + twinkle * 0.6})`;
    });
  };
}

/**
 * Caldero animado para la carga del asistente.
 *
 * `size` es el lado en píxeles: la escena se dibuja siempre en 1080×1080 y
 * se escala con `transform`, así que las coordenadas del diseño original no
 * se tocan y la pieza es nítida en cualquier tamaño.
 */
export function cauldronScene(size = 340) {
  const wrap = document.createElement("div");
  wrap.className = "gt-cauldron";
  wrap.style.width = `${size}px`;
  wrap.style.height = `${size}px`;

  const stage = document.createElement("div");
  stage.className = "gt-cauldron-stage";
  stage.style.width = `${STAGE}px`;
  stage.style.height = `${STAGE}px`;
  stage.style.transform = `scale(${size / STAGE})`;
  wrap.appendChild(stage);

  const render = buildScene(stage);

  // Con "reducir movimiento" activado se dibuja un frame fijo del momento de
  // más brillo y no se arranca el bucle: la pieza sigue comunicando que algo
  // está pasando, sin animación que pueda molestar.
  const quiet = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  if (quiet) {
    render(8.6);
    return wrap;
  }

  const started = performance.now();
  let frame = 0;
  const tick = (now) => {
    // El nodo se va del DOM al cambiar de pantalla: ahí se corta el bucle en
    // vez de seguir consumiendo frames por una animación que ya nadie ve.
    if (!wrap.isConnected && frame > 0) return;
    render((((now - started) / 1000) % LOOP + LOOP) % LOOP);
    frame += 1;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  return wrap;
}
