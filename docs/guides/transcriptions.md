# Transcribing recordings

[Documentation index](../README.md)

A Django app that transcribes audio or video with Gemini and adds speaker labels. Recordings run as persistent background jobs with **two active transcriptions** by default. The interface includes login, upload and preview, language and model settings, an editable prompt, recent job history, status updates, retries, and editable transcripts with copying and text downloads. Persian is the default language hint; leave it blank for auto-detection. Settings start collapsed and load defaults from the API.

The interface starts in dark mode. Use the theme button in the header to switch between dark and light; your choice is saved in the browser and applies to both the login page and workspace.

Select or drop multiple audio/video files to create a batch. Review sizes, preview recordings, and remove individual files before submitting. Shared transcription settings are captured once for the batch. Files upload sequentially; each accepted file becomes a separate background job, while two transcriptions can run at once. The per-file size limit still applies.

Each file shows its upload status. Invalid files are skipped, and failed files can be retried without resubmitting successful ones. If the queue fills or the connection drops, remaining uploads stop and can be retried later. Retry keys prevent duplicate jobs when a server response is lost. Clearing the upload list does not cancel jobs already submitted. Keep the page open until uploads finish; file selections and unsubmitted uploads are not preserved after refreshing or closing it.

## Background jobs and recovery

Uploads return a job ID immediately after the file is saved. Jobs run in queue order and expose **Queued → Processing → Completed / Failed** states. The frontend polls status every two seconds and shows queue position and active worker counts. You can submit another recording while existing jobs run, close the page, or sign out. Reopening the workspace restores the selected job and shows your most recent 50 recordings. Users can list, open, download, retry, or remove only their own jobs; another account's job ID returns HTTP 404. Upload retry keys are scoped to the user. Queue capacity and the two active transcription workers are shared across accounts; the displayed activity counts describe that shared queue. Transcripts remain stored until their job is removed; edits made in the transcript editor are only kept in the current page, so download edited text before leaving.

Queued jobs survive restarts and resume automatically. Jobs interrupted during processing are marked failed with an interruption message; use **Retry transcription** to restart them with their original recording and settings. They are not automatically retried, which avoids silently repeating provider requests. A retry starts from the beginning, not from a saved audio chunk.

Select a queued or running recording in **Recent recordings**, then click **Cancel transcription** in the transcript panel or the selected job's actions. Running jobs show **Cancelling…** while their local process stops, then **Cancelled**. Other recordings keep running. Cancellation preserves the history entry, and **Retry transcription** starts it again with the same recording and settings. Closing the browser or signing out does not cancel work.

Cancellation closes the active provider connection and stops local audio conversion and transcription. A provider may still finish or charge for a request it already received.

Successful recordings are deleted locally after the transcript is committed. Failed, interrupted, and cancelled recordings are retained for retry for 24 hours, with cleanup running at startup and about once per minute. After that, upload the file again. **Remove recording** deletes a job and its stored result; running or cancelling jobs must stop before removal.

## Related documentation

- [Accounts and administration](accounts.md)
- [API reference](../reference/api.md)
