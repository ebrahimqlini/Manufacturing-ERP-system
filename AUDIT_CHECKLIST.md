# Prompt 10 Internal Audit Checklist

## 1. Manufacturing Financial Document Integrity

### Records to Protect (Post-Finalization/Posting)
| Model | Lifecycle Transition | Currently Protected? | Notes |
|-------|---------------------|---------------------|-------|
| MaterialIssue | Posted (create-once; reversal tracked on movement) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` | Admin read-only; DB triggers deferred (direct-SQL residual) |
| FinishedGoodsReceipt | Posted (create-once; reversal tracked on movement) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` | Admin read-only; DB triggers deferred (direct-SQL residual) |
| ProductionResult | Recorded (submission_key, occurred_at) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` (create-once; every recorded result is posted) | Admin read-only; DB triggers deferred (direct-SQL residual) |
| OperationProgress | Recorded (submission_key) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` (create-once) | Admin read-only; DB triggers deferred (direct-SQL residual) |
| ProductionOrder | Released (not DRAFT) | ORM guards: queryset `update`/`delete`/`bulk_update` + instance `delete` for non-DRAFT; instance `save` blocks only status changes (services set `_allow_status_transition`); drafts fully editable | Admin read-only; DB triggers deferred (direct-SQL residual) |
| ProductionMaterialRequirement | Created at release (order non-DRAFT) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` once order is non-DRAFT | Part of order snapshot; DB triggers deferred (direct-SQL residual) |
| ProductionOrderOperation | Created at release (order non-DRAFT) | ORM guards: instance `save`/`delete` + queryset `update`/`delete`/`bulk_update` once order is non-DRAFT (services set `_allow_status_transition` for status changes) | Part of order snapshot; DB triggers deferred (direct-SQL residual) |

### Write Paths to Audit
- Instance `save()` / `delete()`
- `QuerySet.update()` / `delete()` / `bulk_update()`
- Django admin bulk actions
- Services: `issue_material`, `record_production_result`, `receive_finished_goods`, `record_operation_progress`, `complete_operation`, `release_order`, `cancel_order`, `complete_order`
- API: Read-only for owner, customer order request only
- Direct SQL (needs DB triggers)

### Required Protections
1. Add `PostedManufacturingQuerySet` with `update/delete/bulk_update` rejection
2. Add instance `save/delete` guards checking posted state
3. Add PostgreSQL triggers (reversible migration) for critical tables
4. Ensure reversals still work via service layer

## 2. Monthly Closing Concurrency Race

### Vulnerable Write Paths (Check Outside Transaction)
| Service/Function | Check | Location | Fix |
|------------------|-------|----------|-----|
| `issue_material` | `ensure_period_open` | Line 168-170, before `atomic` | Move inside `atomic` with lock |
| `record_production_result` | `ensure_period_open` | Line 319-322, before `atomic` | Move inside `atomic` with lock |
| `create_shipment` | `ensure_profit_period_open` | Line 291-293, before `atomic` | Move inside `atomic` with lock |
| `reverse_movement` (inventory) | `_ensure_finalized_period_open_for_movement` | Line 178, before `atomic` | Move inside `atomic` with lock |
| `receive_finished_goods` | No direct period check (depends on order status) | - | Add check inside `atomic` |

### Locking Protocol (as implemented)
- Mechanism: PostgreSQL transaction-scoped advisory locks via `SELECT pg_advisory_xact_lock(key)`, exposed as `lock_costing_month(year, month)` and `lock_profit_month(year, month)` in `costing/models.py`. Each key combines a fixed per-family salt (`0x43500001` costing, `0x50460001` profit) with year/month, so the two lock families never collide. Locks are released automatically at commit or rollback.
- Why not `select_for_update()` alone: locking a `CostingPeriod`/`MonthlyProfitPeriod` row that does not exist yet locks nothing, so the first posting of a brand-new month could pass its "is the month open?" check against a missing row while another transaction finalizes that month concurrently. Advisory locks exist whether or not any row exists.
- Order: when a write touches both periods, always `lock_costing_month` before `lock_profit_month`, and both advisory locks before any row lock (period rows, documents, inventory). Inverting the order risks deadlock against finalize/reopen/reversal paths. Both locks are re-entrant within one transaction (PostgreSQL counts acquisitions), so nested service calls (finalize → calculate → expense save) do not block themselves.
- Callers: operational writes `issue_material` and `record_production_result` take the costing-month lock; `create_shipment` takes the profit-month lock; `reverse_movement` takes the costing-month lock plus the profit-month lock for every month it would change (posting month and, for sales shipments, the shipment's month); `calculate_monthly_costing`, `finalize_costing_period`, `reopen_costing_period`, `reverse_monthly_expense`, and `MonthlyExpense.save()`/`delete()` take costing before profit; the profit services (`calculate_monthly_profit`, `finalize_monthly_profit`, `reopen_monthly_profit`) take the profit-month lock.
- Limitations: advisory locks are invisible to standard row-lock tooling, are not inherited by direct SQL that bypasses the service layer, and no DB triggers exist (direct-SQL residual risk remains, as in section 1). `receive_finished_goods` has no period check or lock (it remains dependent on order status only). Verified by `costing/tests_concurrency.py` (10 `TransactionTestCase` tests with overlapping worker threads) plus the service-level tests; concurrency coverage targets costing/profit finalization and first-creation races, not every caller listed above.

## 3. Authorization Hardening

### Current State (as implemented)
- Services enforce `require_permission(actor, "<app_label>.<codename>")` from `core/permissions.py` **plus** `require_staff(actor)`; both are required — the permission alone is not enough, and staff status alone is not enough.
- Django permissions are declared in model `Meta.permissions` (manufacturing 8, inventory 2, purchasing 3, sales 4, costing 4 + profit 3); no groups are used — permissions are granted per user.
- Views: expense create/edit/reverse require staff + `costing.manage_expenses` (`manage_expenses_required`); costing financial read remains staff OR `costing.view_costing_financials` (`costing_financials_required`, pre-existing).
- Admin: `MonthlyExpenseAdmin` add/change/delete require `costing.manage_expenses`; the read-only manufacturing admin registrations are pre-existing and unchanged by Prompt 10.
- API: unchanged — `IsOwnerUser` (staff), `IsCustomerUser` (non-staff + customer account); no API-level granular permissions were added.
- Note: `save_draft_order` (manufacturing/sales/purchasing) and `reopen_completed_order` (sales) reuse existing codenames (`release_order`, `confirm_sales_order`, `confirm_purchase_order`, `complete_sales_order`) rather than dedicated ones — flagged for owner review.

### Required Authorization Matrix
| Operation | Required Permission | Current Check |
|-----------|--------------------|---------------|
| Post inventory movement | `inventory.post_movement` | `require_permission` + `require_staff` (verified) |
| Reverse inventory movement | `inventory.reverse_movement` | `require_permission` + `require_staff` (verified) |
| Issue material | `manufacturing.issue_material` | `require_permission` + `require_staff` (verified) |
| Record production result | `manufacturing.record_production_result` | `require_permission` + `require_staff` (verified) |
| Receive finished goods | `manufacturing.receive_finished_goods` | `require_permission` + `require_staff` (verified) |
| Record operation progress | `manufacturing.record_operation_progress` | `require_permission` + `require_staff` (enforced; no negative test) |
| Complete operation | `manufacturing.complete_operation` | `require_permission` + `require_staff` (enforced; no negative test) |
| Release order | `manufacturing.release_order` | `require_permission` + `require_staff` (enforced; no negative test) |
| Cancel order | `manufacturing.cancel_order` | `require_permission` + `require_staff` (enforced; no negative test) |
| Complete order | `manufacturing.complete_order` | `require_permission` + `require_staff` (enforced; no negative test) |
| Create purchase receipt | `purchasing.create_receipt` | `require_permission` + `require_staff` (enforced; no negative test) |
| Create sales shipment | `sales.create_shipment` | `require_permission` + `require_staff` (verified) |
| Finalize costing period | `costing.finalize_costing_period` | `require_permission` + `require_staff` (verified) |
| Reopen costing period | `costing.reopen_costing_period` | `require_permission` + `require_staff` (verified) |
| Finalize monthly profit | `costing.finalize_monthly_profit` | `require_permission` + `require_staff` (verified) |
| Reopen monthly profit | `costing.reopen_monthly_profit` | `require_permission` + `require_staff` (verified) |
| Create/reverse expense | `costing.manage_expenses` | `require_permission` + `require_staff`; views use `manage_expenses_required` (verified) |
| View financial reports | `costing.view_costing_financials` (exists) | `costing_financials_required` (staff OR permission) (verified) |

"verified" = a negative-permission test exists (`core/tests.py::AuthorizationBoundaryTests`, plus `issue_material` in `manufacturing/tests.py` and `reopen_completed_order` in `sales/tests.py`). "enforced; no negative test" = the check is implemented in the service but no test asserts its rejection.

### Implementation
1. Define Django permissions in model `Meta.permissions`
2. Create `@require_permission` decorator or service-level checks
3. Apply to service functions, views, admin, API
4. Add tests for each permission boundary

---

## Implementation Priority

1. **HIGH** - Manufacturing document immutability (ORM + DB triggers)
2. **HIGH** - Concurrency race fixes (move period checks inside transactions)
3. **MEDIUM** - Authorization matrix with Django permissions
4. **MEDIUM** - Regression tests for all three areas