# Preventas (presales) — design

Date: 2026-11-10 · Repos: backend `bemoctezuma_records`, frontend `femoctezuma-records`
Branch in both: `feature/presales` from `origin/main`.

## Why

The owner runs presales of upcoming records by hand (Instagram, the store) and loses
control: who reserved, who paid how much, who paid 100%, what shipping each one owes.
This feature is the single source of truth for every presale and every buyer's money,
and it lets several presales run at once.

**Success:** for any presale the owner can see, without remembering anything, each
buyer's paid / owed / shipping / status, and a record is only handed over against a
fully paid presale order.

**Anti-goals:** money that doesn't add up (cents bugs — see migration 0034); a record
handed over twice or while money is owed; spam (unconfirmed emails, duplicate
announcements); anything charged, deleted or forfeited automatically without the owner
seeing it.

## Concepts

### Presale (created in Administración)
- Cover image (uploaded, `ImageField` like `Bazar.image`), artist, title, description.
- `purchase_deadline` (store-day date; after it no new reservations),
  `estimated_arrival` (optional, shown as "Llega aprox.").
- **One or more options (variants)** — owner: a presale can offer several versions of the
  same record (colors, formats, a new edition with a couple of records). Each option:
  - `name` ("Vinilo rojo", "CD", "Edición 2LP"), optional own image (falls back to the
    presale cover), `category` (Format FK, reused for shipping weight like
    `record_unit_weight_grams`), optional `weight_grams` (box sets / 2LP).
  - `price`, `min_deposit`, `full_payment_discount` (fixed MXN amount, may be 0). All
    `DecimalField(10,2)`. Validation: `0 < min_deposit < price`, `0 <= discount < price`.
  - `copies` (total available for that option); "Agotado" is per option.
  - `arrived_at` — options can arrive separately (my call); "Llegó a tienda" is pressed
    per option, with a shortcut for all options at once.
  - Order of display set by the admin.
- A presale with one option shows no selector; the presale card shows "Desde $X" when
  options have different prices.
- An option can't be deleted once it has orders (it can be hidden from new reservations).
- Slug (auto, same pattern as other slugged models), shown at `/preventas/<slug>`.
- Status: `draft` (only admins see it) → `published` → `arrived` (record reached the
  store) → `closed`. "Agotado" and "deadline passed" are computed, not statuses.
- `announced_at`: set when the newsletter email is sent; a presale is announced once.

### Presale order (one copy per order)
- Belongs to a presale **option** (the customer picks which one). Buyer is either a site `User` (online) or a walk-in /
  Instagram buyer registered by an admin: name, email (optional), phone (optional).
  If an admin-registered order has an email and that person later signs up with the
  same email, the order is linked to their account (on account creation / verification).
- `code`: short unique human code like `PV-7K3Q` (no ambiguous chars). Shown as text and QR.
- Delivery: `pickup` (store) or `shipping` (address + zip snapshot, Envíos Perros
  quote snapshot: estimated cost at reservation, final cost charged on settlement,
  courier/service/tracking link once shipped).
- `terms_accepted_at` (+ terms version string) — required for online orders; for
  admin-registered ones the admin ticks "El cliente aceptó las condiciones".
- Money: `total` = presale price snapshot (minus discount if paid in full at once) +
  shipping once known. `paid` = sum of its payments. `balance` = total − paid.
  Prices are snapshotted on the order; editing the presale doesn't change existing orders.
- `reserved_at`; `deposit_deadline` = the later of `reserved_at + 120 days` and
  `option.arrived_at + 15 days` (owner: the deadline never ends before the record
  arrives; 15 days after arrival is my call). Not arrived yet → no forfeit possible.
  Stored and admin-editable per order and in bulk per presale (extensions), recomputed
  when the option arrives.
