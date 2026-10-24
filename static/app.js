const $ = (id) => document.getElementById(id);

function updateThemeToggle() {
  const dark = document.documentElement.dataset.theme === 'dark';
  $('theme-icon').textContent = dark ? '☀' : '☾';
  $('theme-label').textContent = dark ? 'Light mode' : 'Dark mode';
  $('theme-toggle').setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
}

$('theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').setAttribute('content', theme === 'dark' ? '#131722' : '#f7f8fa');
  try {
    localStorage.setItem('voice-theme', theme);
  } catch {
    // The toggle still works for this page when storage is unavailable.
  }
  updateThemeToggle();
});
updateThemeToggle();

let selectedFile = null;
let previewUrl = null;
let maxUploadMb = 500;
let busy = false;
let activeRequest = null;
let toastTimer = null;
let transcriptFilename = 'transcription.txt';
let transcriptionDefaults = null;
let pollTimer = null;
let pollGeneration = 0;
let signedIn = false;
let accountName = '';
let selectedJobId = null;
let selectedJob = null;
let loadedResultId = null;
let selectionGeneration = 0;
let recentJobs = [];
let historySignature = '';
let refreshTask = null;
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
  signedIn = false;
  stopPolling();
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
  accountName = data.username;
  selectedJobId = savedJob();
  signedIn = true;
  const generation = ++pollGeneration;
  await pollJobs(generation);
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
  $('transcribe-button').textContent = value ? 'Uploading recording…' : 'Transcribe recording →';
  $('file-input').disabled = value;
  $('remove-file').disabled = value;
  for (const input of settingInputs) input.disabled = value;
  $('reset-settings').disabled = value;
  $('dropzone').classList.toggle('disabled', value);
}

function updateWordCount() {
  const text = $('transcript-text').value.trim();
  $('word-count').textContent = `${text ? text.split(/\s+/u).length : 0} words`;
}

