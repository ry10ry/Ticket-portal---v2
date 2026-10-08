# Monthly reports and combined internal notifications

Monthly report now offers Fault Ticket, Change Request and Service Request. Select the month and download the corresponding CSV. CR/SR reports select records by creation month using Singapore time (UTC+8), matching the existing fault report. CSV includes the current status, submitter and content, SGT timestamps, current-cycle support/approval records where applicable, and filenames. It does not export attachment bytes. Fields that resemble spreadsheet formulas are escaped, and UTF-8 BOM supports Excel.

CR & SR Notifications displays both types in one page, newest first, marked CR or SR. Clicking opens the corresponding request. The badge counts unread internal notifications; opening the page marks that user's CR/SR notifications read without marking fault notifications read. Existing SR notifications are included. New CR actions generate notifications: Submit to TL/Admin, Support to Manager/Admin, Approval/Return to the submitting Infra/Admin, comments to the submitting Infra/Admin. Historical CR actions are not converted into retroactive notifications. Fault Notifications remains an independent page.

## Deployment

Replace these five files in the extracted application:

- app/main.py
- app/changes.py
- app/static/app.js
- app/static/services.js
- app/static/index.html

Add app/internal_views.py. Keep all other CR, SR, email and template files. No dependency change or manual migration is required; normal startup creates cr_notifications. New tests: tests/test_zz_reports_notifications.py.

Rebuild web/scheduler with the existing Compose settings, then refresh the browser with Ctrl+F5:

```powershell
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml up -d --build
```

Verification: thirteen tests passed on SQLite and a disposable MySQL database, including Singapore month boundaries, CSV escaping, internal access restrictions, combined notification identities, and separate fault read states. Chromium downloaded CR/SR monthly CSVs and exercised combined notification links and the separate fault page.
