-- Non-destructive upgrade for databases created during Fatia 1.
-- Review the target database before running this file. It is not executed automatically.
ALTER TABLE pix_transactions
    ADD COLUMN IF NOT EXISTS canonicalization_version TEXT;

UPDATE pix_transactions
SET canonicalization_version = 'phase-1-provisional'
WHERE canonicalization_version IS NULL;

ALTER TABLE pix_transactions
    ALTER COLUMN canonicalization_version SET NOT NULL;
