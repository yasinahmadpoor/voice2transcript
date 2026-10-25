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

let selectedFiles = [];
let previewEntryId = null;
let uploadGeneration = 0;
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

function previewFile(entry) {
  if (!entry?.file || entry.status === 'invalid') return;
  clearPreview();
  previewEntryId = entry.id;
  previewUrl = URL.createObjectURL(entry.file);
  $('audio-preview').src = previewUrl;
  $('audio-preview').hidden = false;
  $('preview-name').textContent = `Preview: ${entry.name}`;
  $('preview-name').hidden = false;
  $('audio-preview').load();
}

function clearPreview() {
  $('audio-preview').pause();
  $('audio-preview').removeAttribute('src');
  $('audio-preview').hidden = true;
  $('preview-name').hidden = true;
  $('audio-preview').load();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  previewEntryId = null;
}

function pendingFiles() {
  return selectedFiles.filter(entry => ['ready', 'failed'].includes(entry.status));
}

function updateUploadButton() {
  const count = pendingFiles().length;
  $('transcribe-button').disabled = busy || count === 0;
  if (busy) return;
  const retry = count > 0 && pendingFiles().every(entry => entry.status === 'failed');
  $('transcribe-button').textContent = count
    ? `${retry ? 'Retry' : 'Transcribe'} ${count} recording${count === 1 ? '' : 's'} →`
    : 'Transcribe recordings →';
}

function renderFiles() {
  $('batch-review').hidden = selectedFiles.length === 0;
  const total = selectedFiles.reduce((sum, entry) => sum + entry.size, 0);
  $('batch-summary').textContent = `${selectedFiles.length} recording${selectedFiles.length === 1 ? '' : 's'} · ${(total / (1024 * 1024)).toFixed(2)} MB`;
  $('file-list').replaceChildren();
  for (const entry of selectedFiles) {
    const row = document.createElement('div');
    row.className = 'batch-file';
    row.setAttribute('role', 'listitem');
    const info = document.createElement('div');
    info.className = 'file-info';
    const name = document.createElement('strong');
    name.textContent = entry.name;
    const status = document.createElement('span');
    status.className = 'file-state';
    status.setAttribute('data-status', entry.status);
    const label = {ready:'Ready',invalid:'Invalid file',uploading:'Uploading…',accepted:'Submitted',failed:'Upload failed'}[entry.status];
    status.textContent = `${(entry.size / (1024 * 1024)).toFixed(2)} MB · ${label}`;
    info.append(name, status);
    if (entry.message) {
      const message = document.createElement('span');
      message.className = 'file-message';
      message.textContent = entry.message;
      info.append(message);
    }
    row.append(info);
    if (entry.jobId) {
      const view = document.createElement('button');
      view.type = 'button';
      view.className = 'secondary-button';
      view.textContent = 'View';
      view.setAttribute('aria-label', `View job for ${entry.name}`);
      view.addEventListener('click', () => {
        selectedJobId = entry.jobId;
        savedJob(entry.jobId);
        refreshJobs().catch(error => showError('jobs-error', error.message));
      });
      row.append(view);
    } else if (entry.file && entry.status !== 'invalid') {
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.className = 'secondary-button';
      preview.textContent = 'Preview';
      preview.setAttribute('aria-label', `Preview ${entry.name}`);
      preview.addEventListener('click', () => previewFile(entry));
      row.append(preview);
    }
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-button';
    remove.textContent = '×';
    remove.disabled = busy;
    remove.setAttribute('aria-label', `Remove ${entry.name} from upload list`);
    remove.addEventListener('click', () => {
      if (busy) return;
      if (previewEntryId === entry.id) clearPreview();
      selectedFiles = selectedFiles.filter(item => item.id !== entry.id);
      $('batch-progress').textContent = '';
      renderFiles();
    });
    row.append(remove);
    $('file-list').append(row);
  }
  updateUploadButton();
}

function addFiles(files) {
  if (busy) return;
  showError('upload-error');
  for (const file of Array.from(files || [])) {
    const duplicate = selectedFiles.some(entry => entry.status !== 'accepted' && entry.file === file);
    if (duplicate) continue;
    const message = file.size === 0 ? 'This file is empty.' : file.size > maxUploadMb * 1024 * 1024 ? `Exceeds the ${maxUploadMb} MB per-file limit.` : '';
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    selectedFiles.push({id,file,name:file.name,size:file.size,status:message?'invalid':'ready',message,jobId:null});
  }
  // Retain File objects in the batch and allow selecting the same file again.
  $('file-input').value = '';
  $('batch-progress').textContent = '';
  renderFiles();
  if (!previewEntryId) previewFile(selectedFiles.find(entry => entry.status === 'ready'));
}

