/* Endury — the colour theme: Dark (the default), Light, or Auto (the
   device's own, followed live). A classic script in <head>, so a light page
   never paints dark first. The choice lives in this browser's localStorage
   beside the settings, not in them: Python has no use for it, and Forget my
   keys leaves it. Settings changes it through `enduryTheme.set`. */
(() => {
  const KEY = 'endury.theme';
  const deviceLight = window.matchMedia('(prefers-color-scheme: light)');
  let current = 'dark';
  try {
    current = localStorage.getItem(KEY) || 'dark';
  } catch { /* blocked storage: the default */ }

  function apply() {
    const light = current === 'light' || (current === 'system' && deviceLight.matches);
    const root = document.documentElement;
    root.dataset.theme = light ? 'light' : 'dark';
    // The browser's own bar, and an installed iPhone's status bar at launch.
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', getComputedStyle(root).getPropertyValue('--cb-bg').trim());
    document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')
      ?.setAttribute('content', light ? 'default' : 'black-translucent');
  }

  window.enduryTheme = {
    choice: () => current,
    /** Switch to `next` ('dark', 'light' or 'system') now, and keep it when
        this browser allows. */
    set(next) {
      current = next;
      try {
        localStorage.setItem(KEY, next);
      } catch { /* this visit only */ }
      apply();
    },
  };
  deviceLight.addEventListener('change', apply);
  apply();
})();
