const list = new Intl.ListFormat("es", {style:"long", type:"conjunction"});
const compareKey = (a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0;

function sharedExample(group, minimum) {
  return (group.examples || []).filter(game =>
    Number.isInteger(game.game_id) && game.game_id > 0 &&
    typeof game.name === "string" && game.name.trim() &&
    Number.isInteger(game.authors) && game.authors >= minimum && game.authors <= group.size
  ).map(game => ({...game})).sort((a, b) => b.authors - a.authors || a.game_id - b.game_id)[0] || null;
}

function reflectionFor(groups, minimum) {
  if (!groups.length) return null;
  const leaders = groups.filter(group => group.size === groups[0].size);
  const followers = groups.filter(group => group.size < groups[0].size).slice(0, 2);
  const title = leaders.length === 1
    ? `${leaders[0].label} lidera los gustos observados`
    : leaders.length <= 3
      ? `${list.format(leaders.map(group => group.label))} comparten el primer puesto`
      : "Varios gustos comparten el primer puesto";
  let text;
  if (leaders.length > 1) {
    text = "No hay una única categoría dominante en la muestra. Compará qué valoran estos grupos antes de priorizar una dirección de diseño.";
  } else if (followers.length) {
    const labels = list.format(followers.map(group => group.label));
    text = `${labels} ${followers.length === 1 ? "también tiene" : "también tienen"} una presencia destacada. Estos gustos son un punto de partida para revisar qué valoran esos jugadores en tus juegos.`;
  } else {
    text = "Es la única categoría con evidencia suficiente para aparecer en este informe. Revisá qué valoran estos jugadores antes de ampliar las conclusiones a otros gustos.";
  }
  // An example belongs to one observed group; it does not imply all leaders share it.
  const example = sharedExample(leaders[0], minimum);
  return {title, text, example:example ? {...example, category:leaders[0].label} : null};
}

export function buildAudienceRanking(report) {
  const minimum = Number.isInteger(report?.minimum_group) ? Math.max(5, report.minimum_group) : 5;
  const seen = new Set();
  const groups = (report?.steam_histories?.groups || []).filter(group =>
    group && !group.suppressed && ["genre", "tag"].includes(group.kind) &&
    typeof group.label === "string" && group.label.trim() &&
    Number.isInteger(group.size) && group.size >= minimum
  ).map(group => ({...group, key:group.key || `${group.kind}:${group.label}`}))
    .sort((a, b) => b.size - a.size || compareKey(a, b))
    .filter(group => {if (seen.has(group.key)) return false;seen.add(group.key);return true;});
  const categories = groups.slice(0, 5).map((group, index) => ({
    ...group,
    reception:{...group.reception},
    examples:(group.examples || []).map(game => ({...game})),
    position:index + 1,
    rank:groups.findIndex(candidate => candidate.size === group.size) + 1,
    relativeSize:group.size / groups[0].size,
  }));
  return {categories, reflection:reflectionFor(groups, minimum)};
}
