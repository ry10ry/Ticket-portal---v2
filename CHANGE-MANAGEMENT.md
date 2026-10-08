# Change Management

These files extend the original application in `MOMCC-ServiceDesk-Full-v6-20261008.zip`. The ZIP is unchanged. Extract it, then copy this repository's `app/` and `tests/` files into the extracted application, merging directories and replacing matching files. Keep the remaining original files, including `requirements.txt`, `app/__init__.py`, and `tests/test_workflow.py`. Back up the database before deploying updates.

Start using the original application instructions. Startup creates three additional tables (`change_requests`, `cr_sequences`, `cr_history`); existing fault ticket data is retained. Restart both the web process and scheduler after copying the files. This release adds Change Management only; Service Management is still pending.

## Roles

Admin can view/manage all CRs and perform every review action. Admin's Users page now supports MOMCC Infra, Infra TL, and Infra Manager accounts in addition to Requester and Admin. Assign those roles to the appropriate people. Existing Infra accounts retain their existing role. Requesters cannot access CR APIs or navigation.

Infra staff can create CRs. The submitting user can edit and submit their own In-progress CR; Admin can manage any CR. TL supports or returns Pending Review CRs. Manager approves or returns Pending Approval CRs. Staff cannot support or approve their own CR; Admin retains override authority. Other internal staff can view records.

## Workflow

Creation immediately reserves `CR# MOMCC-YYYYMMDD-NN`, using Singapore's creation date and a daily sequence starting at 01. Number allocation is transaction-safe, including concurrent requests. Resubmission preserves the number. After 99 the sequence continues to 100 without truncation.

In-progress → Pending Review → Pending Approval → Approved. TL or Manager must provide a reason when returning a CR to In-progress. A returned CR goes through TL review again. Reviewed content is locked against editing. Version checks prevent stale edits/actions. Audit history preserves content snapshots and actor, timestamp, action, and return reason.

The Dashboard counts In-progress CR, Pending Review, Pending Approval, and Total CR across all internal records. Total includes Approved CRs. In-progress includes unsubmitted and returned CRs.

Each ticket records Background / Reason for Change, Scope of Change, and the screenshot's summary table: S/N, Environment, CR Number, Description, Deployment / Start Date/Time, and Impact Assessment. S/N is the summary row number (1), not the CR sequence. Deployment supports start/end dates and times in Singapore time, including multiple days. The CR number uses creation date independently of deployment date.

Change Request Form, Runbook, and Checklist-RFC-Impact now have template-backed online fields and DOCX/XLSX export. File names follow the CR number and Description; Checklist keeps its original name. Runbook includes all three workbook sheets, and Checklist includes its original reference matrix. Existing free-text content remains visible. Drafts may be incomplete; structured mandatory fields are checked before submission. See UPDATE-CR-TEMPLATES.md for the deployment file list and database upgrade.

## Validation

From the merged application's root, install its requirements plus `pytest==8.3.5 httpx==0.28.1`, then run `python -m pytest -q`. Run the complete suite: the original workflow tests initialize the test database/accounts, and the CR tests add approval and concurrency coverage. Never use a business database for tests. `TEST_DATABASE_URL` must name an empty disposable database if supplied; otherwise the existing test runner uses disposable SQLite.

Validated: eight tests passed on SQLite and on a separate disposable MySQL database; concurrent CR allocation; return/resubmit, role restrictions, stale versions and audit snapshots; existing fault workflows. Headless Chromium also verified sign-in, CR creation, online filling, submission, TL support and Manager approval using Admin override, Dashboard/detail rendering, and absence of JavaScript errors.
