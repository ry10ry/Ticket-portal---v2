# CR template update — 8 October 2026

This update implements the three supplied templates as online form fields with saved, versioned content and template-based document downloads. The original application ZIP remains unchanged.

## Naming

- CR Form: `CR#MOMCC-YYYYMMDD-NN - {Description}.docx`
- Runbook: `CR#MOMCC-YYYYMMDD-NN - Runbook.xlsx`
- Checklist: `Checklist-RFC-Impact_v1.0.xlsx`

The CR number comes from the ticket. Description comes from the summary's Description field. New descriptions update the document name after saving. File-system-invalid characters are replaced with spaces, and descriptions are limited to 120 characters in filenames. Existing ticket numbers are preserved; the space after `CR#` is removed in filenames only. No manual filenames are needed. Draft downloads first save edits; reviewed records export the saved content.

## Files to replace in an existing extracted application

| Path | Change |
| --- | --- |
| `app/changes.py` | Structured form storage/validation, automatic filenames, restricted export endpoints, larger content columns |
| `app/static/changes.js` | Online template fields, runbook tables, naming and download actions |
| `app/static/style.css` | Form/table styling |
| `requirements.txt` | Original dependency pins plus `lxml==6.1.3` for preserving OOXML namespaces |

The repository previously had no requirements.txt; it is newly added here but replaces the matching file from the extracted original package. If upgrading from the original package rather than the previous CR release, also merge all files listed in CHANGE-MANAGEMENT.md, including app/main.py, app/static/app.js, app/static/index.html and app/changes.py.

## Files to add

| Path | Purpose |
| --- | --- |
| `app/cr_documents.py` | Template field schema and DOCX/XLSX generation |
| `app/templates/cr-form.docx` | Supplied CR Form template (original bytes) |
| `app/templates/runbook.xlsx` | Supplied Runbook template (original bytes) |
| `app/templates/Checklist-RFC-Impact_v1.0.xlsx` | Supplied Checklist template (original bytes) |
| `migrations/001_cr_form_documents.sql` | Expand MySQL CR content/history columns without deleting data |
| `tests/test_zz_cr_documents.py` | Structured form, filenames, export, access and validation tests |
| `UPDATE-CR-TEMPLATES.md` | This update guide |

`CHANGE-MANAGEMENT.md` is also updated documentation. `app/main.py`, `app/static/app.js`, `app/static/index.html` and the original ZIP are unchanged in this update.

## Apply the update

1. Back up the database and application files. Stop the web server and scheduler using your normal deployment process.
2. Merge the update files into the extracted application directory, preserving the other original application files. Keep all three templates under `app/templates/`; do not place them under publicly served `app/static/`.
3. Run `python -m pip install -r requirements.txt` in the application's virtual environment. For Docker, rebuild the application image: its existing Dockerfile already copies the entire app directory, including templates, and installs requirements.
4. For an existing MySQL database, execute `migrations/001_cr_form_documents.sql` against that database using your normal database administration tool. The two statements are safe to repeat and keep existing CR content/history. SQLite requires no column migration. Fresh installations create the larger columns automatically.
5. Restart the web server and scheduler. Refresh the browser. Open a CR, enter Description and the template fields, then save or download each document. TL reviews the saved fields before Support; Manager reviews before Approval. Returned CRs retain the same number and must pass TL review again.

## Template behavior

CR Form represents request details, affected component, impact analysis, contingency plan, external ISTD/CRD records, and FM documentation updates. Date requested, CR number, environment, target implementation date and requestor are supplied by the ticket. Part 4 Manager approval is supplied by the actual recorded portal approval; drafts do not show the template's sample approvals. External ISTD/CRD fields are reference records, not additional workflow stages. The CR ticket still holds Background / Reason for Change and Scope of Change.

Runbook represents Pre-Launch, Implementation Day, Post-implementation, Contingency and Verification rows, Contact List and Actual Start-End. Chained planned start/end formulas are retained; Excel recalculates on opening. Blank chained planned-start fields keep the original formula. Entered text is exported as text, never as an executable Excel formula. Prior change dates, hostnames and sample contacts are not inherited into a new CR.

Checklist represents business/technical ratings, details, other resource impacts, overall levels, Completed By and Date. Overall Impact is calculated using the template's reference matrix. Checklist's name and reference sheet remain intact. One invalid `#N/A` print-title definition in the supplied template is removed only in generated exports so the workbook can open correctly; original template bytes are retained.

Existing free-text CR content is preserved in storage and remains visible in the online forms. New structured forms must be completed before submission. For existing records, populate the structured fields before using template exports; the previous free-text entries are not automatically mapped into template fields.

## Verification

Eight integration tests passed on SQLite and a separate disposable MySQL database. Export checks verified document contents, names, unchanged DOCX assets, retained runbook formulas, removal of stale sample approvals and blocked requester downloads. Headless Chromium verified online entry, three real downloads and filenames, and submit/support/approve.

Run the complete suite in the merged extracted application: install `pytest==8.3.5 httpx==0.28.1`, then run `python -m pytest -q`. The original `tests/test_workflow.py` initializes the shared test database and accounts. Never set TEST_DATABASE_URL to a business database.
