/* Catálogo del desarrollador: informes comparables sin salir de la búsqueda. */
import { api } from '../api.js';
import { cover } from '../components.js';
import { state, isDeveloper } from '../store.js';
import { ASPECT_LABEL, h, icon, modalHead, mount, signed, spinnerBlock } from '../ui.js';
import { aspectComparisons, comparisonHighlights, reviewMetrics } from './developer-comparison-data.js';
import { trainedReviewInsights } from './developer-trained-insights.js';

const number = (value, digits = 1) => Number(value).toLocaleString('es-AR', { maximumFractionDigits: digits });
const missing = value => value === null || value === undefined;
const percent = value => missing(value) ? 'Sin datos' : `${number(value)} %`;
const decimal = value => missing(value) ? 'Sin datos' : number(value);

export function createDeveloperCatalog() {
  const owner = state.user?.id, studio = state.user?.studio?.trim();
  let studioRequest;
  function ownGames() {
    if (!studio) return Promise.resolve([]);
    if (!studioRequest) studioRequest = api.studioAnalytics(studio).then(report => report.juegos)
      .catch(error => { studioRequest = null; throw error; });
    return studioRequest;
  }
  return games => h('div', { class: 'game-grid dev-catalog-grid' }, games.map(game =>
    h('button', { type: 'button', class: 'dev-catalog-card', 'aria-label': `Analizar y comparar ${game.name}`,
      onClick: () => { if (isDeveloper() && state.user?.id === owner) openDeveloperGame(game, ownGames, studio); } },
    cover(game), h('div', { class: 'dev-catalog-info' }, h('h3', null, game.name),
      h('p', { class: 'dev-catalog-meta' }, game.developer || 'Estudio no informado'),
      h('p', { class: 'dev-catalog-meta' }, game.genres?.slice(0, 2).map(g => g.name).join(' · ') || 'Género pendiente'),
      h('span', { class: 'dev-catalog-action' }, icon('chart', 15), 'Métricas y comparación', icon('chevron', 14))))));
}