$('file-input').addEventListener('change', (event) => addFiles(event.target.files));
$('clear-files').addEventListener('click', () => {
  if (busy) return;
  selectedFiles = [];
  clearPreview();
  $('batch-progress').textContent = '';
  renderFiles();
});
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
    if (name === 'drop') addFiles(event.dataTransfer.files);
  });
}
// Do not navigate away if a recording misses the drop target.
window.addEventListener('dragover', (event) => event.preventDefault());
window.addEventListener('drop', (event) => event.preventDefault());

function setBusy(value) {
  busy = value;
  $('file-input').disabled = value;
  $('clear-files').disabled = value;
  for (const input of settingInputs) input.disabled = value;
  $('reset-settings').disabled = value;
  $('dropzone').classList.toggle('disabled', value);
  renderFiles();
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
  const batch = pendingFiles();
  if (busy || !batch.length) return;
  showError('upload-error');
  // Snapshot settings once, before controls are disabled, for the whole batch.
  const settings = [...new FormData($('transcribe-form')).entries()].filter(([name]) => name !== 'file');
  const generation = ++uploadGeneration;
  setBusy(true);
  const controller = new AbortController();
  activeRequest = controller;
  let selectedFirst = false;
  try {
    for (const [index, entry] of batch.entries()) {
      if (generation !== uploadGeneration || !signedIn) return;
      entry.status = 'uploading';
      entry.message = '';
      $('transcribe-button').textContent = `Uploading ${index + 1} of ${batch.length}…`;
      $('batch-progress').textContent = `Uploading ${index + 1} of ${batch.length}: ${entry.name}`;
      renderFiles();
      const form = new FormData();
      for (const [name, value] of settings) form.append(name, value);
      form.set('file', entry.file);
      try {
        const response = await fetch('/jobs', {
          method:'POST',body:form,signal:controller.signal,
          headers:{'Idempotency-Key':entry.id},
        });
        if (generation !== uploadGeneration || !signedIn) return;
        if (response.status === 401) {
          resetWorkspace();
          showLogin('Your session has expired. Sign in again to see submitted jobs, then select any remaining files.');
          return;
        }
        if (!response.ok) {
          entry.status = 'failed';
          entry.message = await errorMessage(response);
          if (response.status === 429) {
            for (const remaining of batch.slice(index + 1)) {
              remaining.status = 'failed';
              remaining.message = 'Not uploaded: the queue is full. Retry when space is available.';
            }
            break;
          }
          continue;
        }
        const job = await response.json();
        if (generation !== uploadGeneration || !signedIn) return;
        entry.jobId = job.id;
        entry.status = 'accepted';
        if (previewEntryId === entry.id) clearPreview();
        entry.file = null;
        if (!selectedFirst) {
          selectedFirst = true;
          selectedJobId = job.id;
          savedJob(job.id);
          await viewJob(job).catch(error => showError('jobs-error', error.message));
        }
      } catch (error) {
        if (error.name === 'AbortError' || generation !== uploadGeneration) return;
        entry.status = 'failed';
        entry.message = 'Upload could not be confirmed. Retry upload to safely check or resume this file.';
        for (const remaining of batch.slice(index + 1)) {
          remaining.status = 'failed';
          remaining.message = 'Not uploaded after a connection error. Retry when connected.';
        }
        break;
      } finally {
        if (generation === uploadGeneration) renderFiles();
      }
    }
  } finally {
    if (generation === uploadGeneration) {
      activeRequest = null;
      setBusy(false);
      const accepted = selectedFiles.filter(entry => entry.status === 'accepted').length;
      const failed = selectedFiles.filter(entry => entry.status === 'failed').length;
      const invalid = selectedFiles.filter(entry => entry.status === 'invalid').length;
      $('batch-progress').textContent = `${accepted} submitted${failed ? ` · ${failed} upload failed — retry below` : ''}${invalid ? ` · ${invalid} invalid file${invalid === 1 ? '' : 's'}` : ''}.`;
      if (!previewEntryId) previewFile(pendingFiles()[0]);
    }
  }
  if (generation === uploadGeneration && signedIn) {
    await refreshJobs().catch(error => showError('jobs-error', error.message));
  }
});

function resetWorkspace() {
  stopPolling();
  ++uploadGeneration;
  ++selectionGeneration;
  activeRequest?.abort();
  activeRequest = null;
  selectedFiles = [];
  setBusy(false);
  clearPreview();
  $('file-input').value = '';
  $('batch-progress').textContent = '';
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
