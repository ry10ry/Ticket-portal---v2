# Service Request

Service Requests are internal to MOMCC Infra, MOMCC Infra TL, MOMCC Infra Manager and Admin. Requesters cannot see the navigation, Dashboard, notifications or attachment APIs.

Raise Service Request immediately allocates `SR#MOMCC-YYYYMMDD-NN` using the Singapore date and an independent daily sequence. It creates an In-progress record, which remains after closing the dialog. Enter Subject and Description, attach optional files/screenshots, and save or Submit for Approval. Pending/Approved content is locked; return it before editing. Returned SRs retain their number and can be resubmitted.

Approval has one stage: **either MOMCC Infra TL or MOMCC Infra Manager can approve**. Both approvals are not required. Either can return with a required reason. Staff cannot approve their own request; Admin can manage any request and override. Internal users can comment and view version history.

Dashboard has exactly three cards:

- Submitted SR: distinct SRs submitted at least once, including returned/approved records; resubmissions do not increase this count.
- Pending Approval: currently awaiting TL or Manager approval.
- Total SR: all SRs, including In-progress records.

Notifications is a single sidebar item, with Fault Report and CR & SR tabs inside its page. The sidebar badge counts unread notifications. Submissions notify TLs, Managers and Admins. Approval/return notifies the submitting Infra and approvers. Notification links open the SR, and reading the notification page marks only the signed-in user's CR and SR notifications as read. Fault notifications remain on their own page and keep their independent read state.

Attachments allow five active files, up to 10 MB per file and 20 MB total. PNG, JPEG, GIF and WebP are detected from their content and displayed inline; other files, including SVG/HTML, are downloads. Removed files remain accessible in version history to internal users. Files and notifications are stored in MySQL and included in normal database backups. The existing HTTPS config's 45 MB request limit supports base64 upload encoding.

## Deployment files

Replace:

- app/main.py (registers the SR module)
- app/static/index.html (adds SR navigation and script)
- app/static/style.css (three-card Dashboard and image preview styles)

Add:

- app/services.py
- app/static/services.js
- tests/test_services.py (test coverage)
- SERVICE-REQUEST.md (this guide)

Keep all existing application, CR, template and dependency files. No dependency change or manual database migration is needed: startup creates new sr_sequences, service_requests, sr_files, sr_history and sr_notifications tables. Do not delete existing database volumes.

Merge these files into the extracted application, then rebuild and restart:

```powershell
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml up -d --build
```

Refresh with Ctrl+F5. If startup reports a missing app.services module, confirm that app/services.py is in the same application directory as app/main.py before rebuilding.

Validation: eleven tests passed on SQLite and a disposable MySQL database, covering existing fault/CR/email behavior, either-role approval, return/resubmit, Submitted counts, notification isolation/read state, image preview access, historical attachments and concurrent numbers. Chromium also exercised the SR UI, uploads, inline screenshot preview, approval and notification links.

## Admin deletion and close-after-save update

Admin can delete any SR after the confirmation dialog. The operation removes the request, current and historical attachments, all SR notifications and history in one transaction. Other roles are rejected by the backend. Version checks reject stale deletion requests, and daily numbers are never reused after deletion.

CR and SR details now close after a successful Save or Submit and refresh their list/Dashboard. Failed validation leaves the dialog open. Approval, return and comment operations keep their existing behavior.

Replace app/services.py, app/static/services.js and app/static/changes.js, then rebuild web/scheduler and refresh the browser. No database migration or new dependency is needed. Fourteen tests passed on SQLite/MySQL, and Chromium verified successful-save/submit closure, failed-submit retention, and Admin deletion cancellation/confirmation.

## Selected SR numbering date

Raise Service Request opens a required SR Date calendar. Confirming creates an In-progress SR and allocates SR#MOMCC-YYYYMMDD-NN using the selected date's independent counter. POST /api/services now requires sr_date in YYYY-MM-DD format. Cancel before confirming creates no record; closing the editor after creation retains the SR. Existing numbers remain unchanged, and deletion does not reset numbering. Actual creation timestamps and monthly report date boundaries remain unchanged. The selected date is shown in the request detail window.

Replace app/services.py, app/static/services.js and app/static/index.html, rebuild Docker and refresh the browser. No database migration or new dependencies are required.
