# Transcribing recordings

[Documentation index](../README.md)

A Django app that transcribes audio or video with Gemini and adds speaker labels. Recordings run as persistent background jobs with **two active transcriptions** by default. The interface includes login, upload and preview, language and model settings, an editable prompt, recent job history, status updates, retries, and editable transcripts with copying and text downloads. Persian is the default language hint; leave it blank for auto-detection. Settings start collapsed and load defaults from the API.

The interface starts in dark mode. Use the theme button in the header to switch between dark and light; your choice is saved in the browser and applies to both the login page and workspace.

Select or drop multiple audio/video files to create a batch. Review sizes, preview recordings, and remove individual files before submitting. Shared transcription settings are captured once for the batch. Files upload sequentially; each accepted file becomes a separate background job, while two transcriptions can run at once. The per-file size limit still applies.

Each file shows its upload status. Invalid files are skipped, and failed files can be retried without resubmitting successful ones. If the queue fills or the connection drops, remaining uploads stop and can be retried later. Retry keys prevent duplicate jobs when a server response is lost. Clearing the upload list does not cancel jobs already submitted. Keep the page open until uploads finish; file selections and unsubmitted uploads are not preserved after refreshing or closing it.

## Meeting details and advanced instructions

The optional **About the meeting** field accepts the topic, agenda, terminology,
and background (up to 5,000 characters). Under **Participants**, add up to 20
people, each with a name (100 characters) and an optional description (1,000
characters), such as their role or name pronunciation. Empty rows are ignored;
a description requires a name. Add or remove rows without editing a prompt.
These hints apply to every recording in the batch.

Leave **Advanced prompt** closed to use the default transcription instructions.
Open it to change formatting or behavior. The app combines these instructions
with meeting and participant context on the server. **Preview final prompt**
shows that combined text without sending audio to a model or creating a job.
Changing any prompt/context field invalidates the old preview. The selected
recording language is still passed separately or appended by the provider's
existing adapter. Context supplies hints rather than replacing the recording:
the instructions ask for names only when supported by the audio and generic
`SPEAKER_1`, `SPEAKER_2` labels otherwise. They do not verify speaker identity.
Provider support for following prompts and speaker labeling varies.

Each job saves the original fields, original prompt, and combined prompt once.
Select a recording in history and click **View saved meeting details** to read
that snapshot. Viewing it does not replace the current form. Retry, resume,
and restart use the saved instructions even after the form or application
defaults change. Older recordings retain their original prompt and show no
meeting or participant details. **Reset to API defaults** clears context and
restores the default prompt. Signing out clears unsent context and previews;
unsubmitted context is not saved in browser storage.

## Background jobs and recovery

Uploads return a job ID immediately after the file is saved. Jobs run in queue order and expose **Queued → Processing → Completed / Failed** states. The frontend polls status every two seconds and shows queue position and active worker counts. You can submit another recording while existing jobs run, close the page, or sign out. Reopening the workspace restores the selected job and shows your most recent 50 recordings. Users can list, open, download, retry, or remove only their own jobs; another account's job ID returns HTTP 404. Upload retry keys are scoped to the user. Queue capacity and the two active transcription workers are shared across accounts; the displayed activity counts describe that shared queue. Transcripts remain stored until their job is removed; edits made in the transcript editor are only kept in the current page, so download edited text before leaving.

Queued jobs survive restarts and resume automatically. Jobs interrupted during processing are marked failed with an interruption message. Each successful audio chunk is saved immediately. When progress exists, **Resume transcription** reuses saved chunks and processes only the unfinished ones, using the original recording and settings. Jobs without saved progress show **Retry transcription**. Failed jobs are not automatically retried.

The transcript panel and recent recordings show saved progress, such as **3 of 10 chunks completed**. **Restart from beginning** discards saved chunks and transcribes the whole recording again. If the recording, chunk boundaries, model, prompt, or provider connection no longer match, resume stops with a message requiring a restart. API key rotation alone does not invalidate saved chunks.

Audio conversion and splitting may run again on resume; saved provider calls are skipped. An interrupted chunk without a committed result is processed again. With splitting disabled, the recording is a single chunk and has no intermediate checkpoint. Jobs that failed before checkpoint support was installed have no saved chunks to reuse. Speaker labeling follows the existing chunk-by-chunk provider behavior.

Select a queued or running recording in **Recent recordings**, then click **Cancel transcription** in the transcript panel or the selected job's actions. Running jobs show **Cancelling…** while their local process stops, then **Cancelled**. Other recordings keep running. Cancellation preserves the history entry and completed chunks; use **Resume transcription** or **Retry transcription** to continue with the same recording and settings. Closing the browser or signing out does not cancel work.

Cancellation closes the active provider connection and stops local audio conversion and transcription. A provider may still finish or charge for a request it already received.

Successful recordings are deleted locally after the transcript is committed. Failed, interrupted, and cancelled recordings are retained for retry for 24 hours, with cleanup running at startup and about once per minute. After that, upload the file again. **Remove recording** opens a confirmation dialog naming the recording. Choose **Delete transcription** to permanently remove the job and its stored result, or **Cancel** to keep it. Running or cancelling jobs must stop before removal.

## Related documentation

- [Accounts and administration](accounts.md)
- [API reference](../reference/api.md)
