/* Optional meeting hints, server-generated prompt previews, and private snapshots. */
class MeetingContext {
  constructor(onUnauthorized) {
    this.get = id => document.getElementById(id);
    this.onUnauthorized = onUnauthorized;
    this.rows = [];
    this.counter = 0;
    this.disabled = false;
    this.previewVersion = 0;
    this.savedVersion = 0;
    this.get('add-participant').addEventListener('click', () => {
      this.add();
      this.rows[this.rows.length - 1]?.name.focus();
    });
    this.get('preview-prompt').addEventListener('click', () => this.preview());
    for (const id of ['meeting-description', 'prompt']) {
      this.get(id).addEventListener('input', () => this.invalidatePreview());
    }
    this.get('close-job-context').addEventListener('click', () => this.get('job-context-dialog').close());
    this.get('job-context-dialog').addEventListener('close', () => {
      if (!this.get('job-context-dialog').open) this.clearSaved();
    });
    this.reset();
  }

  add(participant = {}) {
    if (this.disabled || this.rows.length >= 20) return;
    const create = (tag, text) => {
      const node = document.createElement(tag);
      if (text) node.textContent = text;
      return node;
    };
    const id = ++this.counter;
    const card = create('div'); card.className = 'participant-row';
    const heading = create('div'); heading.className = 'participant-heading';
    const title = create('span', 'Participant');
    const remove = create('button', 'Remove'); remove.type = 'button'; remove.className = 'quiet-button';
    const nameLabel = create('label', 'Name'); nameLabel.htmlFor = `participant-name-${id}`;
    const name = create('input'); name.id = nameLabel.htmlFor; name.type = 'text'; name.maxLength = 100;
    name.className = 'context-input'; name.dir = 'auto'; name.autocomplete = 'off'; name.value = participant.name || '';
    name.placeholder = 'For example, Sara';
    const descriptionLabel = create('label', 'Description (optional)'); descriptionLabel.htmlFor = `participant-description-${id}`;
    const description = create('textarea'); description.id = descriptionLabel.htmlFor;
    description.className = 'context-input'; description.rows = 2; description.maxLength = 1000; description.dir = 'auto';
    description.value = participant.description || ''; description.placeholder = 'Role, name pronunciation, or other useful context…';
    const row = { card, title, name, description, remove };
    heading.append(title, remove); card.append(heading, nameLabel, name, descriptionLabel, description);
    this.rows.push(row);
    this.get('participant-list').append(card);
    for (const input of [name, description]) input.addEventListener('input', () => this.sync());
    remove.addEventListener('click', () => {
      if (this.disabled) return;
      this.rows = this.rows.filter(item => item !== row);
      this.get('participant-list').replaceChildren(...this.rows.map(item => item.card));
      this.sync();
      this.get('add-participant').focus();
    });
    this.sync();
  }

  sync() {
    this.rows.forEach((row, index) => {
      row.title.textContent = `Participant ${index + 1}`;
      row.remove.setAttribute('aria-label', `Remove participant ${index + 1}`);
      row.name.required = Boolean(row.description.value.trim());
    });
    this.get('participants-data').value = JSON.stringify(this.rows.map(row => ({ name: row.name.value, description: row.description.value })).filter(item => item.name.trim() || item.description.trim()));
    this.get('add-participant').disabled = this.disabled || this.rows.length >= 20;
    this.invalidatePreview();
  }

  validate() {
    const invalid = this.rows.find(row => !row.name.value.trim() && row.description.value.trim());
    if (invalid) {
      invalid.name.focus();
      return 'Enter a name for each participant who has a description.';
    }
    return '';
  }

  setDisabled(disabled) {
    this.disabled = disabled;
    for (const row of this.rows) for (const input of [row.name, row.description, row.remove]) input.disabled = disabled;
    this.get('add-participant').disabled = disabled || this.rows.length >= 20;
    this.get('preview-prompt').disabled = disabled || Boolean(this.previewRequest);
  }

  reset(participants = []) {
    this.disabled = false;
    this.rows = [];
    this.get('participant-list').replaceChildren();
    for (const participant of participants.length ? participants : [{}]) this.add(participant);
    this.invalidatePreview();
  }

