# Presale multi-copy orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One presale order holds several version lines (`version × quantity`), each version has an optional per-buyer limit, and the reserve form reuses the cart's address fields.

**Architecture:**
- A new `PreSaleOrderItem` replaces `PreSaleOrder.option`.
- `PreSaleOrder.item_price` stays the order's record total, so the ledger, balance and payment code keep their shape.
- Rules that read "the option" read "the lines" instead. Arrival-based rules use `all_arrived(order)`.

**Tech Stack:** Django 5.2 / DRF 3.16 / pytest (backend `bemoctezuma_records`), React 19 / TS / Tailwind (frontend `femoctezuma-records`). Both repos are on `feature/presales`.

**Spec:** `docs/superpowers/specs/2026-11-14-presale-multi-copy-design.md`

## Global Constraints

- **Naming and style:**
  - Keep `serilizers` and `Genere` spellings. Money is `DecimalField(max_digits=10, decimal_places=2)`.
  - FKs set `related_name`.
  - Store days use `timezone.localdate()`.
  - Frontend copy is Spanish; use Tailwind tokens only, no raw hex. Don't run prettier on the frontend.
- **Approval before stateful steps:** `makemigrations`, `migrate` and `git commit` run only with the owner's go-ahead. Approval of this plan covers the ones listed here, on the local DB and on `feature/presales`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Checks:**
  - Backend: `python3 -m pytest apiApp/tests/ -q` stays green after every task.
  - Frontend: `npm run build` and `npm run lint` show 0 errors.
- **Tests stay hermetic:** Stripe and shipping are mocked, as the existing presale tests do.

## Review Focus

1. Bad `items` input (quantity 0 or negative, the same version twice, a version of another presale, an empty list, a non-list) → 400 `invalid_items`, never a 500 or a partial order.
2. An expired or cancelled order whose lines were partly delivered must not release the delivered copies back to stock (`copies_left` counts delivered lines always).
3. The per-buyer limit counts the buyer's live `pending_payment` holds too (a second tab can't bypass it).
4. A Stripe payment arriving after the hold expired, when any line's version ran out → `oversold`, the money is kept, the store is notified.
5. Settling (liquidar) after a partial pickup charges only the remaining balance and still works.

---

### Task 0: Commit the pending work first

Commit the uncommitted work (per-version images, page redesign, emails, home cards, colonias, the `get_full_name` fix) to `feature/presales` in both repos before the refactor starts. Use one commit per theme:

- **Backend:**
  - `[add] per-version photos on presale create`
  - `[fix] emails: new layout, Spanish dates, money format, plain text skips CSS`
  - `[fix] buyer name never "None None"`
- **Frontend:**
  - `[add] presale page: version photos, back and share, link previews`
  - `[add] home presale cards with version photos`
  - `[add] colonias in presale address`
  - `[add] per-version photo upload in admin`

Run both checks first. Don't commit `.env.local`.

### Task 1: Data model + migration

**Files:**
- Modify: `apiApp/models/presales.py`, `apiApp/models/__init__.py`, `apiApp/admin.py`, `apiApp/tests/presale_factories.py`
- Create: `apiApp/migrations/0059_presale_order_items.py` (generated, plus a `RunPython` step)
- Test: `apiApp/tests/test_presales_models.py`

**Interfaces:**
- Produces:
  - `PreSaleOrderItem` fields: `order` (FK `PreSaleOrder`, CASCADE, `related_name='items'`), `option` (FK `PreSaleOption`, PROTECT, `related_name='items'`), `quantity` (`PositiveIntegerField`, default 1), `unit_price`, `unit_discount` (money, default 0), `delivered_at` (nullable `DateTimeField`), `delivered_by` (FK user, SET_NULL, `related_name='presale_items_delivered'`).
  - `PreSaleOrderItem.line_total` property: `(unit_price - unit_discount) * quantity`.
  - `PreSaleOption.max_per_buyer`: nullable `PositiveIntegerField`.
  - `PreSaleOrder.option` is removed.
  - The factory becomes `make_order(option=None, quantity=1, lines=None, **kw)`. `lines` is a list of `(option, qty)`. It creates the order with `item_price = Σ price×qty` (unless given) plus its items.

- [ ] **Step 1: Write the failing test**

