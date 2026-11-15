# Presales: several copies and versions per order

## Why

Today a presale order is one copy of one version. A buyer who wants 2 red and
3 black vinyls has to reserve five times, with five codes. The store wants one
checkout, one code, one balance, plus a limit per person on each version.

## Decided by the owner

- **One order, several lines.** An order holds lines (`version × quantity`) and keeps one
  code, one QR, one payment ledger and one balance.
- **Limit per version:** each version gets an optional "Máximo por persona". Empty means no limit.
- **Arrivals:**
  - Pickup orders can hand over the lines that already arrived, using the same QR.
  - Shipping orders ship once, when every line has arrived.
  - The payment deadline counts from the last arrival.
- **Address form:** the presale reuses the cart's address form, which has "Referencias (opcional)".
  Both put the postal code first: it fills the colonia list, city and state.

## Data

- **New `PreSaleOrderItem`:** `order` (related_name `items`), `option` (PROTECT, related_name `items`), `quantity` (≥ 1),
  `unit_price` (the version price when reserved), `unit_discount` (full-payment discount per copy,
  0 unless paid in full at reservation), `delivered_at`, `delivered_by`.
- **`PreSaleOrder`:**
  - loses `option`;
  - keeps `item_price`, now the sum of its lines after discounts, and `discount_applied`, the sum of line discounts.
  - Every money rule keeps reading `item_price`, so balance, payments, Ventas totals and the ledger don't change shape.
- **`PreSaleOption`:** gains `max_per_buyer` (nullable positive integer).
- **Migration 0059:**
  - adds both tables;
  - turns each existing order into one line (quantity 1, `unit_price = item_price + discount_applied`,
    `unit_discount = discount_applied`, `delivered_at` copied from the order when it was delivered);
  - then drops `order.option`.

  Presales aren't in production yet, so only local data migrates.

## Rules

- **Copies left:** `copies − Σ quantity` of the version's lines in orders that hold copies. An order holds
  copies when it is apartado, pagado, listo, enviado or entregado, or pending payment with a live hold.
- **Reservation amounts:**
  - minimum = Σ `min_deposit × qty`;
  - price = Σ `price × qty`;
  - full payment = Σ `(price − discount) × qty`.
  - Paying the full amount at reservation applies every line's discount (all or nothing, as today).
- **Limit:**
  - Online reservations check, per version: what the user already holds of it (lines in their
    holding orders) + the new quantity ≤ `max_per_buyer`.
  - Orders registered in Administración may exceed it *(my call: staff decide)*.
- **Arrival:** a version arriving marks its lines arrived. The order is "all arrived" when every line's version
  arrived. The deadline is `reservation + 120 days`, never before `last arrival + 15 days` (once all arrived),
  and never before the admin override.
- **Status:** derived as today, using "all arrived" where it used the single version's arrival.
  - `listo` = pickup, paid in full, all arrived.
  - `entregado` = every line delivered.
- **Partial pickup** *(my call)*: staff can hand over arrived, undelivered lines when what's paid covers the
  value of every delivered line plus the ones being handed over. The buyer never leaves with copies they
  haven't paid for. When the last line is delivered and the balance is 0, the order becomes `entregado`.
- **Shipping:**
  - The estimate and the final quote use one package for all lines: the weights of every copy, with box height
    growing per unit as today.
  - The final re-quote happens when the last line arrives.
  - `mark_shipped` requires all arrived.
- **Cancel, expire and reopen act on the whole order** *(my call; no per-line cancellation)*. Reopen checks copies
  for every line.
- **Ventas:** a paid order counts on the day it got paid. Revenue, cost and commission are reported per line
  (its version's owner and cost × quantity). Consignor emails go per line owner, once per order.

## API

- `POST /presales/reserve/` takes `items: [{option_id, quantity}]` (versions of one presale, quantity ≥ 1),
  plus today's fields. It also takes `shipping_details.reference`, which is optional.
  The error codes are:
  - `limit_exceeded`: names the version and how many more the buyer can take;
  - `sold_out`: names the version.
- `POST /presales/shipping-estimate/` takes `items` too.
- Admin register-order takes `items`. Admin deliver takes `item_ids` (the lines handed over). A new error code
  `paid_not_enough` explains how much is missing.
- **Order payloads:**
  - gain `items: [{id, option_id, option_name, image, quantity, unit_price, unit_discount, arrived, delivered_at}]`;
  - drop the single-option fields;
  - keep `presale` at the top level.
- **Presale payloads:** each option gains `max_per_buyer`, and, for a logged-in buyer, `bought` (what they already hold).

## Screens

- **Presale page:**
  - each version card gets a quantity stepper (0 to the lesser of copies left and limit − bought);
  - the reserve box sums the lines.
  - The page opens with 1 of the first available version, so a single-version presale reads as today.
- **Reserve form:** the shared address fields, with postal code first, colonias, and Referencias. The cart uses the same component.
- **Presale form (admin):** each version gets "Máximo por persona (opcional)".
- **Administración:**
  - the order panel and the deliver-by-code view list the lines;
  - delivery is checkboxes on arrived lines;
  - the register-order form takes lines.
- **Mis órdenes:** the card lists its lines.
- **Emails:** buyer emails list the lines. "Ya llegó" is sent when a version of the order arrives and says
  which lines arrived and which are still on the way. Store notices list the lines.

## Not doing

Per-line cancel or refund, split shipments, guest checkout, buying versions of different presales together.
