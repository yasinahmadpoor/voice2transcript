const $ = (id) => document.getElementById(id);
let selectedFile = null;
let previewUrl = null;
let maxUploadMb = 500;
let busy = false;
let activeRequest = null;
let elapsedTimer = null;
let toastTimer = null;
let transcriptFilename = 'transcription.txt';
let transcriptionDefaults = null;
const settingInputs = document.querySelectorAll('[data-transcription-setting]');

function resetSettings() {
  if (!transcriptionDefaults) return;
  for (const input of settingInputs) input.value = transcriptionDefaults[input.name];
}

$('reset-settings').addEventListener('click', resetSettings);

function showError(id, message = '') {
  $(id).textContent = message;
  $(id).hidden = !message;
}

async function errorMessage(response) {
  try {
    const data = await response.json();
    return typeof data.detail === 'string' ? data.detail : 'Please check the form and try again.';
  } catch {
    return `The server returned an error (${response.status}). Please try again.`;
  }
}

function showLogin(message = '') {
  $('loading').hidden = true;
  $('login-view').hidden = false;
  $('workspace-view').hidden = true;
  $('account').hidden = true;
  $('header-note').hidden = false;
  $('password').value = '';
  showError('login-error', message);
}

async function openWorkspace() {
  const response = await fetch('/auth/me');
  if (!response.ok) {
    showLogin(response.status === 401 ? '' : await errorMessage(response));
    return;
  }
  const data = await response.json();
  const defaultsResponse = await fetch('/transcription/defaults');
  if (!defaultsResponse.ok) {
    showLogin(await errorMessage(defaultsResponse));
    return;
  }
  transcriptionDefaults = await defaultsResponse.json();
  resetSettings();
  maxUploadMb = data.max_upload_mb;
  $('upload-limit').textContent = maxUploadMb;
  $('username-label').textContent = data.username;
  $('loading').hidden = true;
  $('login-view').hidden = true;
  $('workspace-view').hidden = false;
  $('account').hidden = false;
  $('header-note').hidden = true;
  $('password').value = '';
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  showError('login-error');
  $('login-button').disabled = true;
  $('login-button').textContent = 'Signing in…';
  try {
    const response = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value.trim(), password: $('password').value }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    await openWorkspace();
  } catch (error) {
    showError('login-error', error instanceof TypeError ? 'Cannot reach the server. Check your connection and try again.' : error.message);
  } finally {
    $('login-button').disabled = false;
    $('login-button').textContent = 'Sign in →';
  }
});

function chooseFile(file) {
  if (busy) return;
  showError('upload-error');
  if (file && (file.size === 0 || file.size > maxUploadMb * 1024 * 1024)) {
    showError('upload-error', file.size === 0 ? 'This file is empty. Choose a recording with audio.' : `Choose a file smaller than ${maxUploadMb} MB.`);
    $('file-input').value = '';
    return;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  selectedFile = file || null;
  $('selected-file').hidden = !file;
  $('audio-preview').hidden = !file;
  $('audio-preview').pause();
  $('audio-preview').removeAttribute('src');
  if (file) {
    $('file-name').textContent = file.name;
    $('file-size').textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB`;
    previewUrl = URL.createObjectURL(file);
    $('audio-preview').src = previewUrl;
  } else {
    $('file-input').value = '';
  }
  $('audio-preview').load();
  $('transcribe-button').disabled = !selectedFile;
}

$('file-input').addEventListener('change', (event) => chooseFile(event.target.files[0]));
$('remove-file').addEventListener('click', () => chooseFile(null));
for (const name of ['dragenter', 'dragover']) {
  $('dropzone').addEventListener(name, (event) => {
    event.preventDefault();
    if (!busy) $('dropzone').classList.add('dragover');
  });
}
for (const name of ['dragleave', 'drop']) {
  $('dropzone').addEventListener(name, (event) => {
    event.preventDefault();
    $('dropzone').classList.remove('dragover');
    if (name === 'drop') chooseFile(event.dataTransfer.files[0]);
  });
}
// Do not navigate away if a recording misses the drop target.
window.addEventListener('dragover', (event) => event.preventDefault());
window.addEventListener('drop', (event) => event.preventDefault());

function setBusy(value) {
  busy = value;
  $('transcribe-button').disabled = value || !selectedFile;
  $('transcribe-button').textContent = value ? 'Transcribing…' : 'Transcribe recording →';
  $('file-input').disabled = value;
  $('remove-file').disabled = value;
  for (const input of settingInputs) input.disabled = value;
  $('reset-settings').disabled = value;
  $('dropzone').classList.toggle('disabled', value);
  $('processing').hidden = !value;
  $('transcript-panel').setAttribute('aria-busy', String(value));
  clearInterval(elapsedTimer);
  if (value) {
    const started = Date.now();
    $('elapsed').textContent = '0:00 elapsed';
    elapsedTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      $('elapsed').textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} elapsed`;
    }, 1000);
  }
}

