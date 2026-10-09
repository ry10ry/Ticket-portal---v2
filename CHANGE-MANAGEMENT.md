# Change Management — current workflow

Raise Change Request immediately allocates a date-based CR number and creates an In-progress record. Closing the dialog does not delete it. Dashboard totals include those records.

The portal edits Description, Background / Reason for Change and Scope of Change. Description is displayed first and supplies the CR Form download filename. Template downloads save the entered content before generating the file. Download CR Form, Runbook and Checklist templates, complete them locally, then upload them as DOCX/XLSX documents. All three uploaded documents and both text fields are required before Submit. Saving without submitting is permitted. Previously stored summary and online-form content is retained in the database.

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

The upload release passed seven tests on SQLite and a separate empty MySQL database, covering existing fault workflows, immediate creation, uploads/downloads, access control, comments, return/resubmission, file history, concurrent numbering and Admin deletion. Chromium exercised the complete create/close/reopen/download/upload/comment/support/approve/delete UI flow. Tests must use disposable databases, never business data.

Service Request is now available as a separate internal module; see SERVICE-REQUEST.md. The earlier UPDATE-CR-TEMPLATES.md describes the superseded online-form release; this document describes the current workflow.

## ISTD email draft

After the current submission has TL Support and Manager Approval, Approved CRs offer Download ISTD Email Draft (.eml). The draft includes Background / Reason for Change, Scope of Change, Singapore-time support/approval records, three exact documents from the approved revision, and a separate support/approval text record. Receipts identify actual actors (including Admin overrides); new audit snapshots capture identity at the time of the action.

The backend rejects incomplete or stale approval cycles, and never substitutes approval from before a return/resubmission. No email is sent by the portal. Recipients and sender are left for the user to select in Outlook. The EML has X-Unsent: 1; support for opening it directly as an editable draft depends on Outlook version. If Outlook opens it in reading mode, use Forward to create an editable message and verify the attachments before sending. Outlook itself has not been exercised in this Linux environment.

The email update passed nine tests on SQLite, including attachment bytes and approval-version checks, and Chromium downloaded and validated the actual EML.

For this email update, replace app/changes.py and app/static/changes.js and add app/cr_email.py. No new dependency or database migration is required. Rebuild the application image, then refresh the browser. Service Request notifications are now implemented in the separate SR module.


### ISTD body format update

The draft body now contains Dear ISTD, bold Background / Reason for Change and Scope of Change headings with their entered text, followed by “For you review and support.” and the screenshot-style summary table. S/N is 1 and CR Number is filled; Environment, Description, Deployment Start Date/Time and Impact Assessment remain blank for editing in Outlook. Support/approval details appear only in the independent text attachment. Approval prerequisites and the three approved document attachments are unchanged. Replace app/cr_email.py, rebuild web/scheduler, and download a new draft; existing downloaded EML files do not change.

## ISTD approval evidence and closure

Internal Manager Approval leaves the CR at Approved. Infra downloads the ISTD email draft and sends it manually in Outlook. After receiving ISTD approval, the submitting Infra or Admin uploads the approval Email or supporting document using Upload Approval Evidence. Accepted formats: EML, MSG, PDF, DOCX, XLSX, XLS, PNG and JPEG, maximum 10 MB each. Earlier proof versions remain in history when replaced. File format validation checks the uploaded format; the submitting user verifies that the content records ISTD approval.

Close CR becomes available only after evidence is uploaded, and asks the user to confirm that ISTD approved the change. The backend independently enforces Approved status, owner/Admin access, current revision and the existence of evidence. Closing records the actor/time and changes status to Closed; uploads are then disabled. Closed CRs remain in Total CR and can be found with the Closed status filter. The proof, history and original ISTD email draft remain available to internal staff. Evidence upload and closure notify Admin and the owner, excluding the actor. Admin can still delete a Closed CR.

Replace app/changes.py, app/cr_email.py, app/static/changes.js and app/static/index.html, rebuild the Docker application and refresh the browser. No new dependencies or database migration are required.

## Selected CR numbering date

Raise Change Request now opens a required CR Date calendar before creating the record. Confirming the selected date calls POST /api/changes with cr_date in YYYY-MM-DD format and allocates CR# MOMCC-YYYYMMDD-NN using that date's independent transactional counter. Missing/invalid dates are rejected. Cancelling the date picker creates no record; after confirmation, closing the CR editor retains the In-progress record and its assigned number. Existing CR numbers remain unchanged, and counters are not reset after deletion. The selected date is shown in the CR detail window. Creation timestamps and monthly reports continue to reflect actual creation time.

Replace app/changes.py, app/static/changes.js and app/static/index.html, rebuild Docker and refresh the browser. No database migration is required; the existing per-date sequence table is reused.

## Admin-configurable CR approval workflow

Admin settings now includes Change Request Workflow. Add up to eight ordered stages; each has a label, assigned internal role and action type (Review & Support or Approval). Move stages up/down, remove stages, or load the default TL Review & Support -> Manager Approve flow. Save CR Workflow explicitly commits the configuration. At least one stage is required, and the final stage must be an approval. No requester-role access is added: the requester here means the Infra person raising the CR.

Every newly raised CR captures the configured stages. Existing CRs retain their captured workflow; legacy CRs without a captured workflow use the original TL -> Manager sequence. Admin configuration changes do not change drafts or active/approved records already created. Submission enters the first stage; each configured decision advances one stage. Only the assigned role or Admin can act, and the existing restriction against approving one's own CR remains (Admin can override). A return requires a reason and goes to In-progress; resubmission restarts all captured stages.

Review-type stages count as Pending Review, approval-type stages as Pending Approval. Only completion of the last stage produces Approved. Notifications go to the next stage's assigned role. The detail window shows the captured flow/current stage. ISTD drafts require all decisions in the current submission cycle over the same content and documents; the separate approval record attachment includes every stage and actor. Manager-only flows do not invent TL support. The CSV export includes Workflow Approval Records; Closed records retain approval metadata. Completion evidence and Close CR remain required after internal approval.

Configuration is saved separately from Fault Ticket settings in the existing settings table, with a version check against concurrent Admin edits. No dependency or database migration is required.

Add: app/cr_workflow.py and app/static/cr-workflow.js. Replace: app/changes.py, app/cr_email.py, app/internal_views.py, app/static/changes.js, app/static/index.html and app/static/style.css. Rebuild Docker and refresh with Ctrl+F5.
