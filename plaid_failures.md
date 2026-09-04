# Plaid Connection Failures

Patterns of Plaid Link failure and what each one means. Institution-specific
findings for this deployment live in `NOTES.md` (gitignored, local only).

## Failure categories

| Category | Meaning | What to do |
| -------- | ------- | ---------- |
| Not supported | Plaid does not list the institution at all | Check periodically; coverage changes. Otherwise manual entry. |
| Wrong product | Plaid lists a related entity, not the account you hold (e.g. a co-branded card rather than the main account) | Look for a first-party API before assuming Plaid is the route. |
| Partial enumeration | The item connects but exposes only some accounts | Institution-level product support does not imply every account is enumerated. Verify with a Link attempt. |
| 2FA incompatibility | Auth flow cannot complete through Link | Often resolved by the institution later; retry after a few months. |
| BNPL | Buy-now-pay-later providers are largely absent from Plaid | Manual entry. |

## Re-checking coverage

Institution support is not durable — providers that were unavailable often
appear later. Re-check with the free endpoints, which cost nothing:

```bash
# Search by name
curl -s -X POST https://production.plaid.com/institutions/search \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"...","secret":"...","query":"NAME","products":["balance"],"country_codes":["US"]}'

# Inspect a known institution id
curl -s -X POST https://production.plaid.com/institutions/get_by_id \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"...","secret":"...","institution_id":"ins_XXXX","country_codes":["US"]}'
```

`products` must be non-empty or the request returns `INVALID_PRODUCT`.

## Two traps

**Institution support ≠ account support.** An institution can advertise
`liabilities` and still enumerate only one of several accounts you hold there.
Only a Link attempt confirms which accounts appear.

**A recorded failure reason can be wrong.** Before accepting "this cannot be
automated," check whether the original measurement was sound. One provider in
this project was marked permanently manual based on a balance reading that
excluded funds held by open orders — the API had the full amount all along.
