# PRD — Mortgage Installment Simulator (Web, Client-Side)

## 1. Purpose

Let prospective homebuyers simulate a monthly mortgage installment from property price,
down payment, interest rate, and loan term, with an amortization table and a simple chart,
so they can compare scenarios before applying to a bank.

**Audience:** individuals considering a home mortgage.

**Constraint:** single-page, purely client-side app. No backend, no API calls, no database.
All calculation happens in the browser; state resets on page refresh.

## 2. Usage Flow & UI

1. User opens the page and fills in the form:
   - **Property Price** (currency)
   - **Down Payment** — percentage or amount, the two fields stay in sync. The percentage
     is entered as a whole number only (e.g. `12`); decimals such as `12,3` are not
     accepted.
   - **Annual Interest Rate** (%)
   - **Loan Term** (years, e.g. 1–30)
   - **Calculation Method** — *Flat* or *Annuity*
2. User clicks **Calculate**.
3. App shows a **Summary**: Loan Principal, Monthly Installment, Total Interest, Total
   Payment — as cards with large numbers and clear labels.
4. App shows an **Amortization Table** (one row per month: month #, principal portion,
   interest portion, remaining principal), scrollable/paginated with a sticky header — up
   to 360 rows for a 30-year term.
5. App shows a **chart** near the summary cards: principal-vs-interest composition
   (pie/donut) and/or remaining principal over time (line).
6. **Reset** clears the form.
7. *(Optional)* **Export to CSV** downloads the amortization table directly from the
   browser.

**Validation:**
- All numeric fields required and positive.
- Down payment ≤ 100% of property price.
- Loan term ≥ 1 year.
- Errors shown inline under the offending field (no popups).

**Layout:** two columns on desktop (form left, results right); stacked on mobile (form
then results). Currency values use thousands separators. Fully responsive down to phone
screens.

## 3. Tech Stack

- Frontend only: HTML/CSS/JS (vanilla, or one lightweight framework such as Vue/React),
  bundled as a static site.
- No backend, no database, no external API calls — the interest rate is always manual
  input, never fetched from a bank API.
- Charting library optional, must be bundled locally (no runtime dependency on a
  third-party server).
- Deployable as a static site (opened directly from a file or hosted anywhere static).

## 4. Out of Scope

- User login/accounts.
- Saved history across sessions.
- Real-time bank interest-rate integration.
- Multi-currency support.
- Tax, insurance, or bank provision-fee calculations.
