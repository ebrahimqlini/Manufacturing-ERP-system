# TODO and implementation sequence

## Foundation

- [x] Initialize Git and create the minimal Django foundation.
- [x] Pin runtime dependencies and document Python interpreter selection.
- [x] Verify SQLite and PostgreSQL checks, migrations, rollback-safe database read/write, and tests.
- [x] Record both production lines and their still-open process requirements without adding business functionality.
- [ ] Before deployment, rotate the short local PostgreSQL password, confirm network exposure, configure HTTPS/HSTS, and test a current Python patch.
- [ ] Before operational stock use, create an authorized staff administrator and have the factory confirm lot/serial requirements.

## Prompt 1 — shared catalog and inventory (implemented)

- [x] Implement configurable product categories for raw materials, WIP, finished goods, packaging materials, scrap, and consumables.
- [x] Implement configurable units of measure and product-specific Decimal conversions with a documented 12-decimal precision limit and reject-on-overflow behavior.
- [x] Implement shared warehouses/locations and one immutable stock ledger for both production lines.
- [x] Test both product families, shared stock, movements/reversals, conversions, atomicity, constraints, authorization, and PostgreSQL behavior.
- [x] Keep manufacturing, purchasing, sales, and accounting workflows outside Prompt 1.
- [x] Enforce the confirmed no-negative-physical-stock policy transactionally for issues, transfers, decreasing adjustments, and reversals; allow later valid partial production issues without creating shortage movements.
- [x] Guard posted movement and ledger immutability through supported ORM operations and PostgreSQL UPDATE/DELETE triggers; record remaining privilege-level bypass limits.

## Prompt 2 — configurable manufacturing (implemented; verification in IMPLEMENTATION_STATUS.md)

- [x] Add configurable production lines, product-specific versioned BOMs/routings, orders, operation progress, production quantities, scrap, and line attribution.
- [x] Configure paper-cup routes from product-specific printing/cutting/forming/optional bottom assembly/packaging operations.
- [x] Record the confirmed fiber operation (pressing fiber material in a mold) without inventing other stages, materials, machines, units, or rates; keep product BOMs/routes configurable.
- [x] Reuse the shared inventory ledger for partial material issues and separate finished-goods receipts; record shortages as information, not movements.
- [x] Test route/BOM snapshots, partial issues, shortage and stock limits, good/scrap quantities, finished receipts, order limits, authorization, and database behavior.
- [x] Defer WIP balances/transfers and cost calculation because factory/accounting policies and inputs are unconfirmed.
- [ ] Obtain owner/accountant approval of the costing method before calculating production costs; confirm WIP location/transfer policy and fiber inputs/stages before extending workflows.

## Later prompts

- [ ] Prompt 3: shared accounting foundation with consolidated and production-line cost dimensions.
- [ ] Prompt 4: shared supplier/purchasing/receiving workflows against the common inventory ledger.
- [ ] Prompt 5: shared customer/sales/delivery/returns workflows against the common inventory and accounting interfaces.
- [ ] Prompt 6: read-only factory-wide and production-line reports, with reconciled consolidated totals.
- [ ] Prompt 7: approved authorization matrix, audit review, backups/restore, monitoring, and deployment hardening.

See [PROMPT_SPECS.md](PROMPT_SPECS.md) for scope, acceptance gates, and required tests for every planned prompt. See [DECISIONS.md](DECISIONS.md) for architecture and unresolved factory/accounting policies.
