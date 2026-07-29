# Engineering Context

Both pricing functions serve the same checkout-total contract during an endpoint
migration. The authoritative rule is a 5% surcharge plus a fixed 0.30 fee, rounded
half-up to cents. No compatibility contract permits the legacy endpoint to return a
different customer total.
