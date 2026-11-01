// Run before the stylesheet so a saved light preference is applied before paint.
(() => {
  let theme = 'dark';
  try {
    if (localStorage.getItem('voice-theme') === 'light') theme = 'light';
  } catch {
    // Storage can be unavailable; dark remains the default.
  }
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').setAttribute('content', theme === 'dark' ? '#131722' : '#f7f8fa');
})();