  invalidatePreview() {
    ++this.previewVersion;
    this.previewRequest?.abort();
    this.previewRequest = null;
    this.get('prompt-preview').hidden = true;
    this.get('prompt-preview-text').textContent = '';
    this.get('prompt-preview-error').hidden = true;
    this.get('prompt-preview-error').textContent = '';
    this.get('preview-prompt').disabled = this.disabled;
    this.get('preview-prompt').textContent = 'Preview final prompt';
  }

  async read(response) {
    if (response.status === 401) { this.onUnauthorized(); throw new Error('Please sign in again.'); }
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(' ') : data.detail;
      throw new Error(detail || 'Could not load the meeting details.');
    }
    return data;
  }

  async preview() {
    this.invalidatePreview();
    const validation = this.validate();
    if (validation) { this.get('prompt-preview-error').textContent = validation; this.get('prompt-preview-error').hidden = false; return; }
    const version = this.previewVersion;
    const controller = new AbortController(); this.previewRequest = controller;
    this.get('preview-prompt').disabled = true;
    this.get('preview-prompt').textContent = 'Building preview…';
    try {
      const data = await this.read(await fetch('/transcription/prompt-preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ prompt: this.get('prompt').value, meeting_description: this.get('meeting-description').value, participants: JSON.parse(this.get('participants-data').value) }),
      }));
      if (version !== this.previewVersion) return;
      this.get('prompt-preview-text').textContent = data.effective_prompt;
      this.get('prompt-preview').hidden = false;
    } catch (error) {
      if (version !== this.previewVersion || error.name === 'AbortError') return;
      this.get('prompt-preview-error').textContent = error instanceof TypeError ? 'Cannot reach the server. Try again.' : error.message;
      this.get('prompt-preview-error').hidden = false;
    } finally {
      if (version === this.previewVersion) {
        this.previewRequest = null;
        this.get('preview-prompt').disabled = this.disabled;
        this.get('preview-prompt').textContent = 'Preview final prompt';
      }
    }
  }

  clearSaved() {
    ++this.savedVersion;
    this.savedRequest?.abort(); this.savedRequest = null;
    if (this.get('job-context-dialog').open) this.get('job-context-dialog').close();
    for (const id of ['context-job-name', 'context-meeting', 'context-original-prompt', 'context-final-prompt', 'context-error']) this.get(id).textContent = '';
    this.get('context-participants').replaceChildren();
    this.get('context-saved-content').hidden = true;
    this.get('context-loading').hidden = true;
    this.get('context-error').hidden = true;
    this.get('view-job-context').disabled = false;
  }

  async showSaved(job) {
    this.clearSaved();
    const version = this.savedVersion;
    const controller = new AbortController(); this.savedRequest = controller;
    this.get('context-job-name').textContent = job.filename;
    this.get('context-loading').hidden = false;
    this.get('view-job-context').disabled = true;
    this.get('job-context-dialog').showModal();
    try {
      const data = await this.read(await fetch(`/jobs/${job.id}/context`, { signal: controller.signal }));
      if (version !== this.savedVersion) return;
      this.get('context-meeting').textContent = data.meeting_description || 'No meeting details saved.';
      const participants = data.participants || [];
      for (const participant of participants.length ? participants : [{ name: 'No participants saved.', description: '' }]) {
        const item = document.createElement('li'); item.dir = 'auto';
        item.textContent = participant.name + (participant.description ? ` — ${participant.description}` : '');
        this.get('context-participants').append(item);
      }
      this.get('context-original-prompt').textContent = data.prompt;
      this.get('context-final-prompt').textContent = data.effective_prompt;
      this.get('context-saved-content').hidden = false;
    } catch (error) {
      if (version !== this.savedVersion || error.name === 'AbortError') return;
      this.get('context-error').textContent = error instanceof TypeError ? 'Cannot reach the server. Try again.' : error.message;
      this.get('context-error').hidden = false;
    } finally {
      if (version === this.savedVersion) {
        this.savedRequest = null;
        this.get('view-job-context').disabled = false;
        this.get('context-loading').hidden = true;
      }
    }
  }

  clear() { this.reset(); this.clearSaved(); }
}
