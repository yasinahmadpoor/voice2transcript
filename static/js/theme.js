// Apply the saved theme before the stylesheet to avoid a flash during loading.
(() => {
  let theme = 'dark';
  try {
    if (localStorage.getItem('voice-theme') === 'light') theme = 'light';
  } catch {
    // Storage can be unavailable; dark remains the default.
  }

  function applyTheme() {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#131722' : '#f7f8fa');
  }

  function updateToggle() {
    const dark = theme === 'dark';
    const icon = document.getElementById('theme-icon');
    const label = document.getElementById('theme-label');
    if (icon) icon.textContent = dark ? '☀' : '☾';
    if (label) label.textContent = dark ? 'Light mode' : 'Dark mode';
    document.getElementById('theme-toggle')?.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  }

  function initializeToggle() {
    updateToggle();
    document.getElementById('theme-toggle')?.addEventListener('click', () => {
      theme = theme === 'dark' ? 'light' : 'dark';
      applyTheme();
      try {
        localStorage.setItem('voice-theme', theme);
      } catch {
        // The toggle still works when storage is unavailable.
      }
      updateToggle();
    });
  }

  applyTheme();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeToggle, { once: true });
  } else {
    initializeToggle();
  }
})();
