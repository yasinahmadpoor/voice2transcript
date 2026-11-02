const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const root = resolve(__dirname, '..');
const html = readFileSync(resolve(root, 'apps/transcriptions/templates/transcriptions/index.html'), 'utf8');
const source = readFileSync(resolve(root, 'apps/transcriptions/static/transcriptions/app.js'), 'utf8');
const languageSource = readFileSync(resolve(root, 'apps/transcriptions/static/transcriptions/language-select.js'), 'utf8');
const meetingSource = readFileSync(resolve(root, 'apps/transcriptions/static/transcriptions/meeting-context.js'), 'utf8');
const themeSource = readFileSync(resolve(root, 'static/js/theme.js'), 'utf8');
const defaults = {
  provider: '3',
  model: 'server-primary', fallback_model: 'server-fallback',
  meeting_description: '', participants: [],
  chunk_minutes: 20, language_hint: 'fa', prompt: 'Server transcription instructions.',
};

async function workspace({ jobs = [], saved = null, onUpload = null, onCancel = null, onDelete = null, onPreview = null, onContext = null, theme = null } = {}) {
  const elements = new Map();
  const timers = new Map();
  let timerId = 0;
  let elementId = 0;
  let serverJobs = structuredClone(jobs);
  let currentAccount = {username:'tester',user_id:42};
  const retryKeys = new Map();
  const uploadHeaders = [];
  const deletions = [];
  const retries = [];
  const storage = new Map(saved ? [['voice-selected-job:42', saved]] : []);
  if (theme) storage.set('voice-theme', theme);
  function get(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id, value: '', hidden: false, disabled: false, listeners: {},
        addEventListener(event, callback) { this.listeners[event] = callback; },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, focus() { this.focused = true; }, select() {}, scrollIntoView() {},
        showModal() { this.open = true; },
        close() { this.open = false; this.listeners.close?.(); },
        removeAttribute(name) { delete this.attributes[name]; },
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
  const inputs = [...html.matchAll(/<(?:input|textarea|select)\b[^>]*data-transcription-setting[^>]*>/g)].map(([tag]) => {
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
  const previews = [];
  const contextRequests = [];
  const context = vm.createContext({
    document: { readyState: 'complete', getElementById: get, querySelectorAll: () => inputs, createElement: () => get(`created-${++elementId}`), documentElement: {dataset:{theme:'dark'}}, querySelector: () => get('theme-meta') },
    window: { addEventListener() {} },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    FormData: MultipartData, AbortController, crypto:webcrypto,
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
    URL: { createObjectURL: () => 'blob:preview', revokeObjectURL() {} },
    fetch: async (url, options) => {
      if (url === '/auth/me') return { ok: true, json: async () => ({ ...currentAccount, max_upload_mb: 500 }) };
      if (url === '/auth/login') return {ok:true};
      if (url === '/transcription/defaults') return { ok: true, json: async () => defaults };
      if (url === '/transcription/providers') return {ok:true,json:async () => ({providers:[{id:'3',name:'Gemini',model:defaults.model,fallback_model:defaults.fallback_model},{id:'7',name:'Custom audio',model:'custom-audio',fallback_model:''}]})};
      if (url === '/transcription/prompt-preview') { const body = JSON.parse(options.body); previews.push(body); return onPreview ? onPreview(body, options) : {ok:true,json:async () => ({effective_prompt:'Server-generated final prompt'})}; }
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
      if (action === 'context') { contextRequests.push(id); return onContext ? onContext(job,options) : {ok:true,json:async () => job.context || {meeting_description:'',participants:[],prompt:'Original',effective_prompt:'Original'}}; }
      if (action === 'result') return { ok: true, text: async () => job.transcript || 'SPEAKER_1: Test transcript' };
      if (action === 'retry') {
        retries.push({id,options});
        if (options?.body && JSON.parse(options.body).restart) job.progress = {completed_chunks:0,total_chunks:0};
        Object.assign(job, {status:'queued', retryable:false, resumable:false, restart_required:false, interrupted:false, queue_position:1});
        return {ok:true,json:async () => structuredClone(job)};
      }
      if (action === 'cancel') {
        if (onCancel) return onCancel(job, options);
        Object.assign(job, {status:job.status === 'queued' ? 'cancelled' : 'cancelling', retryable:job.status === 'queued'});
        return {ok:true,json:async () => structuredClone(job)};
      }
      if (options?.method === 'DELETE') {
        deletions.push(id);
        if (onDelete) {
          const response = await onDelete(job, options);
          if (response) return response;
        }
        serverJobs = serverJobs.filter(item => item.id !== id);
        return {ok:true};
      }
      return {ok:true,json:async () => structuredClone(job)};
    },
  });
  vm.runInContext(themeSource, context);
  vm.runInContext(languageSource, context);
  vm.runInContext(meetingSource, context);
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return { get, inputs, requests, previews, contextRequests, participants: () => get('participant-list').children.map(card => ({ name: card.children[2], description: card.children[4], remove: card.children[0].children[1] })), timers, storage, uploadHeaders, deletions, retries, jobs:() => serverJobs, setJobs: jobs => {serverJobs = structuredClone(jobs);}, setAccount: account => {currentAccount = account;} };
}

test('shared theme defaults to dark, toggles, and restores a saved preference', async () => {
  const { get, storage } = await workspace();
  assert.equal(get('theme-label').textContent, 'Light mode');
  get('theme-toggle').listeners.click();
  assert.equal(storage.get('voice-theme'), 'light');
  assert.equal(get('theme-meta').attributes.content, '#f7f8fa');
  assert.equal(get('theme-toggle').attributes['aria-label'], 'Switch to dark mode');
  const restored = await workspace({ theme: storage.get('voice-theme') });
  assert.equal(restored.get('theme-label').textContent, 'Dark mode');
  restored.get('theme-toggle').listeners.click();
  assert.equal(restored.storage.get('voice-theme'), 'dark');
});

test('shared theme initializes before paint and tolerates pages without a toggle or storage', () => {
  const document = {
    readyState: 'loading', documentElement: { dataset: {} },
    querySelector: () => null, getElementById: () => null,
    addEventListener(event, callback) { this.ready = callback; },
  };
  vm.runInNewContext(themeSource, {
    document, localStorage: { getItem() { throw Error('Storage unavailable'); } },
  });
  assert.equal(document.documentElement.dataset.theme, 'dark');
  assert.doesNotThrow(() => document.ready());
});

test('loads the API defaults into every transcription setting and resets edits', async () => {
  const { get, inputs } = await workspace();
  assert.deepEqual(inputs.map(input => input.name).sort(), Object.keys(defaults).sort());
  for (const input of inputs) assert.equal(String(input.value), input.name === 'participants' ? JSON.stringify(defaults.participants) : String(defaults[input.name]));
  get('model').value = 'edited';
  get('prompt').value = 'edited';
  get('reset-settings').listeners.click();
  assert.equal(get('model').value, defaults.model);
  assert.equal(get('prompt').value, defaults.prompt);
});

test('participant fields add, remove, validate descriptions, and reset with meeting details', async () => {
  const ui = await workspace();
  assert.equal(ui.participants().length, 1);
  const row = ui.participants()[0];
  row.description.value = 'Project lead'; row.description.listeners.input();
  await ui.get('preview-prompt').listeners.click();
  assert.match(ui.get('prompt-preview-error').textContent, /Enter a name/);
  assert.equal(ui.previews.length, 0);
  row.name.value = 'Sara'; row.name.listeners.input();
  ui.get('meeting-description').value = 'Quarterly planning';
  ui.get('add-participant').listeners.click();
  assert.equal(ui.participants().length, 2);
  assert.deepEqual(JSON.parse(ui.get('participants-data').value), [{name:'Sara', description:'Project lead'}]);
  ui.participants()[1].remove.listeners.click();
  assert.equal(ui.participants().length, 1);
  ui.get('reset-settings').listeners.click();
  assert.equal(ui.get('meeting-description').value, '');
  assert.equal(ui.get('participants-data').value, '[]');
  assert.equal(ui.participants()[0].name.value, '');
  for (let index = 1; index < 20; index++) ui.get('add-participant').listeners.click();
  assert.equal(ui.get('add-participant').disabled, true);
  ui.get('add-participant').listeners.click();
  assert.equal(ui.participants().length, 20);
});

test('final prompt preview sends original fields to the server and uses its exact response', async () => {
  const ui = await workspace({onPreview: async body => ({ok:true,json:async () => ({effective_prompt:'Exact server prompt\n<script>literal text</script>'})})});
  ui.get('meeting-description').value = 'Planning';
  ui.get('prompt').value = 'Return JSON only';
  const row = ui.participants()[0]; row.name.value = 'Sara'; row.description.value = 'Lead'; row.name.listeners.input();
  await ui.get('preview-prompt').listeners.click();
  assert.deepEqual(ui.previews[0], {prompt:'Return JSON only',meeting_description:'Planning',participants:[{name:'Sara',description:'Lead'}]});
  assert.equal(ui.get('prompt-preview-text').textContent, 'Exact server prompt\n<script>literal text</script>');
  assert.equal(ui.get('prompt-preview').hidden, false);
  assert.equal(ui.get('prompt-preview-text').innerHTML, undefined);
  assert.equal(ui.get('prompt').value, 'Return JSON only');
  ui.get('meeting-description').listeners.input();
  assert.equal(ui.get('prompt-preview').hidden, true);
  assert.equal(ui.get('prompt-preview-text').textContent, '');
});

test('editing fields discards a late prompt preview and clears private preview on logout', async () => {
  let finish;
  const ui = await workspace({onPreview: () => new Promise(resolve => {finish = resolve;})});
  const pending = ui.get('preview-prompt').listeners.click();
  ui.get('meeting-description').value = 'Changed meeting'; ui.get('meeting-description').listeners.input();
  finish({ok:true,json:async () => ({effective_prompt:'Stale private context'})});
  await pending;
  assert.equal(ui.get('prompt-preview-text').textContent, '');
  assert.equal(ui.get('prompt-preview').hidden, true);
  await ui.get('logout').listeners.click();
  assert.equal(ui.get('meeting-description').value, '');
  assert.equal(ui.get('prompt').value, '');
  assert.equal(ui.get('participants-data').value, '[]');
});

test('meeting context is one immutable snapshot shared by every upload in a batch', async () => {
  let ui;
  ui = await workspace({onUpload: async (options, count) => {
    assert.ok(ui.participants().every(row => row.name.disabled && row.description.disabled && row.remove.disabled));
    assert.equal(ui.get('add-participant').disabled, true);
    if (count === 1) ui.get('meeting-description').value = 'Changed during upload';
  }});
  ui.get('meeting-description').value = 'Original meeting';
  const row = ui.participants()[0]; row.name.value = 'Sara'; row.name.listeners.input();
  ui.get('file-input').listeners.change({target:{files:[{name:'one.wav',size:100},{name:'two.wav',size:100}]}});
  await ui.get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(ui.requests.length, 2);
  for (const request of ui.requests) {
    assert.equal(request.get('meeting_description'), 'Original meeting');
    assert.deepEqual(JSON.parse(request.get('participants')), [{name:'Sara',description:''}]);
  }
});

test('saved context is read only and renders participant markup as literal text', async () => {
  const context = {meeting_description:'Planning',participants:[{name:'<script>literal</script>',description:'Lead'}],prompt:'Saved base',effective_prompt:'Saved final'};
  const ui = await workspace({saved:'saved-job',jobs:[{id:'saved-job',filename:'meeting.wav',status:'completed',context}]});
  await ui.get('view-job-context').listeners.click();
  assert.equal(ui.get('job-context-dialog').open, true);
  assert.equal(ui.get('context-meeting').textContent, 'Planning');
  assert.equal(ui.get('context-participants').children[0].textContent, '<script>literal</script> — Lead');
  assert.equal(ui.get('context-original-prompt').textContent, 'Saved base');
  assert.equal(ui.get('context-final-prompt').textContent, 'Saved final');
  assert.equal(ui.get('meeting-description').value, '');
  assert.equal(ui.get('prompt').value, defaults.prompt);
  ui.get('close-job-context').listeners.click();
  assert.equal(ui.get('job-context-dialog').open, false);
  assert.equal(ui.get('context-final-prompt').textContent, '');
});

test('logging out aborts saved-context loading and prevents stale private details from appearing', async () => {
  let finish;
  const ui = await workspace({saved:'saved-job',jobs:[{id:'saved-job',filename:'private.wav',status:'completed'}],onContext: () => new Promise(resolve => {finish = resolve;})});
  const pending = ui.get('view-job-context').listeners.click();
  await ui.get('logout').listeners.click();
  finish({ok:true,json:async () => ({meeting_description:'Private meeting',participants:[],prompt:'Private',effective_prompt:'Private final'})});
  await pending;
  assert.equal(ui.get('job-context-dialog').open, false);
  assert.equal(ui.get('context-job-name').textContent, '');
  assert.equal(ui.get('context-final-prompt').textContent, '');
  assert.equal(ui.get('context-meeting').textContent, '');
});

test('submits all edited settings with the selected file, preserving zero and empty values', async () => {
  const { get, inputs, requests } = await workspace();
  const file = { name: 'selected-recording.wav', size: 32_000 };
  get('file-input').listeners.change({ target: { files: [file] } });
  const values = {
    provider: '7',
    model: 'custom-primary', fallback_model: '', chunk_minutes: '0',
    meeting_description: '', participants: '[]',
    language_hint: '', prompt: 'Custom instructions\nwith a second line.',
  };
  for (const input of inputs) input.value = values[input.name];
  await get('transcribe-form').listeners.submit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  const payload = requests[0];
  assert.equal(payload.get('file'), file, 'Selected/dragged file must replace a stale native file input');
  for (const [name, value] of Object.entries(values)) assert.equal(payload.get(name), value);
  assert.equal(payload.size, Object.keys(values).length + 1);
  assert.ok(inputs.every(input => !input.disabled));
  assert.equal(get('reset-settings').disabled, false);
  assert.equal(get('processing').hidden, false);
  assert.equal(get('processing-title').textContent, 'Waiting for a transcription slot');
  assert.equal(get('transcript-text').value, '');
  get('file-input').listeners.change({target:{files:[{name:'second.wav',size:100}]}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests.length, 2, 'Another recording can be uploaded while the first job is queued');
});

test('selecting a configured provider updates model defaults and submits its identity', async () => {
  const { get, requests } = await workspace();
  get('provider').value = '7';
  get('provider').listeners.change();
  assert.equal(get('model').value, 'custom-audio');
  assert.equal(get('fallback-model').value, '');
  get('file-input').listeners.change({target:{files:[{name:'voice.wav',size:100}]}});
  await get('transcribe-form').listeners.submit({preventDefault(){}});
  assert.equal(requests[0].get('provider'), '7');
  get('reset-settings').listeners.click();
  assert.equal(get('provider').value, '3');
  assert.equal(get('model').value, defaults.model);
});

test('language selector searches a broad catalog by name, native name, and code', async () => {
  const { get } = await workspace();
  const search = get('language-search');
  const list = get('language-options');
  assert.match(search.value, /Persian/);
  assert.equal(get('language').value, 'fa');
  get('language-toggle').listeners.click();
  assert.equal(list.children.length, 185, '184 ISO languages plus auto-detect');
  assert.equal(search.attributes['aria-expanded'], 'true');
  const options = () => list.children.map(option => option.children[0].textContent);
  for (const query of ['Persian', 'فارسی', 'Farsi', 'fa']) {
    search.value = query;
    search.listeners.input();
    assert.ok(options().some(label => label.includes('Persian')), query);
    assert.equal(get('language').value, 'fa');
  }
  search.value = 'Spanish';
  search.listeners.input();
  assert.equal(get('language').value, 'es');
  assert.deepEqual(options(), ['Spanish / español']);
  search.value = 'Japanese';
  search.listeners.input();
  assert.deepEqual(options(), ['Japanese / 日本語']);
  let prevented = false;
  search.listeners.keydown({ key: 'ArrowDown', preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(search.attributes['aria-activedescendant'], /^language-option-/);
  search.listeners.keydown({ key: 'Enter', preventDefault() {} });
  assert.equal(get('language').value, 'ja');
  assert.equal(list.hidden, true);
  assert.equal(search.attributes['aria-activedescendant'], undefined);
  get('reset-settings').listeners.click();
  assert.equal(get('language').value, 'fa');
  assert.match(search.value, /Persian/);
});

test('language selector submits codes, auto-detect, and custom hints with the batch settings', async () => {
  const { get, requests } = await workspace({ onUpload: async () => {
    assert.equal(get('language-search').disabled, true);
    assert.equal(get('language-toggle').disabled, true);
  } });
  const search = get('language-search');
  for (const [query, expected] of [['German', 'de'], ['', ''], ['My regional dialect', 'My regional dialect']]) {
    search.value = query;
    search.listeners.input();
    if (query === 'My regional dialect') {
      const custom = get('language-options').children.at(-1);
      assert.equal(custom.children[1].textContent, 'Custom');
      custom.listeners.click();
    } else {
      get('language-options').children[0].listeners.click();
    }
    get('file-input').listeners.change({ target: { files: [{ name: `${expected || 'auto'}.wav`, size: 100 }] } });
    await get('transcribe-form').listeners.submit({ preventDefault() {} });
    assert.equal(requests.at(-1).get('language_hint'), expected);
    assert.equal(search.disabled, false);
    assert.equal(get('language-toggle').disabled, false);
  }
  get('language-toggle').listeners.click();
  search.listeners.keydown({ key: 'Escape', preventDefault() {} });
  assert.equal(get('language-options').hidden, true);
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
  await get('confirm-delete-job').listeners.click();
  assert.equal(get('jobs-empty').hidden, false);
  assert.equal(get('empty-result').hidden, false);
});

test('deleting requires explicit confirmation and cancel or Escape keeps the transcription', async () => {
  const {get,jobs,deletions} = await workspace({jobs:[{id:'saved',filename:'Interview.wav',status:'completed',created_at:1}]});
  get('delete-job').listeners.click();
  assert.equal(get('delete-dialog').open, true);
  assert.equal(get('delete-job-name').textContent, 'Interview.wav');
  assert.equal(get('cancel-delete-job').focused, true);
  assert.equal(deletions.length, 0);
  get('cancel-delete-job').listeners.click();
  assert.equal(get('delete-dialog').open, false);
  assert.equal(jobs().length, 1);
  await get('confirm-delete-job').listeners.click();
  assert.equal(deletions.length, 0);
  get('delete-job').listeners.click();
  let blocked = false;
  get('delete-dialog').listeners.cancel({preventDefault() {blocked = true;}});
  assert.equal(blocked, false);
  get('delete-dialog').close();
  assert.equal(deletions.length, 0);
});

test('failed jobs show chunk progress and resume by default with a separate restart action', async () => {
  const job = {id:'partial',filename:'Partial.wav',status:'failed',created_at:1,retryable:true,resumable:true,progress:{completed_chunks:2,total_chunks:5}};
  const {get,jobs,setJobs,retries} = await workspace({jobs:[job]});
  assert.equal(get('chunk-progress').hidden, false);
  assert.match(get('chunk-progress').textContent, /2 of 5/);
  assert.equal(get('retry-job').textContent, 'Resume transcription');
  assert.equal(get('restart-job').hidden, false);
  await get('retry-job').listeners.click();
  assert.equal(retries[0].options.body, undefined);
  assert.equal(jobs()[0].progress.completed_chunks, 2);
  setJobs([{...job,restart_required:true,resumable:false}]);
  await get('refresh-jobs').listeners.click();
  assert.equal(get('retry-job').hidden, true);
  assert.equal(get('restart-job').hidden, false);
  await get('restart-job').listeners.click();
  assert.deepEqual(JSON.parse(retries[1].options.body), {restart:true});
  assert.equal(jobs()[0].progress.completed_chunks, 0);
  setJobs([{...job,restart_required:true,resumable:false,progress:{completed_chunks:0,total_chunks:0}}]);
  await get('refresh-jobs').listeners.click();
  assert.equal(get('restart-job').hidden, false);
});

test('confirmation deletes the captured recording even if the selected job changes', async () => {
  const filename = '<script>test</script> جلسه.wav';
  const {get,jobs,deletions} = await workspace({jobs:[
    {id:'first',filename,status:'completed',created_at:1},
    {id:'second',filename:'Other.wav',status:'completed',created_at:2},
  ]});
  get('delete-job').listeners.click();
  assert.equal(get('delete-job-name').textContent, filename);
  get('jobs-list').children[1].listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  await get('confirm-delete-job').listeners.click();
  assert.deepEqual(deletions, ['first']);
  assert.deepEqual(jobs().map(job => job.id), ['second']);
  assert.equal(get('delete-dialog').open, false);
});

test('confirmation blocks duplicate requests and preserves the dialog on deletion errors', async () => {
  let respond;
  const {get,jobs,deletions} = await workspace({jobs:[{id:'saved',filename:'Saved.wav',status:'failed',created_at:1}],onDelete:() => new Promise(resolve => {respond = resolve;})});
  get('delete-job').listeners.click();
  const pending = get('confirm-delete-job').listeners.click();
  assert.equal(get('confirm-delete-job').disabled, true);
  assert.equal(get('cancel-delete-job').disabled, true);
  let blocked = false;
  get('delete-dialog').listeners.cancel({preventDefault() {blocked = true;}});
  assert.equal(blocked, true);
  get('cancel-delete-job').listeners.click();
  assert.equal(get('delete-dialog').open, true);
  await get('confirm-delete-job').listeners.click();
  assert.equal(deletions.length, 1);
  respond({ok:false,json:async () => ({detail:'Unable to delete recording.'})});
  await pending;
  assert.equal(jobs().length, 1);
  assert.equal(get('delete-dialog').open, true);
  assert.equal(get('delete-dialog-error').textContent, 'Unable to delete recording.');
  assert.equal(get('confirm-delete-job').disabled, false);
  get('cancel-delete-job').listeners.click();
  assert.equal(get('delete-dialog').open, false);
});

test('running jobs expose cancel, show cancelling, and preserve cancelled history for retry', async () => {
  const job = {id:'running',filename:'voice.wav',status:'processing',created_at:1,started_at:1,retryable:false};
  const {get,setJobs,jobs} = await workspace({saved:'running',jobs:[job]});
  assert.equal(get('cancel-job').hidden, false);
  assert.equal(get('cancel-selected-job').hidden, false);
  assert.equal(get('delete-job').disabled, true);
  await get('cancel-job').listeners.click();
  assert.equal(jobs()[0].status, 'cancelling');
  assert.equal(get('cancel-job').disabled, true);
  assert.equal(get('cancel-job').textContent, 'Cancelling…');
  assert.equal(get('processing-title').textContent, 'Cancelling transcription…');
  setJobs([{...job,status:'cancelled',retryable:true}]);
  await get('refresh-jobs').listeners.click();
  assert.equal(get('job-cancelled').hidden, false);
  assert.equal(get('cancel-job').hidden, true);
  assert.equal(get('result').hidden, true);
  assert.equal(get('retry-cancelled-job').hidden, false);
  assert.equal(get('delete-job').disabled, false);
  await get('retry-cancelled-job').listeners.click();
  assert.equal(jobs()[0].status, 'queued');
  assert.equal(get('job-cancelled').hidden, true);
  assert.equal(get('cancel-job').disabled, false);
});

test('queued jobs can be cancelled without disappearing and cancellation errors remain actionable', async () => {
  const job = {id:'waiting',filename:'voice.wav',status:'queued',created_at:1,queue_position:1,retryable:false};
  const {get,jobs} = await workspace({jobs:[job]});
  await get('cancel-selected-job').listeners.click();
  assert.equal(jobs().length, 1);
  assert.equal(jobs()[0].status, 'cancelled');
  assert.equal(get('job-cancelled').hidden, false);
  const failed = await workspace({jobs:[job],onCancel:async () => ({ok:false,json:async () => ({detail:'Cannot reach the server.'})})});
  await failed.get('cancel-job').listeners.click();
  assert.equal(failed.get('jobs-error').textContent, 'Cannot reach the server.');
  assert.equal(failed.get('cancel-job').disabled, false);
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

test('switching users clears private transcripts and selections, including reused usernames', async () => {
  const {get,setAccount,setJobs,storage} = await workspace({saved:'private-first',jobs:[{id:'private-first',filename:'private.wav',status:'completed',created_at:1,queue_position:0,transcript:'Private first account transcript'}]});
  get('transcript-text').value = 'Private edited transcript';
  get('file-input').listeners.change({target:{files:[{name:'private-unsubmitted.wav',size:100}]}});
  await get('logout').listeners.click();
  assert.equal(get('transcript-text').value, '');
  assert.equal(get('audio-preview').hidden, true);
  setAccount({username:'tester',user_id:43});
  setJobs([]);
  await get('login-form').listeners.submit({preventDefault(){}});
  assert.equal(get('transcript-text').value, '');
  assert.equal(get('result').hidden, true);
  assert.equal(get('jobs-empty').hidden, false);
  assert.equal(get('batch-review').hidden, true);
  assert.equal(storage.get('voice-selected-job:42'), 'private-first');
  assert.equal(storage.has('voice-selected-job:43'), false);
});
