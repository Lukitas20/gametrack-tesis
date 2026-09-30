// Observa también las tarjetas que llegan de la API después de montar la vista.
export function initMotion(root) {
  if (!("IntersectionObserver" in window)) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const seen = new WeakSet();
  const targets = ".gts-card,.gts-heading,.section-head,.game-card,.recommendation-card,.quiz-result-card,.friend-row,.steam-friend,.play-question-panel,.play-plan";
  const disabled = () => reduced.matches || document.body.classList.contains("motion-paused");
  const show = node => { node.classList.add("is-revealed"); observer.unobserve(node); };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) show(entry.target);
  }, { threshold: 0.06, rootMargin: "0px 0px -24px 0px" });
  function scan(node) {
    if (!(node instanceof Element) || !node.isConnected) return;
    const nodes = [...(node.matches(targets) ? [node] : []), ...node.querySelectorAll(targets)];
    for (const item of nodes) {
      if (seen.has(item) || item.closest(".recommendation-card,.gts-card") && !item.matches(".recommendation-card,.gts-card")) continue;
      seen.add(item);
      if (disabled()) continue;
      item.style.setProperty("--reveal-delay", `${Math.min([...item.parentElement.children].indexOf(item) % 4, 3) * 55}ms`);
      item.classList.add("scroll-reveal"); observer.observe(item);
    }
  }
  new MutationObserver(records => {
    for (const record of records) {
      for (const removed of record.removedNodes) if (removed instanceof Element) {
        observer.unobserve(removed); removed.querySelectorAll(".scroll-reveal").forEach(node => observer.unobserve(node));
      }
      record.addedNodes.forEach(scan);
    }
  }).observe(root, { childList: true, subtree: true });
  const revealAll = () => { if (disabled()) root.querySelectorAll(".scroll-reveal:not(.is-revealed)").forEach(show); };
  new MutationObserver(revealAll).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  reduced.addEventListener("change", revealAll);
  root.addEventListener("focusin", event => { const item = event.target.closest(".scroll-reveal"); if (item) show(item); });
  scan(root);
}
