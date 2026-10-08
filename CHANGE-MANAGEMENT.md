# Change Management — current workflow

Raise Change Request immediately allocates a date-based CR number and creates an In-progress record. Closing the dialog does not delete it. Dashboard totals include those records.

The portal edits only Background / Reason for Change and Scope of Change. Download CR Form, Runbook and Checklist templates, complete them locally, then upload them as DOCX/XLSX documents. All three uploaded documents and both text fields are required before Submit. Saving without submitting is permitted. Previously stored summary and online-form content is retained in the database.

Submit → Pending Review → MOMCC Infra TL Support → Pending Approval → MOMCC Infra Manager Approval → Approved. TL/Manager can return the CR with a required comment; it returns to In-progress. Resubmissions retain the CR number and pass TL review again. Staff cannot review their own CR; Admin can manage all records. Requesters cannot access CR APIs, templates, files or Dashboard.

Internal staff can download the completed files and add comments. Uploaded replacements retain older file versions; history provides download links for files saved at that revision. Admin can permanently delete a CR, all of its files and history after the UI confirmation. The daily sequence is never reset by deleting a CR.

## Deploy this update

Replace app/changes.py and app/static/changes.js, and update ssl/nginx.conf for the original HTTPS setup. Keep app/cr_documents.py, all three app/templates files, the updated requirements.txt, and the Change Management registration at the end of app/main.py. Do not modify the original application ZIP. Source files in this repository must be merged into the extracted application.

At startup SQLAlchemy creates the new cr_files table automatically. Files are stored in the database, including previous versions, so database backups now also cover uploaded CR documents. Earlier MySQL deployments still need migrations/001_cr_form_documents.sql to widen the CR content/history fields if it has not been applied already.

Uploads allow one DOCX CR Form and two XLSX documents, each up to 10 MB. The supplied nginx configuration uses client_max_body_size 45m to accommodate the three files plus JSON base64 encoding. Rebuild web/scheduler after source changes and restart the HTTPS proxy after its config changes. Do not delete database volumes.

For the original HTTPS Compose deployment:

```powershell
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml up -d --build
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml restart proxy
```

Use the actual HTTPS service name from compose.ssl.yaml if it differs from proxy. Refresh the browser with Ctrl+F5.

Seven tests passed on SQLite and a separate empty MySQL database, covering existing fault workflows, immediate creation, uploads/downloads, access control, comments, return/resubmission, file history, concurrent numbering and Admin deletion. Chromium exercised the complete create/close/reopen/download/upload/comment/support/approve/delete UI flow. Tests must use disposable databases, never business data.

Service Management is not yet implemented. The earlier UPDATE-CR-TEMPLATES.md describes the superseded online-form release; this document describes the current workflow.