function updateWordCount() {
  const text = $('transcript-text').value.trim();
  $('word-count').textContent = `${text ? text.split(/\s+/u).length : 0} words`;
}

$('transcribe-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedFile || busy) return;
  showError('upload-error');
  const file = selectedFile;
  const previousTranscript = $('transcript-text').value;
  const form = new FormData($('transcribe-form'));
  // Dragged files may differ from the native input's current FileList.
  form.set('file', file);
  $('empty-result').hidden = true;
  $('result').hidden = true;
  $('result-badge').hidden = true;
  $('transcript-status').textContent = 'Transcribing your recording';
  setBusy(true);
  const controller = new AbortController();
  activeRequest = controller;
  try {
    const response = await fetch('/transcribe', { method: 'POST', body: form, signal: controller.signal });
    if (response.status === 401) {
      resetWorkspace();
      showLogin('Your session has expired. Please sign in again.');
      return;
    }
    if (!response.ok) throw new Error(await errorMessage(response));
    const text = await response.text();
    if (!text.trim()) throw new Error('No transcript was returned. Please try a different recording.');
    $('transcript-text').value = text;
    $('result-file').textContent = file.name;
    transcriptFilename = `${file.name.replace(/\.[^.]+$/, '') || 'recording'}-transcript.txt`;
    updateWordCount();
    $('result').hidden = false;
    $('result-badge').hidden = false;
    $('transcript-status').textContent = 'Transcription complete';
  } catch (error) {
    if (error.name !== 'AbortError') {
      showError('upload-error', error instanceof TypeError ? 'Connection lost. Please check your connection and try again.' : error.message);
      $('result').hidden = !previousTranscript;
      $('result-badge').hidden = !previousTranscript;
      $('empty-result').hidden = Boolean(previousTranscript);
      $('transcript-status').textContent = 'Unable to transcribe. Please try again.';
    }
  } finally {
    if (activeRequest === controller) activeRequest = null;
    setBusy(false);
  }
});

function resetWorkspace() {
  activeRequest?.abort();
  activeRequest = null;
  setBusy(false);
  chooseFile(null);
  $('transcript-text').value = '';
  $('result-file').textContent = '';
  $('word-count').textContent = '';
  $('result').hidden = true;
  $('result-badge').hidden = true;
  $('processing').hidden = true;
  $('empty-result').hidden = false;
  $('transcript-status').textContent = 'Ready when you are';
}

$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  try {
    const response = await fetch('/auth/logout', { method: 'POST' });
    if (!response.ok) throw new Error(await errorMessage(response));
    resetWorkspace();
    showLogin();
    $('username').focus();
  } catch {
    toast('Could not sign out. Check your connection and try again.');
  } finally {
    $('logout').disabled = false;
  }
});

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}

$('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('transcript-text').value);
    toast('Transcript copied');
  } catch {
    $('transcript-text').focus();
    $('transcript-text').select();
    toast('Press Ctrl+C or ⌘C to copy the selected transcript.');
  }
});
$('download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([$('transcript-text').value], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = transcriptFilename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('transcript-text').addEventListener('input', updateWordCount);
openWorkspace().catch(() => showLogin('Cannot reach the server. Please reload the page to try again.'));
