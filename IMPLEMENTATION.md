# IMPLEMENTATION.md

Process and operational detail for this project. `CLAUDE.md` is intentionally
static — it describes the repo model and the invariants that do not change.
**This file is where process detail lives and gets updated.**

Anything naming a real institution, balance, mask, or account belongs in
`NOTES.md` (gitignored, local only) — never here.

---

## Documentation layout

| File | Audience | Changes | Contains |
| ---- | -------- | ------- | -------- |
| `CLAUDE.md` | Agents + contributors | Rarely | Repo model, invariants, cost rules |
| `IMPLEMENTATION.md` | Agents + contributors | Often | Workflow, process, operational detail |
| `NOTES.md` | Local only (gitignored) | Often | Real institutions, balances, per-account decisions |
| `README.md` | Public users | Occasionally | Setup and usage |

**The rule:** if a sentence would identify where a specific human banks or how
much they hold, it goes in `NOTES.md`. Generic mechanism goes here.

`sync-to-public.sh` stages via `git archive HEAD`, so untracked files are
structurally absent from the sync. That is the protection — not an exclude
list, which can go stale. Do not teach the sync script about individual files.

## Biweekly balance cycle

Runs on the 1st and 15th of each month.

```bash
# 0. In the sheet: run "Prep for New Week" FIRST.

# 1. Fetch automated balances (writes to the sheet directly).
python plaid_balance.py --force
python mercury_balance.py
python zillow_balance.py
python coinbase_balance.py

# 2. Enter manual balances in the sheet by hand.

# 3. Snapshot into history.db.
python balance_history.py snapshot

# 4. Review what changed.
python balance_history.py diff
```

Step 2 is why `snapshot` is never called automatically after a fetch — manual
rows have to land first, or the snapshot captures a half-updated sheet.

### Retrying a single institution

A bare `--force` bills every linked item at $0.10. When one institution fails —
a re-auth prompt, or a transient `INSTITUTION_NOT_RESPONDING` — scope the retry:

```bash
python plaid_balance.py --force --only "Institution Name"
```

Only the fetched rows are written; every other row keeps its current value.
A scoped run deliberately does not stamp the rate-limit clock, so a cheap retry
never blocks the next full sweep. An unknown name exits 2 and lists the valid
institutions rather than silently fetching nothing.

Re-running a full sweep to recover one item is the most common way to waste
money here. Scope it.

## Plaid cost model

Two billing models, needing opposite treatment:

| Endpoint | Billing | Implication |
| -------- | ------- | ----------- |
| `accounts/balance/get` | **Per-call**, $0.10/item | Minimize. Use `--cached` for dev. |
| `investments/holdings/get` | **Subscription**, ~$0.35/item/month, unlimited | Free to call. Do not "optimize" away. |
| `transactions/sync` | Subscription, ~$0.30/item/month | Currently unused |

Holdings being subscription-billed is easy to get wrong in the cautious
direction: removing a holdings call from `--cached` or `snapshot` saves $0 and
breaks history capture. Verify *how* an endpoint bills before treating it as
expensive — a code comment asserting a cost is a claim, not evidence.

## Adding a sheet row

A row whose column-H ID has no `accounts.yaml` entry fails the `balances`
foreign key. `snapshot` checks this before fetching or writing anything and
names the offending IDs. Add each under `manual_accounts` (`id`, `label`,
`type`), then re-run.

Row resolution is always by column-H ID, never by row number — rows move.
Multiple Plaid accounts may share an `id`; their balances are summed onto that
row.

## Automating a manual account

1. Check Plaid coverage with the free `institutions/search` and
   `institutions/get_by_id` endpoints — institution support changes over time,
   so a past "not supported" is not durable.
2. Confirm the *account* is enumerated, not just the institution. Institution-
   level product support does not imply every account under it is exposed;
   a lender may support `liabilities` and still surface only one loan.
3. If Plaid does not cover it, check for a first-party API.
4. Record the outcome and its reasoning in `NOTES.md`.

When reversing a prior "leave it manual" decision, state what specifically
changed. A decision recorded with a wrong measurement behind it will otherwise
be re-derived indefinitely.

## Publishing to the public repo

```bash
./sync-to-public.sh                              # dry run
./sync-to-public.sh --push --message "feat: …"  # opens a PR
```

Before publishing, confirm the staged content carries no personal data. The
dry run lists changed files; read the diff for any doc you have edited since
the last sync. Gitignored files cannot leak, but a tracked doc can — that is
how balances and an employer name reached a public repo in September 2026.
