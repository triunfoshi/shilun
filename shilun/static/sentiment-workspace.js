/* P2 shared date, independent views; switching views never changes the session. */
(() => {
  'use strict';
  const keys = ['today', 'rotation', 'history'];
  let current = 'today';
  const date = () => document.getElementById('sentimentDate')?.value;
  function select(key, focus = false) {
    if (!keys.includes(key)) return;
    current = key;
    for (const name of keys) {
      const button = document.getElementById(`sentiment-view-${name}`);
      const panel = document.getElementById(`sentiment-pane-${name}`);
      if (!button || !panel) continue;
      button.setAttribute('aria-selected', String(name === key));
      button.tabIndex = name === key ? 0 : -1;
      panel.hidden = name !== key;
      if (focus && name === key) button.focus({preventScroll: true});
    }
    if (key === 'history') mountHistoryTrend(false);
  }
  function mountHistoryTrend(force) {
    const section = document.getElementById('sentimentHistorySupplement');
    const root = document.getElementById('sentimentBoardHistory');
    if (!section?.open || !root || !date()) return;
    if (!force && root.dataset.loadedDate === date()) return;
    void window.BoardDynamics?.mountTrend(root, date(), force, {facts: false});
  }
  function mount(day, force = false) {
    if (!day) return;
    void window.ShortlineViews?.mount({treeTarget: 'sentimentThemeTree', rotationTarget: 'sentimentThemeRotation',
      evidenceTarget: 'sentimentThemeEvidence', historyTarget: 'sentimentEmotionHistory', date: day, force});
    void window.BoardDynamics?.mountFacts(document.getElementById('sentimentBoardDynamics'), day, force);
    mountHistoryTrend(force);
  }
  for (const key of keys) {
    const button = document.getElementById(`sentiment-view-${key}`);
    if (!button) continue;
    button.addEventListener('click', () => select(key));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const i = keys.indexOf(current);
      select(event.key === 'Home' ? keys[0] : event.key === 'End' ? keys.at(-1) : keys[(i + (event.key === 'ArrowRight' ? 1 : 2)) % keys.length], true);
    });
  }
  document.getElementById('sentimentHistorySupplement')?.addEventListener('toggle', () => mountHistoryTrend(false));
  const header = document.querySelector('.topbar');
  const updateTop = () => {
    const fixed = header && ['fixed', 'sticky'].includes(getComputedStyle(header).position);
    document.getElementById('tab-sentiment')?.style.setProperty('--sentiment-nav-top', `${fixed ? header.getBoundingClientRect().height : 0}px`);
  };
  window.addEventListener('resize', updateTop, {passive: true});
  if (typeof ResizeObserver === 'function' && header) new ResizeObserver(updateTop).observe(header);
  updateTop();
  function showEvidence() {
    select('rotation');
    document.getElementById('sentimentThemeEvidence')?.scrollIntoView({block:'start', behavior:'instant'});
  }
  window.SentimentWorkspace = {mount, select, showEvidence, current: () => current};
})();