- Statuses (Spanish labels are the owner's):
  - `apartado` — Apartado (paid < total)
  - `pagado` — Pagado (balance 0, record not arrived)
  - `esperando` — Esperando que llegue a tienda *(see open note below)*
  - `listo` — Listo para recoger (arrived, pickup, balance 0)
  - `enviado` — Enviado (shipping, with tracking)
  - `entregado` — Entregado (handed over; who and when recorded)
  - `vencido` — Apartado vencido (120 days passed with balance > 0; deposit forfeited,
    copy released). Only applied by the owner from a "Vencidos" list, never silently;
    the list highlights candidates.
  - `cancelado` — cancelled by admin (copy released).

  *Builder's note:* `pagado` vs `esperando` overlap; treat "Esperando que llegue a
  tienda" as the label shown for paid orders whose presale hasn't arrived, and keep
  `pagado` as the state name if a single state is simpler. Owner's labels must all appear.

### Payment (ledger line)
Every payment is its own row: order, amount, method (`stripe` / `cash` / `card` /
`transfer`), note (e.g. transfer reference), who registered it (null for Stripe),
Stripe session id (unique, for idempotency), created_at. The order's paid amount is
always derived from these rows. Status moves to paid automatically when balance hits 0.

## Money rules
- Reserving online: the customer chooses the amount: Mínimo / La mitad / Otra cantidad,
  from `min_deposit` up to the full price. If they pay ≥ (price − discount) at reservation,
  it is a full payment: the discount applies and the amount is exactly price − discount.
- Discount only applies when 100% is paid at reservation. Later settlement gets no discount.
- **Shipping (option B):** at reservation show "Envío estimado: $X · se paga al liquidar"
  (live Envíos Perros quote for one record to their zip). Shipping is charged with the last
  payment. When a full payment is made at reservation, shipping is charged then.
  When the presale arrives, shipping is re-quoted for every unpaid shipping order and the
  new amount is what they pay. (Final quote uses the existing `services/shipping.py`.)
- Online settlement: one "Liquidar" button pays the whole remaining balance (+ shipping)
  via Stripe. No partial online payments after the reservation.
- In-store: admin "Registrar pago" with any amount ≤ balance, method cash / card /
  transfer, optional note. Card commission is NOT applied here (my call; the POS
  commission logic stays in Punto de venta).
- Forfeit: after 120 days with balance > 0 the deposit is lost (owner applies `vencido`).
  No refunds flow in v1; cancellations are manual (admin cancels, refunds outside the site).
- Copies (per option): an order holds a copy while `apartado`/`pagado`/`listo`/`enviado`/`entregado`.
  `vencido` and `cancelado` release it. Reservation is rejected when no copies remain
  (check inside a transaction with a row lock to avoid overselling).

## Corrections (my calls)
- "Fully paid" includes shipping. A shipping order whose record price is paid before
  arrival shows "Pagado · falta envío" until shipping is quoted and paid.
- 100% paid at reservation charges the live shipping quote then; that amount is final.
- An amount equal to the full price counts as full payment (discount + live shipping).
- Payments are never edited or deleted: an admin voids a wrong one (kept, crossed out,
  with reason and who voided it). Delivery method can change before the final payment.
  Cancelled / expired orders can be reopened if a copy of that option is free.
- Status history (who / when) is kept per order.

## Reporting (owner: a presale counts in Ventas once it is fully paid)
- Each option also has `cost_price` (Decimal, optional) and `owner` (Owner FK, null =
  store stock), like records, so profit and the owner split work.
- A presale order enters Ventas on the store day it becomes fully paid (`paid_at`,
  cleared if a void makes it owe money again). Delivered/shipped doesn't matter.
- `services/sales.py` gets a third channel `presale` ("Preventas") next to `pos` and
  `online`: gross = order total without shipping (shipping is pass-through, like online
  orders — builder: match how online orders treat shipping), cost = option cost_price,
  payment methods counted per ledger line (Stripe lines as `stripe`, store lines by
  method), timeline and previous-period comparison included, owner split included.
- The sales CSV gets presale rows (folio = presale code, "Lugar de venta" = Preventa).
- Consignor owners get the existing owner-sale email when their presale order is paid.
- Presales don't create `Sale`/`SaleItem` rows (my call: aggregated directly, like
  online `Order`s, so voids and reopenings stay consistent).
- Frontend Ventas dashboard shows the new channel wherever pos/online appear.

## Stripe
Reuse the existing Stripe setup and webhook. Checkout sessions carry metadata
`kind=presale`, `presale_order_id` and the payment purpose (`reserve` / `settle`).
The webhook and the success-page completion both fulfil idempotently (unique session id
on the payment row) — same resilience pattern as cart checkout. The order row is
created before redirecting (status pending-payment, holding the copy for a short window,
e.g. 30 min, then released if unpaid) — builder decides the exact hold mechanism; it must
not oversell and must not keep copies held forever.

## Handing over the record ("only with the pre-order")
- Order code + QR (QR encodes a site URL to the admin lookup for that code; rendering the
  QR client-side needs a small library — ask the owner before adding the npm package, or
  render via a dependency-free approach).
- Code + QR in Mis órdenes (order detail) and in every presale email.
- Admin "Entregar preventa": scan or type the code → order card with name, record, cover,
  and a big state: green "Pagado completo · Entregar" or red "Falta $600" (with
  "Registrar pago" right there). "Entregado" is only possible at balance 0 and once;
  a second lookup shows "Ya entregado el 12 nov por <admin>".
- Shipping orders: marking Enviado (with tracking) is the delivery record.

## Presale arrival
Admin presses "Llegó a tienda" on an option (or all): option `arrived_at` set (presale → `arrived` when all have); paid pickup orders →
Listo para recoger; unpaid shipping orders get their final shipping quote; every buyer
gets an email: "Ya llegó: liquida $X" or "Ya está pagado: ven por él / lo enviamos".

