const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

const root = resolve(__dirname, '..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const source = readFileSync(resolve(root, 'static/app.js'), 'utf8');
const defaults = {
  model: 'server-primary', fallback_model: 'server-fallback',
  chunk_minutes: 20, language_hint: 'fa', prompt: 'Server transcription instructions.',
};

async function workspace() {
  const elements = new Map();
  function get(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id, value: '', hidden: false, disabled: false, listeners: {},
        addEventListener(event, callback) { this.listeners[event] = callback; },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, removeAttribute() {}, setAttribute() {}, focus() {},
      });
    }
    return elements.get(id);
  }
  // Read the real form field names so an omitted or misnamed control fails.
  const inputs = [...html.matchAll(/<(?:input|textarea)\b[^>]*data-transcription-setting[^>]*>/g)].map(([tag]) => {
    const input = get(tag.match(/\bid="([^"]+)"/)[1]);
    input.name = tag.match(/\bname="([^"]+)"/)[1];
    return input;
  });
  class MultipartData extends Map {
    constructor(form) {
      super();
      if (form) {
        assert.equal(form.id, 'transcribe-form');
        for (const input of inputs) {
          if (!input.disabled) this.set(input.name, String(input.value));
        }
        this.set('file', { name: 'previous-input.wav' });
      }
    }
    append(name, value) { this.set(name, value); }
  }
  const requests = [];
  const context = vm.createContext({
    document: { getElementById: get, querySelectorAll: () => inputs },
    window: { addEventListener() {} },
    FormData: MultipartData, AbortController, setInterval, clearInterval, setTimeout, clearTimeout,
    URL: { createObjectURL: () => 'blob:preview', revokeObjectURL() {} },
    fetch: async (url, options) => {
      if (url === '/auth/me') return { ok: true, json: async () => ({ username: 'tester', max_upload_mb: 500 }) };
      if (url === '/transcription/defaults') return { ok: true, json: async () => defaults };
      assert.equal(url, '/transcribe');
      requests.push(options.body);
      assert.ok(inputs.every(input => input.disabled), 'Settings must be locked during transcription');
      assert.equal(get('reset-settings').disabled, true);
      return { ok: true, status: 200, text: async () => 'SPEAKER_1: Test transcript' };
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return { get, inputs, requests };
}

test('loads the API defaults into every transcription setting and resets edits', async () => {
  const { get, inputs } = await workspace();
  assert.deepEqual(inputs.map(input => input.name).sort(), Object.keys(defaults).sort());
  for (const input of inputs) assert.equal(String(input.value), String(defaults[input.name]));
  get('model').value = 'edited';
  get('prompt').value = 'edited';
  get('reset-settings').listeners.click();
  assert.equal(get('model').value, defaults.model);
  assert.equal(get('prompt').value, defaults.prompt);
});

test('submits all edited settings with the selected file, preserving zero and empty values', async () => {
  const { get, inputs, requests } = await workspace();
  const file = { name: 'selected-recording.wav', size: 32_000 };
  get('file-input').listeners.change({ target: { files: [file] } });
  const values = {
    model: 'custom-primary', fallback_model: '', chunk_minutes: '0',
    language_hint: '', prompt: 'Custom instructions\nwith a second line.',
  };
  for (const input of inputs) input.value = values[input.name];
  await get('transcribe-form').listeners.submit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  const payload = requests[0];
  assert.equal(payload.get('file'), file, 'Selected/dragged file must replace a stale native file input');
  for (const [name, value] of Object.entries(values)) assert.equal(payload.get(name), value);
  assert.equal(payload.size, 6);
  assert.ok(inputs.every(input => !input.disabled));
  assert.equal(get('reset-settings').disabled, false);
  assert.equal(get('transcript-text').value, 'SPEAKER_1: Test transcript');
});
