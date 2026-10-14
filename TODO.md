# TODO

## Immediate setup

- [x] Initialize Git and commit the foundation.
- [ ] Update the available Python 3.12.14 runtime to the documented 3.12.15 patch (SQLite validation used 3.12.14).
- [x] Install the pinned requirements into `.venv` and run Django checks, migrations, and tests against SQLite.
- [ ] Install PostgreSQL 18 for local integration checks and verify the environment-based connection configuration.

## Before production use

- [ ] Select and document secret management, allowed hosts, TLS, and deployment server settings.
- [ ] Agree with the factory accountant on currency/tax/fiscal calendar, chart of accounts, posting rules, stock valuation, rounding, and corrections.
- [ ] Confirm numbering scopes, warehouse/location policy, UOM conversions, BOM/routing versions, yield, scrap, and quality rules with factory stakeholders.
- [ ] Design and rehearse PostgreSQL backup, restore, retention, and off-machine storage.

## Future implementation phases

- [ ] Confirm business rules and implement catalog and units of measure.
- [ ] Implement inventory ledger and valuation only after costing decisions.
- [ ] Implement purchasing and controlled receiving/invoice posting.
- [ ] Implement versioned BOM/routings and production reporting.
- [ ] Implement sales, delivery, invoice, returns, and receipts.
- [ ] Implement accounting posting, trial balance, and financial statements.
- [ ] Add reports, authorization matrix, audit review, and deployment hardening.
