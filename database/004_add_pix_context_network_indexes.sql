-- Índices incrementais para as agregações locais da política adaptativa V3.
-- Aplicar somente em uma base existente; instalações novas já usam database.sql.
-- Etapa histórica: a migração 005 substitui estes índices pelos equivalentes
-- baseados em FKs e pela coluna INET tipada.

CREATE INDEX IF NOT EXISTS ix_pix_transactions_payee_received_at
    ON pix_transactions (
        payee_participant,
        payee_branch,
        payee_account_number,
        payee_document,
        received_at
    );

CREATE INDEX IF NOT EXISTS ix_pix_transactions_device_received_at
    ON pix_transactions (source_device_id, received_at);

CREATE INDEX IF NOT EXISTS ix_pix_transactions_source_ip_received_at
    ON pix_transactions ((request_payload #>> '{source,ip}'), received_at);
