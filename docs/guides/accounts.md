# Accounts and administration

[Documentation index](../README.md)

Open <http://localhost:8000/admin/> (with the trailing slash and no period) and sign in with a staff/superuser account. The standard Django admin includes users, groups, and transcription jobs.

To give someone access:

1. Open **Users → Add user**, enter a username, enable password authentication, and set a password.
2. Keep **Active** enabled. Regular people do not need **Staff status**, **Superuser status**, groups, or permissions to transcribe.
3. Give them their credentials and the frontend URL, <http://localhost:8000/>. They can upload recordings and return to their own history after signing in again.

Every active Django account can use the frontend. Only staff with the relevant permissions can enter Django admin; superusers can manage all accounts and recordings. Usernames can be up to 150 characters. Administrators can change passwords or deactivate accounts from the Users screen.

Jobs can be searched and filtered, and their settings, errors, and transcripts inspected. Job fields are read-only; retry and removal actions go through the queue so processing jobs cannot be deleted or edited. Admin actions are available to superusers. Django's standard CSRF protection applies to all admin forms. Existing API clients keep their current authentication flow.

Django's auth/admin/session tables share `jobs.sqlite3` with the existing queue table. The `TranscriptionJob` ORM model maps that existing table with `managed=False`, so migrations do not recreate it. Admin adds tables alongside the stored jobs. Jobs belong to permanent user IDs, not usernames: renaming an account retains its history, and deleting/recreating an account does not inherit someone else's recordings. The upgrade imports the original account and assigns matching legacy jobs to it; unknown legacy owners remain inaccessible to future accounts. All existing job IDs, transcripts, settings, and retry keys are preserved. The queue retains ownership of job state and its atomic concurrency controls.

## Related documentation

- [Model providers](model-providers.md)
- [Local setup](getting-started.md)
- [Docker and deployment](deployment.md)
- [API reference](../reference/api.md)
