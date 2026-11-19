
import { h, SENTIMENT_LABEL, signed } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";

const BAR = 18;
const GAP = 2; // hueco de superficie entre rellenos contiguos
const ROUND = 4; // radio del extremo de dato
const ROW = 34;
const CHAR = 6.15; // ancho aproximado por carácter, para medir antes de etiquetar

export const SENTIMENTS = ["positivo", "neutro", "negativo"];

export const SENTIMENT_VAR = {
  positivo: "var(--positivo)",
  neutro: "var(--neutro)",
  negativo: "var(--negativo)",
};

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  return node;
}

function textWidth(text) {
  return String(text).length * CHAR;
}

/**
 * Rectángulo con sólo el extremo de dato redondeado.
 *
 * @param {"right"|"left"|"none"} side Extremo que lleva el radio.
 */
function barPath(x, y, width, height, side) {
  const w = Math.max(width, 0.5);
  const r = Math.min(ROUND, w / 2, height / 2);
  if (side === "none" || r <= 0.5) {
    return `M${x},${y}h${w}v${height}h${-w}z`;
  }
  if (side === "right") {
    return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${height - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}z`;
  }
  return `M${x + r},${y}h${w - r}v${height}h${-(w - r)}a${r},${r} 0 0 1 ${-r},${-r}v${-(height - 2 * r)}a${r},${r} 0 0 1 ${r},${-r}z`;
}


let tip = null;

function ensureTip() {
  if (!tip) {
    tip = h("div", { class: "chart-tip", role: "tooltip" });
    document.body.appendChild(tip);
  }
  return tip;
}

function showTip(event, title, lines) {
  const node = ensureTip();
  node.replaceChildren(
    h("strong", null, title),
    ...lines.map((line) => h("div", { class: "secondary" }, line)),
  );
  node.dataset.show = "true";
  const rect = node.getBoundingClientRect();
  const x = Math.min(event.clientX + 14, window.innerWidth - rect.width - 10);
  const y = Math.max(event.clientY - rect.height - 12, 8);
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
}

function hideTip() {
  if (tip) tip.dataset.show = "false";
}

function attachTip(target, hitRect, title, lines) {
  hitRect.addEventListener("mousemove", (event) => showTip(event, title, lines));
  hitRect.addEventListener("mouseleave", hideTip);
  hitRect.setAttribute("tabindex", "0");
  hitRect.addEventListener("focus", (event) => {
    const box = event.target.getBoundingClientRect();
    showTip({ clientX: box.left + box.width / 2, clientY: box.top + box.height }, title, lines);
  });
  hitRect.addEventListener("blur", hideTip);
  target.appendChild(hitRect);
}


export function legend(entries) {
  return h(
    "div",
    { class: "legend" },
    entries.map((entry) =>
      h(
        "span",
        { class: "legend-item" },
        h("span", { class: "legend-swatch", style: { background: entry.color } }),
        entry.label,
        entry.value !== undefined && h("span", { class: "legend-value" }, entry.value),
      ),
    ),
  );
}

export function tableView(headers, rows, summary = "Ver los datos en tabla") {
  return h(
    "details",
    { class: "table-view" },
    h("summary", null, summary),
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        null,
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            headers.map((header, index) =>
              h("th", { class: index === 0 ? null : "num" }, header),
            ),
          ),
        ),
        h(
          "tbody",
          null,
          rows.map((row) =>
            h(
              "tr",
              null,
              row.map((cell, index) =>
                h("td", { class: index === 0 ? null : "num" }, cell),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}


/**
 * Forma correcta para una escala ordenada (negativo → neutro → positivo):
 * el neutro se reparte a ambos lados del cero, de modo que la longitud a
 * izquierda y derecha se compara directamente entre filas. Una torta de tres
 * porciones no permite eso.
 *
 * @param {Array<{label:string, positivo:number, neutro:number, negativo:number, onClick?:Function}>} rows
 */
export function divergingStackedBar(rows, { labelWidth = 150, onRowClick = null } = {}) {
  const width = 720;
  const axisBand = 26;
  const height = rows.length * ROW + axisBand;
  const plotLeft = labelWidth;
  const plotWidth = width - plotLeft - 44;
  const centre = plotLeft + plotWidth / 2;

  let maxExtent = 0;
  const prepared = rows.map((row) => {
    const total = row.positivo + row.neutro + row.negativo;
    const share = (value) => (total ? (value / total) * 100 : 0);
    const positive = share(row.positivo);
    const neutral = share(row.neutro);
    const negative = share(row.negativo);
    maxExtent = Math.max(maxExtent, negative + neutral / 2, positive + neutral / 2);
    return { ...row, total, positive, neutral, negative };
  });
  const domain = Math.min(100, Math.max(25, Math.ceil(maxExtent / 25) * 25));
  const scale = (value) => (value / domain) * (plotWidth / 2);

  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    style: `min-width:${Math.max(460, labelWidth + 320)}px`,
  });

  const ticks = [-domain, -domain / 2, 0, domain / 2, domain];
  for (const tick of ticks) {
    const x = centre + scale(tick);
    svg.appendChild(
      svgEl("line", {
        x1: x,
        x2: x,
        y1: 0,
        y2: rows.length * ROW,
        class: tick === 0 ? "chart-baseline" : "chart-grid-line",
      }),
    );
    const label = svgEl("text", {
      x,
      y: rows.length * ROW + 16,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    label.textContent = `${Math.abs(tick)}%`;
    svg.appendChild(label);
  }

  prepared.forEach((row, index) => {
    const y = index * ROW + (ROW - BAR) / 2;
    const group = svgEl("g", { class: "chart-mark" });

    const halfNeutral = scale(row.neutral / 2);
    const negativeWidth = scale(row.negative);
    const positiveWidth = scale(row.positive);

    if (row.negative > 0) {
      group.appendChild(
        svgEl("path", {
          d: barPath(centre - halfNeutral - negativeWidth, y, negativeWidth - GAP, BAR, "left"),
          fill: SENTIMENT_VAR.negativo,
        }),
      );
    }
    if (row.neutral > 0) {
      group.appendChild(
        svgEl("path", {
          d: barPath(centre - halfNeutral, y, halfNeutral * 2, BAR, "none"),
          fill: SENTIMENT_VAR.neutro,
        }),
      );
    }
    if (row.positive > 0) {
      group.appendChild(
        svgEl("path", {
          d: barPath(centre + halfNeutral + GAP, y, positiveWidth - GAP, BAR, "right"),
          fill: SENTIMENT_VAR.positivo,
        }),
      );
    }

    const maxChars = Math.floor((labelWidth - 12) / CHAR);
    const text = svgEl("text", {
      x: labelWidth - 12,
      y: y + BAR / 2 + 4,
      class: "chart-axis-label",
      "text-anchor": "end",
    });
    text.textContent =
      row.label.length > maxChars ? `${row.label.slice(0, maxChars - 1)}…` : row.label;
    group.appendChild(text);

    const net = row.total ? (row.positivo - row.negativo) / row.total : 0;
    const value = svgEl("text", {
      x: width - 6,
      y: y + BAR / 2 + 4,
      class: "chart-value",
      "text-anchor": "end",
    });
    value.textContent = signed(net, 2);
    group.appendChild(value);

    svg.appendChild(group);

    const hit = svgEl("rect", {
      x: plotLeft,
      y: index * ROW,
      width: plotWidth,
      height: ROW,
      class: "chart-hit",
    });
    if (onRowClick) hit.addEventListener("click", () => onRowClick(row));
    attachTip(svg, hit, row.label, [
      `Positivas: ${row.positivo} (${Math.round(row.positive)}%)`,
      `Neutras: ${row.neutro} (${Math.round(row.neutral)}%)`,
      `Negativas: ${row.negativo} (${Math.round(row.negative)}%)`,
      `Neto: ${signed(net, 2)}`,
    ]);
  });

  return h("div", { class: "chart" }, svg);
}


/**
 * Desviación respecto de un cero: la forma correcta para un valor con signo.
 *
 * @param {Array<{label:string, value:number, note?:string, tip?:string[]}>} rows
 */
export function divergingBar(rows, { labelWidth = 130, domain = 1 } = {}) {
  const width = 720;
  const axisBand = 26;
  const height = rows.length * ROW + axisBand;
  const plotLeft = labelWidth;
  const plotWidth = width - plotLeft - 52;
  const centre = plotLeft + plotWidth / 2;
  const scale = (value) => (value / domain) * (plotWidth / 2);

  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    style: `min-width:${Math.max(460, labelWidth + 320)}px`,
  });

  for (const tick of [-domain, -domain / 2, 0, domain / 2, domain]) {
    const x = centre + scale(tick);
    svg.appendChild(
      svgEl("line", {
        x1: x,
        x2: x,
        y1: 0,
        y2: rows.length * ROW,
        class: tick === 0 ? "chart-baseline" : "chart-grid-line",
      }),
    );
    const label = svgEl("text", {
      x,
      y: rows.length * ROW + 16,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    label.textContent = tick === 0 ? "0" : signed(tick, 1);
    svg.appendChild(label);
  }

  rows.forEach((row, index) => {
    const y = index * ROW + (ROW - BAR) / 2;
    const magnitude = Math.abs(scale(row.value));
    const isPositive = row.value >= 0;
    const group = svgEl("g", { class: "chart-mark" });

    group.appendChild(
      svgEl("path", {
        d: isPositive
          ? barPath(centre + GAP / 2, y, magnitude, BAR, "right")
          : barPath(centre - GAP / 2 - magnitude, y, magnitude, BAR, "left"),
        fill: isPositive ? SENTIMENT_VAR.positivo : SENTIMENT_VAR.negativo,
      }),
    );

    const label = svgEl("text", {
      x: labelWidth - 12,
      y: y + BAR / 2 + 4,
      class: "chart-axis-label",
      "text-anchor": "end",
    });
    label.textContent = row.label;
    group.appendChild(label);

    const text = signed(row.value, 2);
    const outsideX = isPositive ? centre + magnitude + 8 : centre - magnitude - 8;
    const fitsOutside = isPositive
      ? outsideX + textWidth(text) < width - 2
      : outsideX - textWidth(text) > plotLeft + 4;

    let valueX;
    let anchor;
    if (fitsOutside) {
      valueX = outsideX;
      anchor = isPositive ? "start" : "end";
    } else {
      valueX = isPositive ? centre + magnitude - 8 : centre - magnitude + 8;
      anchor = isPositive ? "end" : "start";
    }

    const value = svgEl("text", {
      x: valueX,
      y: y + BAR / 2 + 4,
      class: "chart-value",
      "text-anchor": anchor,
      fill: fitsOutside ? null : "#fff",
    });
    value.textContent = text;
    group.appendChild(value);

    svg.appendChild(group);

    const hit = svgEl("rect", {
      x: plotLeft,
      y: index * ROW,
      width: plotWidth,
      height: ROW,
      class: "chart-hit",
    });
    attachTip(svg, hit, row.label, row.tip || [`Neto: ${signed(row.value, 2)}`]);
  });

  return h("div", { class: "chart" }, svg);
}


/**
 * Magnitud simple: una sola serie, un solo tono. No se usa una rampa de
 * valor sobre categorías sin orden natural, porque duplicaría en el color la
 * información que la longitud ya da.
 *
 * @param {Array<{label:string, value:number, tip?:string[]}>} rows
 */
export function barChart(rows, { labelWidth = 130, formatValue = (v) => v, color = "var(--accent)" } = {}) {
  const width = 720;
  const axisBand = 4;
  const height = rows.length * ROW + axisBand;
  const plotLeft = labelWidth;
  const maxValue = Math.max(...rows.map((row) => row.value), 1);
  const reserved = 52;
  const plotWidth = width - plotLeft - reserved;
  const scale = (value) => (value / maxValue) * plotWidth;

  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    style: `min-width:${Math.max(420, labelWidth + 280)}px`,
  });

  svg.appendChild(
    svgEl("line", {
      x1: plotLeft,
      x2: plotLeft,
      y1: 0,
      y2: rows.length * ROW,
      class: "chart-baseline",
    }),
  );

  rows.forEach((row, index) => {
    const y = index * ROW + (ROW - BAR) / 2;
    const barWidth = Math.max(scale(row.value), 2);
    const group = svgEl("g", { class: "chart-mark" });

    group.appendChild(
      svgEl("path", {
        d: barPath(plotLeft + 1, y, barWidth, BAR, "right"),
        fill: color,
      }),
    );

    const label = svgEl("text", {
      x: labelWidth - 12,
      y: y + BAR / 2 + 4,
      class: "chart-axis-label",
      "text-anchor": "end",
    });
    label.textContent = row.label;
    group.appendChild(label);

    const formatted = String(formatValue(row.value));
    const fitsInside = barWidth > textWidth(formatted) + 30;
    const value = svgEl("text", {
      x: fitsInside ? plotLeft + barWidth - 8 : plotLeft + barWidth + 8,
      y: y + BAR / 2 + 4,
      class: "chart-value",
      "text-anchor": fitsInside ? "end" : "start",
      fill: fitsInside ? "#fff" : null,
    });
    value.textContent = formatted;
    group.appendChild(value);

    svg.appendChild(group);

    const hit = svgEl("rect", {
      x: plotLeft,
      y: index * ROW,
      width: plotWidth + reserved - 4,
      height: ROW,
      class: "chart-hit",
    });
    attachTip(svg, hit, row.label, row.tip || [`${formatted}`]);
  });

  return h("div", { class: "chart" }, svg);
}


export function chartCard({ title, subtitle, chart, legendEntries, table, action }) {
  return h(
    "section",
    { class: "chart-card" },
    h(
      "div",
      { class: "card-head" },
      h(
        "div",
        null,
        h("h3", { class: "card-title" }, title),
        subtitle && h("p", { class: "card-sub" }, subtitle),
      ),
      action,
    ),
    h("div", { class: "chart-body" }, chart),
    legendEntries && legendEntries.length > 1 ? legend(legendEntries) : null,
    table,
  );
}

export function sentimentLegend(distribution) {
  return SENTIMENTS.map((key) => ({
    label: SENTIMENT_LABEL[key],
    color: SENTIMENT_VAR[key],
    value: distribution?.[key] ?? 0,
  }));
}
