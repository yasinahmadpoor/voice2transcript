(() => {
  'use strict';
  const form = document.getElementById('provider-test-form');
  const editor = document.getElementById('modelprovider_form');
  if (!form || !editor) return;
  const button = document.getElementById('provider-test-button');
  const result = document.getElementById('provider-test-result');
  const savedMessage = result.textContent;
  const snapshot = () => JSON.stringify([...editor.elements]
    .filter(field => field.name && !['submit', 'button', 'hidden'].includes(field.type))
    .map(field => [field.name, field.value, field.checked]));
  const saved = snapshot();
  let running = false;
  const dirty = () => snapshot() !== saved;
  const update = () => {
    button.disabled = running || dirty();
    if (!running && dirty()) {
      result.textContent = 'Save and continue editing before testing the changed settings.';
      result.dataset.result = '';
    } else if (!running && result.textContent.startsWith('Save and continue')) {
      result.textContent = savedMessage;
    }
  };
  editor.addEventListener('input', update);
  editor.addEventListener('change', update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || dirty()) { update(); return; }
    running = true;
    button.disabled = true;
    button.textContent = 'Testing…';
    result.textContent = 'Testing the saved primary model…';
    result.dataset.result = '';
    form.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (response.status === 403 || response.redirected) {
        throw new Error('Your admin session expired or access was denied. Reload and sign in again.');
      }
      const data = await response.json();
      if (typeof data.ok !== 'boolean' || typeof data.message !== 'string') {
        throw new Error('The server returned an unexpected result. Reload and try again.');
      }
      const elapsed = Number.isFinite(data.elapsed_seconds) ? ` (${data.elapsed_seconds.toFixed(2)} seconds)` : '';
      result.textContent = data.message + elapsed;
      result.dataset.result = data.ok ? 'success' : 'error';
    } catch (error) {
      result.textContent = error.name === 'AbortError'
        ? 'The test timed out. Check the provider or try again.'
        : (error instanceof TypeError || error instanceof SyntaxError)
          ? 'Could not complete the test. Check your connection, reload, and try again.'
          : error.message;
      result.dataset.result = 'error';
    } finally {
      clearTimeout(timer);
      running = false;
      button.textContent = 'Test connection';
      form.setAttribute('aria-busy', 'false');
      update();
    }
  });
  update();
})();