function savedJob(id) {
  try {
    const key = `voice-selected-job:${accountName}`;
    if (id !== undefined) {
      if (id) localStorage.setItem(key, id);
      else localStorage.removeItem(key);
    }
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function stopPolling() {
  clearTimeout(pollTimer);
  ++pollGeneration;
}

function labelFor(job) {
  if (job.status === 'queued') return `Queued · #${job.queue_position}`;
  if (job.interrupted && job.status === 'failed') return 'Interrupted';
  return { processing: 'Processing', completed: 'Completed', failed: 'Failed' }[job.status];
}

function renderHistory(jobs) {
  const signature = JSON.stringify([selectedJobId, jobs]);
  if (signature === historySignature) return;
  historySignature = signature;
  $('jobs-list').replaceChildren();
  $('jobs-empty').hidden = jobs.length > 0;
  for (const job of jobs) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'job-row';
    row.setAttribute('aria-current', String(job.id === selectedJobId));
    const name = document.createElement('span');
    name.className = 'job-name';
    name.textContent = job.filename;
    const status = document.createElement('span');
    status.className = 'job-state';
    status.textContent = labelFor(job);
    status.setAttribute('data-status', job.status);
    row.append(name, status);
    row.addEventListener('click', () => {
      selectedJobId = job.id;
      savedJob(job.id);
      renderHistory(recentJobs);
      viewJob(job).catch(error => showError('jobs-error', error.message));
    });
    $('jobs-list').append(row);
  }
}

async function viewJob(job) {
  const changed = selectedJob?.id !== job?.id;
  if (changed) {
    ++selectionGeneration;
    loadedResultId = null;
    $('transcript-text').value = '';
  }
  selectedJob = job;
  const pending = job && ['queued', 'processing'].includes(job.status);
  $('processing').hidden = !pending;
  $('empty-result').hidden = Boolean(job);
  $('job-failure').hidden = !job || job.status !== 'failed';
  $('result').hidden = !job || job.status !== 'completed' || loadedResultId !== job.id;
  $('result-badge').hidden = !job || job.status !== 'completed';
  $('job-actions').hidden = !job;
  $('transcript-panel').setAttribute('aria-busy', String(Boolean(pending)));
  $('retry-job').hidden = !job || !job.retryable;
  $('delete-job').disabled = job?.status === 'processing';
  if (!job) {
    $('transcript-status').textContent = 'Ready when you are';
    return;
  }
  $('selected-job-note').textContent = `Selected: ${job.filename}`;
  $('delete-job').textContent = job.status === 'queued' ? 'Cancel queued recording' : 'Remove recording';
  $('transcript-status').textContent = `${labelFor(job)} · ${job.filename}`;
  if (pending) {
    loadedResultId = null;
    $('processing-title').textContent = job.status === 'queued' ? 'Waiting for a transcription slot' : 'Finding the words…';
    $('processing-description').textContent = job.status === 'queued'
      ? `Queue position ${job.queue_position}. Your recording will start when a worker is available. You can leave and return later.`
      : 'Your job continues if you close this page. Reopen it to check the result.';
    const started = job.status === 'queued' ? (job.queued_at || job.created_at) : (job.started_at || job.created_at);
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - started));
    $('elapsed').textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} ${job.status === 'queued' ? 'waiting' : 'elapsed'}`;
  } else if (job.status === 'failed') {
    loadedResultId = null;
    $('job-error').textContent = `${job.error}${job.retryable ? '' : ' Please upload the recording again.'}`;
  } else if (job.status === 'completed' && loadedResultId !== job.id) {
    const generation = selectionGeneration;
    const response = await fetch(`/jobs/${job.id}/result`);
    if (!response.ok) throw new Error(await errorMessage(response));
    const text = await response.text();
    if (generation !== selectionGeneration || selectedJobId !== job.id || !signedIn) return;
    $('transcript-text').value = text;
    $('result-file').textContent = job.filename;
    transcriptFilename = `${job.filename.replace(/\.[^.]+$/, '') || 'recording'}-transcript.txt`;
    loadedResultId = job.id;
    updateWordCount();
    $('result').hidden = false;
  }
}

function refreshJobs(generation = pollGeneration) {
  if (refreshTask?.generation === generation) return refreshTask.promise;
  const promise = fetchJobStatus(generation).finally(() => {
    if (refreshTask?.promise === promise) refreshTask = null;
  });
  refreshTask = { generation, promise };
  return promise;
}

async function fetchJobStatus(generation) {
  const response = await fetch('/jobs');
  if (generation !== pollGeneration || !signedIn) return;
  if (response.status === 401) {
    resetWorkspace();
    showLogin('Your session has expired. Sign in again to see your recordings.');
    return;
  }
  if (!response.ok) throw new Error(await errorMessage(response));
  const data = await response.json();
  if (generation !== pollGeneration || !signedIn) return;
  recentJobs = data.jobs;
  $('queue-summary').textContent = `${data.active} / ${data.max_active} processing · ${data.queued} queued`;
  let job = recentJobs.find(item => item.id === selectedJobId);
  if (!job && selectedJobId) {
    const older = await fetch(`/jobs/${selectedJobId}`);
    if (generation !== pollGeneration || !signedIn) return;
    if (older.ok) job = await older.json();
    else if (older.status !== 404) throw new Error(await errorMessage(older));
  }
  if (!job) job = recentJobs[0] || null;
  if (generation !== pollGeneration || !signedIn) return;
  selectedJobId = job?.id || null;
  savedJob(selectedJobId);
  renderHistory(recentJobs);
  await viewJob(job);
  showError('jobs-error');
}

async function pollJobs(generation) {
  try {
    await refreshJobs(generation);
  } catch {
    if (generation === pollGeneration && signedIn) {
      showError('jobs-error', 'Cannot refresh job status. Your jobs continue on the server. Reconnecting…');
    }
  } finally {
    if (generation === pollGeneration && signedIn) {
      pollTimer = setTimeout(() => pollJobs(generation), 2000);
    }
  }
}

$('refresh-jobs').addEventListener('click', () => refreshJobs().catch(error => showError('jobs-error', error.message)));

$('retry-job').addEventListener('click', async () => {
  if (!selectedJob) return;
  $('retry-job').disabled = true;
  try {
    const response = await fetch(`/jobs/${selectedJob.id}/retry`, { method: 'POST' });
    if (!response.ok) throw new Error(await errorMessage(response));
    await refreshJobs();
  } catch (error) {
    showError('jobs-error', error.message);
  } finally {
    $('retry-job').disabled = false;
  }
});

$('delete-job').addEventListener('click', async () => {
  if (!selectedJob) return;
  $('delete-job').disabled = true;
  try {
    const response = await fetch(`/jobs/${selectedJob.id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error(await errorMessage(response));
    selectedJobId = null;
    savedJob(null);
    await refreshJobs();
  } catch (error) {
    showError('jobs-error', error.message);
  } finally {
    $('delete-job').disabled = selectedJob?.status === 'processing';
  }
});

$('transcribe-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedFile || busy) return;
  showError('upload-error');
  const file = selectedFile;
  const form = new FormData($('transcribe-form'));
  // Dragged files may differ from the native input's current FileList.
  form.set('file', file);
  $('transcript-status').textContent = 'Uploading recording…';
  setBusy(true);
  const controller = new AbortController();
  activeRequest = controller;
  try {
    const response = await fetch('/jobs', { method: 'POST', body: form, signal: controller.signal });
    if (response.status === 401) {
      resetWorkspace();
      showLogin('Your session has expired. Please sign in again.');
      return;
    }
    if (!response.ok) throw new Error(await errorMessage(response));
    const job = await response.json();
    if (activeRequest !== controller || !signedIn) return;
    selectedJobId = job.id;
    savedJob(job.id);
    await viewJob(job);
    setBusy(false);
    chooseFile(null);
    await refreshJobs();
  } catch (error) {
    if (error.name !== 'AbortError') {
      showError('upload-error', error instanceof TypeError ? 'Connection lost. Check Recent recordings before uploading again; your job may already have been queued.' : error.message);
    }
  } finally {
    if (activeRequest === controller) activeRequest = null;
    setBusy(false);
  }
});

function resetWorkspace() {
  stopPolling();
  ++selectionGeneration;
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
  $('job-failure').hidden = true;
  $('job-actions').hidden = true;
  $('empty-result').hidden = false;
  selectedJobId = null;
  selectedJob = null;
  loadedResultId = null;
  recentJobs = [];
  historySignature = '';
  $('jobs-list').replaceChildren();
  $('jobs-empty').hidden = false;
  $('queue-summary').textContent = '';
  showError('jobs-error');
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
