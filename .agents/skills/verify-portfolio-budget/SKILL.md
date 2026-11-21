---
name: verify-portfolio-budget
description: Verify the attached local portfolio guard whenever reservation, settlement, retries, or crash handling changes.
---

Run `python3 scripts/verify_budget.py` and `python3 -m unittest discover -s tests -p test_portfolio_guard.py` from the repository root with Python 3.10 or later. No credentials are required.

The bounded checker drives the production Guard against fresh SQLite files. It tries every three-attempt sequence for two identities and two provider outcomes under two budget policies (128 schedules). At provider entry it uses a second database connection to confirm the maximum reservation is already committed. It checks expected per-identity admission, total spend, conservative handling of missing usage, and exact settlement. It also kills a subprocess immediately after simulated generation dispatch and attempts a retry under another identity. The unit suite adds a true two-caller race, per-investigation limits, disable and fail-closed cases.

The synchronization points are Guard.investigation, Investigation._reserve, Investigation.model, and _transaction in portfolio_guard.py. The check was matched to the guard introduced at f93e779. Update that record after rechecking any changes to the guard. Full-output and uncached-input pricing are fixed assumptions. The proof scope is finite; provider billing, storage hardware, and the operating system are outside it.

To test the check itself, make a disposable copy of the guard and checker preserving their relative paths. Omit the pending reservation from the comparison in Investigation._reserve and rerun the checker on that copy. It must fail when a provider call exceeds the independently calculated balance. Restore the comparison and require both verification commands to pass. Never run mutations in a user's active service.
