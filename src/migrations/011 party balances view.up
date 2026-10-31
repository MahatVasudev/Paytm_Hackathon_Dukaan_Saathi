-- The single place where debt direction is decided.
-- balance > 0 : the party owes the business
-- balance < 0 : the business owes the party
CREATE VIEW party_balances AS
SELECT
    t.business_id,
    t.party_id,
    SUM(CASE t.type
            WHEN 'sale'             THEN   t.total_amount - t.paid_amount
            WHEN 'purchase'         THEN -( t.total_amount - t.paid_amount)
            WHEN 'payment_received' THEN  -t.total_amount
            WHEN 'payment_paid'     THEN   t.total_amount
            ELSE 0
        END)::BIGINT AS balance
FROM transactions t
WHERE t.status = 'confirmed'
  AND t.party_id IS NOT NULL
GROUP BY t.business_id, t.party_id;
