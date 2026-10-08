-- Back up the existing MySQL database first. Safe to rerun; retains existing content.
ALTER TABLE change_requests MODIFY COLUMN content LONGTEXT NOT NULL;
ALTER TABLE cr_history MODIFY COLUMN snapshot LONGTEXT NOT NULL;