```python
def test_order_lines_total_and_copies(db):
    red = make_option(copies=10); black = make_option(red.presale, name='Negro', price=D('600'))
    order = make_order(lines=[(red, 2), (black, 3)])
    assert order.item_price == D('3400')
    assert [i.quantity for i in order.items.order_by('id')] == [2, 3]
    assert order.items.first().line_total == D('1600')
```

- [ ] **Step 2: Run it.** Expected: FAIL (`make_order() got an unexpected keyword 'lines'`, or there's no `items`).
- [ ] **Step 3: Implement the model and the factory.** Run `makemigrations apiApp -n presale_order_items`. Add `RunPython(forward, migrations.RunPython.noop)` before the `RemoveField`. `forward` creates one item per order: `quantity=1`, `unit_price=item_price+discount_applied`, `unit_discount=discount_applied`, `delivered_at`/`delivered_by` copied when the status is `entregado`. Admin: an `PreSaleOrderItem` inline on `PreSaleOrderAdmin`. Remove `option` from its `list_display`.
- [ ] **Step 4: Run it.** Expected: PASS. The rest of the suite fails until Task 2. That's expected and noted in the ledger.
- [ ] **Step 5:** Run `python manage.py migrate` on the local DB. Then check the shell: `PreSaleOrderItem.objects.count() == PreSaleOrder.objects.count()`.
- [ ] **Step 6:** Commit at the end of Task 2, when the suite is green again.

### Task 2: Service rules on lines

**Files:**
- Modify: `apiApp/services/presales.py`, `apiApp/services/shipping.py`
- Test: `apiApp/tests/test_presales_service.py` and `test_presales_review_fixes.py` (update their calls), plus new tests in `test_presales_service.py`

**Interfaces:**
- Consumes: the Task 1 model.
- Produces:
  - `parse_items(raw, presale=None) -> list[tuple[PreSaleOption, int]]`. Raises `PresaleError('invalid_items')` when: `raw` isn't a non-empty list; a quantity isn't an int ≥ 1; an `option_id` repeats or doesn't exist; or the options belong to different presales (or not to `presale` when given).
  - `copies_left(option) -> int` = `copies` − Σ `quantity` of lines whose order holds copies, **plus** lines with `delivered_at` set, whatever the order status.
  - `held_by_user(option, user) -> int`: Σ `quantity` in that user's holding orders, live holds included.
  - `all_arrived(order) -> bool` and `last_arrival(order) -> datetime | None` (None unless all arrived).
  - `split_amount(lines, amount, delivery, shipping_quote) -> {'item_price','discount','shipping_cost','charge','full'}`, using the spec's sums. `full` is True when the amount ≥ Σ `(price − discount) × qty`.
  - `create_order(lines, *, buyer, source, delivery, shipping_details, amount, method=None, shipping_quote=None, created_by=None, accept_terms, note='')`:
    - it locks every option (`select_for_update`, ordered by pk);
    - `sold_out` carries the version name: `PresaleError('sold_out', f'Ya no quedan copias de {name}.')`;
    - for online orders with `user` and a `max_per_buyer`, it raises `PresaleError('limit_exceeded', f'Puedes llevar hasta {max} de {name}' + (f'; ya tienes {held}.' if held else '.'))`;
    - each item stores `unit_discount` when `full`.
  - `deliver(order, *, by, item_ids=None)`: `None` means every arrived, undelivered line. Lines must belong to the order and have arrived (`invalid_items` otherwise). `PresaleError('paid_not_enough', f'Faltan {missing} para entregar esto.')` when paid < Σ `line_total` of the lines delivered so far plus these. The order becomes `entregado` only when every line is delivered and the balance is 0.
  - `quote_shipping(lines, zip_code)`, using `shipping.build_package_for_items(lines)`, where the units are Σ qty and the grams are tare + Σ unit weight × qty, built like the cart's package.
  - `mark_arrived(option, *, by) -> list[PreSaleOrder]`: the orders that have a line of it.
  - `requote_shipping` and `mark_shipped` require `all_arrived`. `refresh_status` uses `all_arrived` for `listo`. `deadline` and `is_expirable` use `last_arrival`.
  - `reopen` checks `copies_left ≥ 0` after counting the order back, for every line.
  - `fulfil_presale_session` sets `oversold` when any line's version has `copies_left < 0` after the expired hold is counted.
  - `option.name` in `_log` notes becomes the line summary `'Rojo ×2, Negro ×3'` (helper `lines_label(order) -> str`).

- [ ] **Step 1: Write the failing tests** (the names carry the assertions):
  - `test_split_amount_sums_lines`: red 800×2 and black 600×3 with min 200 and discount 50 each: min is 1000 and full is 3150. Amount 1000 gives `item_price` 3400 and `discount` 0. Amount 3150 gives `item_price` 3150, `discount` 250 and `full` True.
  - `test_parse_items_rejects_bad_input`, parametrized over `[]`, `'x'`, `[{'option_id': red.id, 'quantity': 0}]`, a duplicate id, an id from another presale, and an unknown id → `invalid_items`.
  - `test_limit_counts_existing_and_live_holds`: `max_per_buyer=2`. The user holds 1 paid and 1 `pending_payment` with a live hold. A new order of 1 → `limit_exceeded`. An admin order (`method='cash'`) of 1 succeeds.
  - `test_copies_left_counts_quantities` and `test_delivered_lines_never_return_to_stock` (a partly delivered order is cancelled, and the delivered line's copies stay taken).
  - `test_deadline_waits_for_last_arrival`: one line arrived → the deadline is the reservation + 120 days. All arrived with the last arrival late → last arrival + 15 days.
  - `test_partial_pickup_needs_paid_lines`: a paid deposit of 1000 can't cover red ×2 = 1600, so delivering it → `paid_not_enough`. After paying 600 more, delivering red works and the status isn't `entregado`. After liquidating the rest and delivering black → `entregado`.
  - `test_shipping_waits_for_all_arrived`: `mark_shipped` with one line missing → `invalid_status`.
  - `test_package_for_items_counts_units`: two lines of 2 and 3 LPs give height 10 and weight (300+5×300)/1000 = 1.8.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement the interfaces above.** Update the existing tests' `create_order(option, ...)` calls to `create_order([(option, 1)], ...)`.
- [ ] **Step 4: Run** `python3 -m pytest apiApp/tests/test_presales_service.py apiApp/tests/test_presales_review_fixes.py apiApp/tests/test_presales_models.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** the model and service together: `[add] presale orders hold several version lines`.

### Task 3: Ventas and consignors per line

**Files:**
- Modify: `apiApp/services/sales.py`
- Test: `apiApp/tests/test_presales_sales.py`

**Interfaces:**
- Consumes: `order.items` and `PreSaleOrderItem.line_total`.
- Produces: `presale_figures(order)`. It keeps its keys, but `'line'` becomes `'lines'`: a list of `SimpleNamespace(record=None, owner, price=line_total/qty, quantity, cost_price)` per item. `cost` is Σ `cost_price × qty`, and `uncosted` is Σ qty without cost.

Changes:
- `metrics` and the rows export loop over `figures['lines']`, with `add_record` keyed `presale:{option_id}`, and units are Σ qty.
- `owner_id` filtering keeps the lines of that owner.
- `notify_presale_owner(order)` sends one email per distinct owner, listing that owner's lines. It sets `owner_notified` once after all owners.
- The `_paid_presales` select/prefetch becomes `prefetch_related('payments', 'items__option__presale', 'items__option__owner')`.

- [ ] **Step 1: Failing tests:**
  - `test_metrics_split_lines_by_version`: an order with red ×2 (cost 300) and black ×1 (cost 200), paid in full. Presale units are 3 and cost is 800. The records table has two rows with the right gross.
  - `test_owner_emails_go_per_owner`: two owners → 2 emails; `owner_notified` is True.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `test_presales_sales.py` and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `[add] Ventas reports presale lines`.

### Task 4: API

**Files:**
- Modify: `apiApp/serilizers/presales.py`, `apiApp/views/presales.py`, `apiApp/views/presales_admin.py`
- Test: `test_presales_api.py`, `test_presales_admin.py`, `test_presales_checkout.py` (update and extend)

**Interfaces:**
- Consumes: Task 2's service.
- Produces:
  - **Order payload:** `items: [{id, option_id, option_name, image, quantity, unit_price, unit_discount, line_total, arrived, delivered_at}]`, `all_arrived` and `lines_label`. It drops `option` and `arrived`. `presale.cover` = the presale cover.
  - **Public option:** gains `max_per_buyer` and `bought`, where `bought = held_by_user(option, request.user)` for an authenticated request, else 0.
  - **Admin option serializer:** `max_per_buyer` is writable.
  - **`POST /presales/reserve/`:**
    - body `{items, amount, delivery, shipping_details?, accept_terms}`;
    - `shipping_details` keeps `reference` (optional, ≤ 200 chars);
    - a 409 for `sold_out` and `limit_exceeded`.
  - **`POST /presales/shipping-estimate/`:** body `{items, zip}`.
  - **Stripe `_checkout`:** reserve sends one line item per order line, `"{title} — {name} ×{qty} (apartado|pago completo)"`, with the item amount spread across lines in proportion to their totals, cents rounded, the remainder on the last line. Settle keeps a single line, `"{title} (liquidación)"`.
  - **Admin `POST /presales/admin/orders/`:** takes `items`.
  - **Admin deliver:** takes optional `item_ids`.
  - **Admin filters:** `ready`/`expiring` use `all_arrived`. `presale_totals` counts copies (Σ qty). The orders/extend querysets filter `items__option__presale=presale`, using `.distinct()`.
  - **`_orders_qs` and `_own_order`:** prefetch `items__option`.

- [ ] **Step 1: Failing tests:**
  - `test_reserve_two_versions_one_order`: POST items red×2 + black×1 → 200. One order with 2 items. The Stripe mock gets 2 line items whose amounts sum to the charge.
  - `test_reserve_rejects_bad_items` → 400 `invalid_items`.
  - `test_reserve_over_limit_is_409` (`limit_exceeded`).
  - `test_reserve_keeps_reference`: `shipping_details.reference` is saved.
  - `test_public_option_shows_bought_for_user`.
  - `test_admin_deliver_some_lines`: the `item_ids` subset → 200, those lines are delivered and the status stays.
  - `test_estimate_takes_items`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Update the existing tests that post `option_id`.
- [ ] **Step 4:** Run the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `[add] presale API takes several versions per order`.

### Task 5: Emails and reminders

**Files:**
- Modify: `apiApp/services/emailing.py`, `apiApp/templates/emails/presale_order.html`, `presale_store_notice.html`, `apiApp/management/commands/presale_reminders.py`, `apiApp/views/presales_admin.py` (`_arrive`)
- Test: `test_presales_emails.py`

**Interfaces:**
- **`_presale_email_context`:** gains `lines: [{name, quantity, image_url, arrived, delivered}]`, drops `option`, and `cover_url` = the first line's image or the cover. The `arrived` intro names the arrived lines (`'Llegó: Rojo ×2.'`, plus `' Falta: Negro ×1.'` when some are missing).
- **`_arrive`:** each affected order gets exactly one `arrived` email per call, even when two of its versions arrive in the same call.
- **Reminders command:** iterates holding orders with `svc.last_arrival(order)` set (filtered in Python after `prefetch_related('items__option')`).
- **Templates:**
  - the order email lists the lines: photo 56px, name ×qty, and a ✓ "entregado" or "en camino" chip;
  - the store notice lists the lines.

- [ ] **Step 1: Failing tests:**
  - `test_order_email_lists_lines`: the body has `'Rojo ×2'` and `'Negro ×1'`.
  - `test_arrived_email_names_what_is_missing`.
  - `test_reminders_wait_for_all_lines`: one line not arrived → no reminder.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `[add] presale emails list every line`.

### Task 6: Frontend types, service, shared address fields

**Files:**
- Modify:
  - `src/app/domain/presales.ts` (`PresaleOrder.items`, `PresaleOrderItem`, `PresaleOption.max_per_buyer` and `bought`, `ReserveInput.items`, `AdminOrderInput.items`, `ShippingAddress.reference?`, `PresaleOptionInput.max_per_buyer?`)
  - `src/app/services/presaleService.ts` (`estimateShipping(items, zip)`, `deliver(orderId, itemIds?)`)
  - `src/pages/cart/ShippingAddressFields.tsx`, `src/pages/cart/CartPage.tsx`, `src/pages/presales/ReserveForm.tsx`

**Interfaces:**
- `ShippingAddressFields` becomes a fields-only component. Its props are `{ values, errors, locations, locationsZip, onChange, idPrefix? }`, and it renders no `<form>`. Field order: Nombre, Teléfono, Código postal, Calle, Número, Colonia (a select when there are `locations`), Ciudad, Estado, Referencias (opcional). The tap targets are at least 44px.
- `CartPage` wraps it in its existing `<form ref={shippingFormRef} id="shipping-form" onSubmit=…>`.
- `ReserveForm` renders it inside its form. Its address state uses `ShippingAddressValues` (same keys plus `reference`), and it keeps its colonia effect.

- [ ] **Step 1:** Make the type and service changes. `npm run build` → the expected errors are in the presale pages only (fixed in Tasks 7–8).
- [ ] **Step 2:** Refactor the component and both callers.
- [ ] **Step 3: Check in the browser** with the scratchpad script:
  - the cart checkout still validates the address and shows colonias;
  - the reserve form shows Referencias;
  - postal code 15400 fills colonia, city and state in both.
- [ ] **Step 4:** Commit with Task 7.

### Task 7: Presale page — quantities per version

**Files:**
- Modify: `src/pages/presales/VersionPicker.tsx` (renamed in place to a quantity picker), `src/pages/presales/PresalePage.tsx`, `src/pages/presales/ReserveForm.tsx`

**Interfaces:**
- **Picker props:** `{ options, quantities: Record<number, number>, fallbackImage, onChange(optionId, qty), onFocusOption(option) }`.
- **Each card:**
  - the photo;
  - the name and price;
  - a − / qty / + stepper with 44px buttons, `aria-label` "Quitar una"/"Agregar una" + name;
  - max = `min(copies_left, max_per_buyer − bought)` (no limit → `copies_left`);
  - when it's capped by the limit, the hint "Máximo {n} por persona".
- The photo shown big is the last version changed.
- The page starts with `{firstAvailable.id: 1}`.
- **`ReserveForm` props:** `{ presale: Presale, quantities }`.
  - The amount choices use the sums: min, half and full as in the spec.
  - The summary lists the lines ("Rojo ×2 · $1,600.00").
  - The submit is disabled with "Elige al menos un disco." when the total quantity is 0.
  - The estimate is re-requested when the quantities or the zip change (key = sorted `id:qty` + zip).
- **Motion:** the stepper number changes with a 150ms scale; buttons press on click (`active:scale-95`); `motion-reduce` is respected.

- [ ] **Step 1:** Implement. `npm run build` and `npm run lint` → 0 errors.
- [ ] **Step 2: Check in the browser** at 390 and 1440:
  - set red 2, black 1 → the summary shows both lines and the amounts match the backend split;
  - the limit hint shows when capped;
  - reserving reaches Stripe's URL (the mocked API isn't needed: run the local backend with the test Stripe key from `.env`).
- [ ] **Step 3: Commit** (frontend) `[add] buy several copies and versions in one presale order`.

### Task 8: Admin, Mis órdenes

**Files:**
- Modify: `src/pages/admin/presales/PresaleForm.tsx` (each version gets "Máximo por persona (opcional)", empty → null), `RegisterPresaleOrder.tsx` (one quantity per version, same stepper as Task 7 minus the photos, no limit), `PresaleOrderPanel.tsx` and `DeliverByCode.tsx` (lines list; when pickup, checkboxes on arrived undelivered lines plus "Entregar seleccionados", using the service's `deliver(orderId, itemIds)`; the error text from the API), `PresaleDetail.tsx` (anything that read `order.option` reads `lines_label`), `src/pages/orders/PresaleOrderCard.tsx` (lists the lines with ✓ entregado / en camino)

- [ ] **Step 1:** Implement. `npm run build` and `npm run lint` → 0 errors.
- [ ] **Step 2: Check in the browser** with the scratchpad admin script:
  - create a presale with a limit;
  - register an order of 2 versions;
  - mark one version arrived;
  - deliver it by code once it's paid enough;
  - Mis órdenes shows the lines.
- [ ] **Step 3: Commit** `[add] admin and Mis órdenes show presale lines`.

### Task 9: Final review

- [ ] Run the backend suite, the frontend build and lint, and `makemigrations --check`.
- [ ] Do a whole-branch review of both repos' diffs against the spec and the Review Focus. Fix the Critical and Important findings test-first, then report.