function openDeveloperGame(game, ownGames, studio) {
  const owner = state.user?.id, opener = document.activeElement;
  let revision = 0, catalog = null, reference = null, titles = [], ownId = null;
  let loadingReference = false, referenceError = null, catalogError = null, marketError = false;
  const reports = new Map(), details = new Map([[game.id, game]]);
  const dialog = h('dialog', { class: 'modal modal-wide dev-compare-dialog dev-page', 'aria-label': `Métricas y comparación de ${game.name}` });
  const content = h('div', { class: 'dev-compare-content', 'aria-live': 'polite' });
  const status = h('p', { class: 'dev-compare-status', role: 'status' }, 'Cargando los títulos de tu estudio…');
  const picker = h('select', { 'aria-label': 'Comparar con un juego de mi estudio', disabled: true },
    h('option', { value: '' }, 'Elegí uno de tus juegos'));
  const options = new Map();
  const controls = h('fieldset', { class: 'dev-compare-options' }, h('legend', null, 'Qué querés ver'));
  for (const [key, label, checked] of [['reception', 'Recepción', true], ['aspects', 'Aspectos', true], ['market', 'Comunidad y crítica', false], ['evidence', 'Fragmentos de reseñas', false]]) {
    const input = h('input', { type: 'checkbox', checked, onChange: render });
    options.set(key, input); controls.append(h('label', null, input, label));
  }
  const active = version => dialog.isConnected && dialog.open && state.user?.id === owner && version === revision;
  const close = () => dialog.close();
  const onRoute = () => close();
  dialog.addEventListener('close', () => {
    ++revision; window.removeEventListener('hashchange', onRoute); dialog.remove();
    if (opener?.isConnected) opener.focus();
  }, { once: true });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  });
  dialog.append(modalHead(game.name, 'Informe de desarrollador · elegí un título de tu estudio para compararlo.', close),
    h('div', { class: 'dev-compare-body' }, h('div', { class: 'dev-compare-toolbar' },
      h('label', { class: 'dev-compare-selector' }, 'Comparar con', picker), status), controls, content));
  document.body.append(dialog); dialog.showModal(); window.addEventListener('hashchange', onRoute);

  function section(title, caption, ...children) {
    return h('section', { class: 'dev-compare-section' }, h('h3', null, title), h('p', { class: 'dev-compare-note' }, caption), ...children);
  }
  function gameHeader(report, label) {
    const g = report.juego, detail = details.get(g.id) || {}, art = cover({ name: g.nombre, background_image: g.background_image || detail.background_image });
    return h('article', { class: 'dev-compare-game' }, art, h('div', { class: 'dev-compare-game-meta' },
      h('span', { class: 'dev-eyebrow' }, label), h('h3', null, g.nombre), h('p', null, g.desarrollador || 'Estudio no informado'),
      h('span', { class: 'dev-badge' }, `${number(report.resenas.analizadas)} reseñas analizadas`)));
  }
  function metricTable(rows, deltaTitle = 'Tu juego − catálogo') {
    return h('div', { class: 'dev-compare-table' }, h('table', null,
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Métrica'), h('th', { scope: 'col' }, catalog.juego.nombre),
        reference && h('th', { scope: 'col' }, reference.juego.nombre), reference && h('th', { scope: 'col' }, deltaTitle))),
      h('tbody', null, rows.map(([label, left, right, delta]) => h('tr', null,
        h('th', { scope: 'row' }, label), h('td', null, left), reference && h('td', null, right),
        reference && h('td', null, delta ? h('span', { class: 'dev-compare-delta' }, delta) : '—'))))));
  }
  function reception() {
    const left = reviewMetrics(catalog), right = reference && reviewMetrics(reference);
    const delta = (key, suffix = '') => !right || missing(left[key]) || missing(right[key]) ? null : `${signed(right[key] - left[key], key === 'net' ? 2 : 1)}${suffix}`;
    return section('Recepción de los jugadores', 'Muestra guardada en GameTrack. Los porcentajes se calculan sobre las reseñas analizadas; no representan todo Steam.',
      metricTable([
        ['Reseñas analizadas', number(left.analyzed), right && number(right.analyzed), null],
        ['Textos positivos', percent(left.positive), right && percent(right.positive), delta('positive', ' p.p.')],
        ['Textos negativos', percent(left.negative), right && percent(right.negative), delta('negative', ' p.p.')],
        ['Balance del sentimiento (−1 a +1)', missing(left.net) ? 'Sin datos' : signed(left.net, 2), right && (missing(right.net) ? 'Sin datos' : signed(right.net, 2)), delta('net')],
        ['Cobertura del análisis', percent(left.coverage), right && percent(right.coverage), delta('coverage', ' p.p.')],
        ['Reseñas pendientes', number(left.pending), right && number(right.pending), null],
      ]), h('p', { class: 'dev-compare-note' }, 'p.p. = puntos porcentuales. Una muestra mayor no implica que el juego sea mejor.'));
  }
  function aspects() {
    const rows = aspectComparisons(catalog, reference);
    const cell = a => a ? h('div', null, h('strong', null, signed(a.sentimiento_neto, 2)), h('small', null, `${number(a.menciones)} ${a.menciones === 1 ? 'mención' : 'menciones'}${a.menciones < 5 ? ' · muestra pequeña' : ''}`)) : 'Sin menciones';
    return section('Qué dicen de cada aspecto', 'Balance positivo menos negativo, de −1 a +1. Las menciones pertenecen a cada aspecto, no son jugadores únicos.',
      metricTable(rows.map(row => [ASPECT_LABEL[row.key] || row.key, cell(row.catalog), cell(row.own),
        missing(row.delta) ? 'No comparable' : h('span', null, signed(row.delta, 2), !row.comparable && h('small', null, 'Muestra pequeña'))])),
      h('p', { class: 'dev-compare-note' }, 'Sin menciones significa que falta evidencia; no equivale a un balance de cero.'),
      h('div', { class: 'dev-trained-comparison' },
        trainedReviewInsights(catalog.trained_ai, { title: `Temas detectados · ${catalog.juego.nombre}`, compact: true }),
        reference && trainedReviewInsights(reference.trained_ai, { title: `Temas detectados · ${reference.juego.nombre}`, compact: true })),
      catalog.trained_ai && h('p', { class: 'dev-compare-note' }, 'Cada título usa su propia muestra reciente. Compará las menciones junto con la cantidad de reseñas; los conteos no son una nota de calidad.'));
  }
  function market() {
    const left = details.get(catalog.juego.id), right = reference && details.get(reference.juego.id);
    const rating = report => report.juego.cantidad_ratings_local ? `${decimal(report.juego.rating_local_promedio)} / 5` : 'Sin votos';
    const publicRating = detail => !detail ? 'Sin datos' : detail.ratings_count ? `${decimal(detail.avg_rating)} / 5` : 'Sin votos';
    return section('Comunidad y crítica', 'Metascore, índice público del catálogo y valoraciones de GameTrack son fuentes distintas del análisis del texto.',
      metricTable([
        ['Metascore de Metacritic', missing(left?.metacritic) ? 'Sin dato' : `${left.metacritic} / 100`, missing(right?.metacritic) ? 'Sin dato' : `${right.metacritic} / 100`, null],
        ['Nota pública del catálogo', publicRating(left), publicRating(right), null],
        ['Votos públicos del catálogo', left ? number(left.ratings_count || 0) : 'Sin dato', right ? number(right.ratings_count || 0) : 'Sin dato', null],
        ['Nota en GameTrack', rating(catalog), reference && rating(reference), null],
        ['Votos en GameTrack', number(catalog.juego.cantidad_ratings_local), reference && number(reference.juego.cantidad_ratings_local), null],
      ]), marketError && h('p', { class: 'dev-compare-note', role: 'status' }, 'Algunos datos públicos no se pudieron consultar; el informe de reseñas sigue disponible.'));
  }
  function evidence() {
    const block = report => {
      const entries = Object.entries(report.citas_negativas || {}).filter(([, quotes]) => quotes.length);
      return h('div', null, h('h4', null, report.juego.nombre), entries.length ? entries.map(([key, quotes]) =>
        h('details', { class: 'dev-compare-evidence' }, h('summary', null, ASPECT_LABEL[key] || key, ` · ${quotes.length} ${quotes.length === 1 ? 'fragmento' : 'fragmentos'}`),
          quotes.slice(0, 3).map(quote => h('blockquote', null, quote)))) : h('p', { class: 'dev-compare-note' }, 'Sin fragmentos negativos guardados.'));
    };
    return section('Evidencia para revisar', 'Fragmentos de la muestra que el análisis local clasificó como negativos. Revisalos en contexto antes de decidir.',
      h('div', { class: 'dev-compare-games' }, block(catalog), reference && block(reference)));
  }
  function insights() {
    const highlights = comparisonHighlights(catalog, reference);
    return section('Qué podés sacar de esta comparación', 'Diferencias descriptivas de las muestras; no explican por sí solas las causas ni predicen ventas.',
      h('ul', { class: 'dev-compare-insights' }, highlights.length ? highlights.map(row => h('li', null,
        `${ASPECT_LABEL[row.key] || row.key}: ${row.delta > 0 ? reference.juego.nombre : catalog.juego.nombre} tiene un balance más positivo (${number(Math.abs(row.delta), 2)} de diferencia). `,
        `El catálogo tiene ${number(row.catalog.menciones)} menciones y tu referencia ${number(row.own.menciones)}.`)) :
        h('li', null, 'No hay diferencias destacables con al menos cinco menciones por aspecto en ambos juegos. No significa que sean equivalentes.')),
      h('p', { class: 'dev-compare-note' }, `Compará junto con los tamaños de muestra: ${number(catalog.resenas.analizadas)} y ${number(reference.resenas.analizadas)} reseñas analizadas.`));
  }
  function render() {
    if (!catalog) {
      mount(content, catalogError ? h('div', { role: 'alert' }, h('p', null, catalogError), h('button', { class: 'btn', onClick: loadCatalogReport }, 'Reintentar informe')) : spinnerBlock('Leyendo las métricas del juego…'));
      return;
    }
    const footer = h('div', { class: 'dev-compare-footer' }, h('a', { class: 'btn', href: `#/dev/juego/${catalog.juego.id}` }, 'Abrir informe completo', icon('chevron', 14)),
      reference && h('a', { class: 'btn', href: `#/dev/juego/${reference.juego.id}` }, 'Informe de mi referencia', icon('chevron', 14)));
    mount(content, h('div', { class: 'dev-compare-games', style: !reference ? { gridTemplateColumns: '1fr' } : null },
      gameHeader(catalog, 'Juego del catálogo'), reference && gameHeader(reference, 'Referencia de tu estudio')),
      loadingReference && h('p', { class: 'dev-compare-loading', role: 'status' }, 'Cargando la referencia…'),
      referenceError && h('div', { role: 'alert' }, h('p', null, referenceError), h('button', { class: 'btn', onClick: loadReference }, 'Reintentar comparación')),
      options.get('reception').checked && reception(), options.get('aspects').checked && aspects(),
      options.get('market').checked && market(), options.get('evidence').checked && evidence(),
      ![...options.values()].some(input => input.checked) && h('p', { class: 'dev-compare-note' }, 'Marcá al menos un grupo de métricas para mostrarlo.'),
      reference && insights(), footer);
  }
  async function reportFor(id) {
    if (reports.has(id)) return reports.get(id);
    const [report, detail] = await Promise.all([api.gameAnalytics(id), api.game(id).catch(() => null)]);
    reports.set(id, report); if (detail) details.set(id, detail); else marketError = true;
    return report;
  }
  async function loadReference() {
    ownId = picker.value ? Number(picker.value) : null;
    const version = ++revision; reference = null; referenceError = null; loadingReference = Boolean(ownId);
    render();
    if (!ownId) return;
    if (ownId === game.id || !titles.some(title => title.id === ownId)) { loadingReference = false; render(); return; }
    try {
      const report = await reportFor(ownId);
      if (active(version)) reference = report;
    } catch (error) { if (active(version)) referenceError = error.message || 'No pudimos cargar esta referencia.'; }
    finally { if (active(version)) { loadingReference = false; render(); } }
  }
  picker.addEventListener('change', loadReference);
  async function loadTitles() {
    try {
      titles = (await ownGames()).filter(title => title.id !== game.id);
      if (!dialog.isConnected || state.user?.id !== owner) return;
      mount(picker, h('option', { value: '' }, 'Elegí uno de tus juegos'), titles.map(title => h('option', { value: String(title.id) }, `${title.nombre} · ${number(title.resenas_analizadas)} reseñas analizadas`)));
      picker.disabled = !titles.length;
      mount(status, studio ? `Estudio de tu cuenta: ${studio}. ` : 'Tu cuenta no tiene un estudio configurado. ',
        titles.length ? 'Elegí una referencia para ver las diferencias.' : 'No hay otro título disponible para comparar.',
        !studio && h('a', { href: '#/perfil' }, 'Configurar mi perfil'));
    } catch (error) {
      if (dialog.isConnected && state.user?.id === owner) mount(status, 'No pudimos cargar los juegos de tu estudio. ', h('button', { class: 'btn btn-sm', onClick: loadTitles }, 'Reintentar'));
    }
  }
  async function loadCatalogReport() {
    const current = () => dialog.isConnected && dialog.open && state.user?.id === owner;
    catalogError = null; render();
    try { const report = await reportFor(game.id); if (current()) { catalog = report; render(); } }
    catch (error) { if (current()) { catalogError = error.message || 'No pudimos consultar el informe.'; render(); } }
  }
  render(); loadCatalogReport(); loadTitles();
}
