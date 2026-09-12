-- Run the whole script in a PostgreSQL SQL editor connected to the CRM database.
BEGIN;
CREATE TEMP TABLE crm_lesson_ids (kind text PRIMARY KEY, id bigint NOT NULL) ON COMMIT DROP;

WITH inserted AS (
    INSERT INTO customers_client (name, phone, created_at)
    VALUES ('SQL lesson client', '+77000000000', CURRENT_TIMESTAMP)
    RETURNING id
)
INSERT INTO crm_lesson_ids SELECT 'client', id FROM inserted;

WITH inserted AS (
    INSERT INTO services_service (name, description, created_at)
    VALUES ('SQL lesson ' || gen_random_uuid()::text, 'Training only', CURRENT_TIMESTAMP)
    RETURNING id
)
INSERT INTO crm_lesson_ids SELECT 'service', id FROM inserted;

WITH inserted AS (
    INSERT INTO leads_lead (title, client_id, service_id, employee_id, created_at)
    VALUES ('SQL lesson lead',
        (SELECT id FROM crm_lesson_ids WHERE kind = 'client'),
        (SELECT id FROM crm_lesson_ids WHERE kind = 'service'),
        NULL, CURRENT_TIMESTAMP)
    RETURNING id
)
INSERT INTO crm_lesson_ids SELECT 'lead', id FROM inserted;

SELECT id, name, phone, created_at
FROM customers_client
WHERE id = (SELECT id FROM crm_lesson_ids WHERE kind = 'client');

UPDATE customers_client SET phone = '+77000000001'
WHERE id = (SELECT id FROM crm_lesson_ids WHERE kind = 'client')
RETURNING id, name, phone;

SELECT l.id, l.title, c.name AS client, s.name AS service, e.username AS employee
FROM leads_lead AS l
JOIN customers_client AS c ON c.id = l.client_id
JOIN services_service AS s ON s.id = l.service_id
LEFT JOIN accounts_user AS e ON e.id = l.employee_id
WHERE l.id = (SELECT id FROM crm_lesson_ids WHERE kind = 'lead');

SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('accounts_user', 'customers_client', 'services_service', 'leads_lead')
ORDER BY tablename, indexname;

EXPLAIN SELECT * FROM customers_client WHERE phone = '+77000000001';

DELETE FROM leads_lead WHERE id = (SELECT id FROM crm_lesson_ids WHERE kind = 'lead');
DELETE FROM customers_client WHERE id = (SELECT id FROM crm_lesson_ids WHERE kind = 'client');
DELETE FROM services_service WHERE id = (SELECT id FROM crm_lesson_ids WHERE kind = 'service');
ROLLBACK;
