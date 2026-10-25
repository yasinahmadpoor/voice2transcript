const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const root = resolve(__dirname, '..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const source = readFileSync(resolve(root, 'static/app.js'), 'utf8');
const defaults = {
  model: 'server-primary', fallback_model: 'server-fallback',
  chunk_minutes: 20, language_hint: 'fa', prompt: 'Server transcription instructions.',
};

async function workspace({ jobs = [], saved = null, onUpload = null } = {}) {
  const elements = new Map();
  const timers = new Map();
  let timerId = 0;
  let elementId = 0;
  let serverJobs = structuredClone(jobs);
  const retryKeys = new Map();
  const uploadHeaders = [];
  const storage = new Map(saved ? [['voice-selected-job:tester', saved]] : []);
  function get(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id, value: '', hidden: false, disabled: false, listeners: {},
        addEventListener(event, callback) { this.listeners[event] = callback; },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, removeAttribute() {}, focus() {},
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = String(value); },
        children: [],
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
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
    document: { getElementById: get, querySelectorAll: () => inputs, createElement: () => get(`created-${++elementId}`), documentElement: {dataset:{theme:'dark'}}, querySelector: () => get('theme-meta') },
    window: { addEventListener() {} },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    FormData: MultipartData, AbortController, crypto:webcrypto,
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
    URL: { createObjectURL: () => 'blob:preview', revokeObjectURL() {} },
    fetch: async (url, options) => {
      if (url === '/auth/me') return { ok: true, json: async () => ({ username: 'tester', max_upload_mb: 500 }) };
      if (url === '/transcription/defaults') return { ok: true, json: async () => defaults };
      if (url === '/auth/logout') return { ok: true };
      if (url === '/jobs' && !options) {
        return { ok: true, json: async () => structuredClone({ jobs: serverJobs, max_active: 2, active: serverJobs.filter(job => job.status === 'processing').length, queued: serverJobs.filter(job => job.status === 'queued').length }) };
      }
      if (url === '/jobs' && options.method === 'POST') {
        requests.push(options.body);
        uploadHeaders.push(options.headers);
        assert.ok(inputs.every(input => input.disabled), 'Settings must be locked during upload');
        assert.equal(get('reset-settings').disabled, true);
        const key = options.headers['Idempotency-Key'];
        if (retryKeys.has(key)) return {ok:true,status:202,json:async () => structuredClone(retryKeys.get(key))};
        const accept = () => {
          const job = { id: `job-${requests.length}`, filename: options.body.get('file').name, status: 'queued', created_at: Date.now() / 1000, started_at: null, queue_position: serverJobs.length + 1, retryable: false };
          serverJobs.unshift(job);
          retryKeys.set(key, job);
          return { ok: true, status: 202, json: async () => structuredClone(job) };
        };
        return (onUpload && await onUpload(options, requests.length, accept)) || accept();
      }
      const [, , id, action] = url.split('/');
      const job = serverJobs.find(item => item.id === id);
      if (!job) return { ok: false, status: 404, json: async () => ({detail:'Job not found.'}) };
      if (action === 'result') return { ok: true, text: async () => job.transcript || 'SPEAKER_1: Test transcript' };
      if (action === 'retry') {
        Object.assign(job, {status:'queued', retryable:false, interrupted:false, queue_position:1});
        return {ok:true,json:async () => structuredClone(job)};
      }
      if (options?.method === 'DELETE') {
        serverJobs = serverJobs.filter(item => item.id !== id);
        return {ok:true};
      }
      return {ok:true,json:async () => structuredClone(job)};
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return { get, inputs, requests, timers, storage, uploadHeaders, jobs:() => serverJobs, setJobs: jobs => {serverJobs = structuredClone(jobs);} };
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
  assert.equal(get('processing').hidden, false);
  assert.equal(get('processing-title').textContent, 'Waiting for a transcription slot');
  assert.equal(get('transcript-text').value, '');
  get('file-input').listeners.change({target:{files:[{name:'second.wav',size:100}]}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 2, 'Another recording can be uploaded while the first job is queued');
});

test('reopens a saved completed job without reuploading and keeps edits during polling', async () => {
  const {get, requests, timers} = await workspace({saved:'saved-job',jobs:[{id:'saved-job',filename:'saved.wav',status:'completed',created_at:1,queue_position:0,transcript:'SPEAKER_1: Recovered result'}]});
  assert.equal(requests.length, 0);
  assert.equal(get('result').hidden, false);
  assert.equal(get('transcript-text').value, 'SPEAKER_1: Recovered result');
  get('transcript-text').value = 'My edited transcript';
  await get('refresh-jobs').listeners.click();
  assert.equal(get('transcript-text').value, 'My edited transcript');
  assert.equal(timers.size, 1);
  await get('logout').listeners.click();
  assert.equal(timers.size, 0);
  assert.equal(get('transcript-text').value, '');
});

test('tracks processing and completed states after a queued upload', async () => {
  const {get,setJobs} = await workspace();
  get('file-input').listeners.change({target:{files:[{name:'voice.wav',size:100}]}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  setJobs([{id:'job-1',filename:'voice.wav',status:'processing',created_at:1,started_at:Date.now()/1000,queue_position:0}]);
  await get('refresh-jobs').listeners.click();
  assert.equal(get('processing-title').textContent, 'Finding the words…');
  assert.equal(get('delete-job').disabled, true);
  setJobs([{id:'job-1',filename:'voice.wav',status:'completed',created_at:1,queue_position:0}]);
  await get('refresh-jobs').listeners.click();
  assert.equal(get('processing').hidden, true);
  assert.equal(get('transcript-text').value, 'SPEAKER_1: Test transcript');
});

test('shows interrupted jobs with retry and can remove a queued job', async () => {
  const {get} = await workspace({jobs:[{id:'interrupted',filename:'voice.wav',status:'failed',interrupted:true,retryable:true,error:'Server restarted.',created_at:1,queue_position:0}]});
  assert.equal(get('job-failure').hidden, false);
  assert.equal(get('retry-job').hidden, false);
  await get('retry-job').listeners.click();
  assert.equal(get('job-failure').hidden, true);
  assert.equal(get('processing-title').textContent, 'Waiting for a transcription slot');
  await get('delete-job').listeners.click();
  assert.equal(get('jobs-empty').hidden, false);
  assert.equal(get('empty-result').hidden, false);
});

function batchRows(get) {
  return get('file-list').children.map(row => ({
    name:row.children[0].children[0].textContent,
    status:row.children[0].children[1].textContent,
    buttons:row.children.filter(child => child.type === 'button'),
  }));
}

test('uploads every selected file sequentially with a shared settings snapshot', async () => {
  let active = 0;
  let peak = 0;
  let controls;
  const app = await workspace({onUpload:async () => {
    peak = Math.max(peak, ++active);
    await new Promise(resolve => setImmediate(resolve));
    if (controls) controls[0].value = 'changed-during-upload';
    --active;
  }});
  controls = app.inputs;
  const original = controls.map(input => [input.name,String(input.value)]);
  const files = ['one.wav','two.mp3','three.m4a'].map(name => ({name,size:100}));
  app.get('file-input').listeners.change({target:{files}});
  assert.equal(batchRows(app.get).length, 3);
  await app.get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(peak, 1);
  assert.deepEqual(app.requests.map(form => form.get('file').name), files.map(file => file.name));
  for (const form of app.requests) for (const [name,value] of original) assert.equal(form.get(name), value);
  assert.equal(new Set(app.uploadHeaders.map(header => header['Idempotency-Key'])).size, 3);
  assert.ok(batchRows(app.get).every(row => row.status.endsWith('Submitted')));
  assert.equal(app.get('batch-progress').textContent, '3 submitted.');
  assert.equal(app.get('transcribe-button').disabled, true);
});

test('drag and drop handles all files, validates individually, and supports removing one', async () => {
  const {get,requests} = await workspace();
  const files = [{name:'good.wav',size:100},{name:'empty.wav',size:0},{name:'too-big.mp3',size:500*1024*1024+1}];
  get('dropzone').listeners.drop({preventDefault(){},dataTransfer:{files}});
  assert.equal(batchRows(get).length, 3);
  assert.equal(batchRows(get).filter(row => row.status.endsWith('Invalid file')).length, 2);
  // Selecting the same unsubmitted recording again does not duplicate it.
  get('file-input').listeners.change({target:{files:[files[0]]}});
  assert.equal(batchRows(get).length, 3);
  batchRows(get).find(row => row.name === 'empty.wav').buttons[0].listeners.click();
  assert.equal(batchRows(get).length, 2);
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].get('file').name, 'good.wav');
  assert.match(get('batch-progress').textContent, /1 submitted.*1 invalid file/);
});

test('partial failures retry only failed files without resubmitting accepted recordings', async () => {
  const {get,requests,uploadHeaders,jobs} = await workspace({onUpload:async (_options,index) => index === 2 ? {ok:false,status:500,json:async () => ({detail:'Temporary upload error.'})} : null});
  get('file-input').listeners.change({target:{files:['one.wav','two.wav','three.wav'].map(name => ({name,size:100}))}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 3);
  assert.equal(jobs().length, 2);
  assert.match(get('transcribe-button').textContent, /Retry 1 recording/);
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 4);
  assert.equal(requests[3].get('file').name, 'two.wav');
  assert.equal(uploadHeaders[1]['Idempotency-Key'], uploadHeaders[3]['Idempotency-Key']);
  assert.equal(jobs().length, 3);
  get('clear-files').listeners.click();
  assert.equal(get('batch-review').hidden, true);
  assert.equal(jobs().length, 3, 'Clearing the upload list must not cancel server jobs');
});

test('a lost accepted response can be retried with the same key without duplicating the job', async () => {
  const {get,requests,uploadHeaders,jobs} = await workspace({onUpload:async (_options,index,accept) => {
    if (index === 1) {
      accept();
      throw new TypeError('Connection lost after acceptance');
    }
  }});
  get('file-input').listeners.change({target:{files:[{name:'one.wav',size:100},{name:'two.wav',size:100}]}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 1);
  assert.equal(jobs().length, 1);
  assert.ok(batchRows(get).every(row => row.status.endsWith('Upload failed')));
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 3);
  assert.equal(jobs().length, 2);
  assert.equal(uploadHeaders[0]['Idempotency-Key'], uploadHeaders[1]['Idempotency-Key']);
});

test('a full queue stops remaining uploads and allows retrying them later', async () => {
  const {get,requests,jobs} = await workspace({onUpload:async (_options,index) => index === 2 ? {ok:false,status:429,json:async () => ({detail:'Queue full.'})} : null});
  get('file-input').listeners.change({target:{files:['one.wav','two.wav','three.wav','four.wav'].map(name => ({name,size:100}))}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 2);
  assert.equal(jobs().length, 1);
  assert.equal(batchRows(get).filter(row => row.status.endsWith('Upload failed')).length, 3);
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.deepEqual(requests.slice(2).map(form => form.get('file').name), ['two.wav','three.wav','four.wav']);
  assert.equal(jobs().length, 4);
});

test('distinct files with identical names and sizes are both submitted', async () => {
  const {get,requests,uploadHeaders} = await workspace();
  const files = [{name:'recording.wav',size:100,lastModified:1},{name:'recording.wav',size:100,lastModified:1}];
  get('file-input').listeners.change({target:{files}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 2);
  assert.equal(requests[0].get('file'), files[0]);
  assert.equal(requests[1].get('file'), files[1]);
  assert.notEqual(uploadHeaders[0]['Idempotency-Key'], uploadHeaders[1]['Idempotency-Key']);
});
