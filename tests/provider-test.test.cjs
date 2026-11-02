const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

const source = readFileSync(resolve(__dirname, '../apps/transcriptions/static/transcriptions/provider-test.js'), 'utf8');
function setup(respond) {
  const element = values => ({
    textContent: '', dataset: {}, listeners: {}, disabled: false,
    addEventListener(event, callback) { this.listeners[event] = callback; },
    setAttribute(name, value) { this[name] = value; },
    ...values,
  });
  const model = { name: 'model', value: 'saved-model', type: 'text' };
  const key = { name: 'api_key', value: '', type: 'password' };
  const editor = element({ elements: [model, key] });
  const form = element({ action: '/admin/web/modelprovider/1/test-connection/' });
  const button = element({ textContent: 'Test connection' });
  const result = element({ textContent: 'Uses saved settings.' });
  const elements = {
    'provider-test-form': form, 'modelprovider_form': editor,
    'provider-test-button': button, 'provider-test-result': result,
  };
  const calls = [];
  const context = vm.createContext({
    document: { getElementById: id => elements[id] },
    FormData: class { constructor(target) { assert.equal(target, form); } },
    AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => { calls.push({ url, options }); return respond(); },
  });
  vm.runInContext(source, context);
  return { editor, form, button, result, model, key, calls,
    submit: () => form.listeners.submit({ preventDefault() {} }),
  };
}

test('shows bounded connection result, locks duplicate clicks and restores controls', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const ui = setup(() => response);
  const pending = ui.submit();
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.button.textContent, 'Testing…');
  await ui.submit();
  assert.equal(ui.calls.length, 1);
  finish({ status: 200, json: async () => ({ ok: true, message: 'Connected', elapsed_seconds: 0.25 }) });
  await pending;
  assert.equal(ui.result.textContent, 'Connected (0.25 seconds)');
  assert.equal(ui.result.dataset.result, 'success');
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.form['aria-busy'], 'false');
  assert.equal(ui.calls[0].options.headers.Accept, 'application/json');
});

test('unsaved changes cannot send requests, including a changed API key', async () => {
  const ui = setup(() => { throw new Error('Must not send'); });
  ui.key.value = 'new-unsaved-key';
  ui.editor.listeners.input();
  assert.equal(ui.button.disabled, true);
  assert.match(ui.result.textContent, /Save and continue/);
  await ui.submit();
  assert.equal(ui.calls.length, 0);
  ui.key.value = '';
  ui.editor.listeners.input();
  assert.equal(ui.button.disabled, false);
});

test('failed tests and busy responses stay visible and allow another attempt', async () => {
  const ui = setup(() => ({ status: 429, json: async () => ({ ok: false, message: 'Another test is running' }) }));
  await ui.submit();
  assert.equal(ui.result.dataset.result, 'error');
  assert.equal(ui.result.textContent, 'Another test is running');
  assert.equal(ui.button.disabled, false);
});

test('expired admin sessions show a login instruction and no HTML response', async () => {
  const ui = setup(() => ({ status: 403 }));
  await ui.submit();
  assert.match(ui.result.textContent, /sign in again/);
  assert.equal(ui.result.dataset.result, 'error');
  assert.equal(ui.button.disabled, false);
});
