-- Non-destructive index for databases created before Fatia 3.
-- Review the target database before running this file. It is not executed automatically.
CREATE INDEX IF NOT EXISTS ix_pix_transactions_payer_received_at
    ON pix_transactions (payer_document, payer_branch, payer_account_number, received_at);