## Newsletter (presale announcements)
- Anyone with an email can subscribe: form in the home Preventas section and the footer;
  logged-in users also get a toggle in Mi perfil.
- Double opt-in: confirmation email with a link; only confirmed subscribers are emailed.
- Every email has a one-click unsubscribe link (signed token, no login).
- Publishing a presale shows "Se enviará a N suscriptores" and requires confirmation;
  sent once per presale (`announced_at`). Sent via the existing `send_email` / Resend.
  Sending to many recipients should be one email per recipient (no exposed lists) and
  must not time out the request (builder: batch or background thread; there is no task
  queue in this project).
- Throttle the public subscribe endpoint; honeypot like the record-request form.
- Respect the cookie rule: no optional storage used for any of this.

## Emails
Reservation confirmed (amount paid, balance, deadline date, code + QR), payment
registered, presale arrived, reminder before the 120-day deadline (e.g. at 30 and 7 days
left — builder: needs a periodic trigger; a management command run by Railway cron is
acceptable, document it), shipped, delivered, newsletter confirmation, new presale
announcement. Store notification on every new reservation (SELLER_NOTIFY_EMAILS).

## Administración
New tab `presales` "Preventas" in `admin_panel.TABS` with permissions: tab, create/edit
presales, register payments & admin orders, deliver. Inside:
- Presale list (status, copies used, collected, owed) and create/edit form.
- Presale detail: totals (apartados, pagados completos, cobrado, por cobrar, vencidos),
  buyer list with filters (Debe dinero · Listo para entregar · Vencido / por vencer),
  each row: name, contact, deposit/full, paid / total, shipping, status, days left.
- "Registrar preventa" for Instagram / store buyers (name, email?, phone?, amount
  paid now + method, delivery, terms ticked).
- Order detail: payment ledger, register payment, status changes, tracking, deliver.
- "Entregar preventa" code lookup (also reachable from the tab top).

## Customer site
- **Home:** "Preventas" section near the top listing every published presale (cover,
  artist/title, price, "Quedan 4 de 20", countdown to deadline, "Paga completo y ahorra
  $50"), plus the subscribe box. Hidden when there are no open presales (subscribe box can
  remain in the footer).
- **`/preventas/<slug>`:** presale page with reservation flow: amount chooser, delivery
  choice (pickup / shipping with zip → estimated shipping), the mandatory checkbox
  ("Acepto que si no liquido en 120 días pierdo mi apartado", link to Términos), summary
  "Hoy pagas $X · Te faltarán $Y + envío · Tienes hasta <fecha>", then Stripe.
  Not logged in → login/signup and back to the same presale.
- **Mis órdenes:** presale orders alongside normal orders, each with progress
  (Apartado → Pagado → Esperando → Listo/Enviado), paid/total, countdown, Liquidar button,
  payment history, code + QR.
- **Términos y condiciones:** new section "Preventas y apartados" (120-day rule, deposit
  forfeit, discount only for full payment at reservation, shipping paid on settlement and
  quoted at arrival, delivery only against the presale code, one copy per order).

## Design
Use the installed Taste Skill (`design-taste-frontend`) inside the existing visual system
(Tailwind tokens, Spanish copy, no raw hex, repository pattern for services). Extreme
care on responsive (320 → 1440), motion (respect reduced motion), and states (loading,
empty, sold out, deadline passed, error). Admin screens: quiet panels, aligned figures.

## Constraints
- AGENTS.md: one app, domain modules re-exported from package `__init__`, explicit
  `related_name`, `settings.AUTH_USER_MODEL`, Decimal money, `timezone.localdate()`,
  approve migrations / git before running them, no new packages without asking.
- Tests hermetic (mock Stripe, Envíos Perros, email failures).
- Frontend checks: `npm run build`, `npm run lint`.
- Test on the local prod copy (made 2026-11-10).

## Non-goals (v1)
Refunds through Stripe; partial online payments after reservation; several copies per
order; automatic forfeits; SMS/WhatsApp notifications.

## Owner decisions (verbatim intent)
120 days from reservation then deposit lost, but never before the record arrives; mandatory checkbox + Terms; several
presales at once; balance paid online or in store (transfer, cash, card); shipping
option B; anyone can subscribe, account needed to buy; deposit can exceed the minimum;
admin can register Instagram / store presale buyers; presales have options (colors, formats, editions) and the buyer picks one; a presale counts in Ventas (with metrics) once fully paid.

## Calls made by Claude (owner can veto)
Fixed-amount discount; one copy per order; double opt-in; announce once; online
settlement pays everything left; in-store partial payments allowed; no card commission
on presale payments; QR + short code; reminder emails at 30 and 7 days; `vencido`
applied by the owner, not automatically; admin orders linked to accounts by email. Options arrive separately; deadline = max(reserved + 120 days, arrival + 15 days), extendable by the admin; paid-in-full shipping is final; payments voided, never deleted; per-option price, deposit, discount and copies.
