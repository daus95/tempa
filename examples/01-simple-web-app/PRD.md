# PRD — Mortgage (KPR) Installment Simulator (Web, Client-Side)

## 1. Purpose

Help prospective homebuyers simulate their monthly mortgage (KPR) installment based on
property price, down payment, interest rate, and loan term — complete with an amortization
table and a simple visualization — so they can compare a few scenarios before applying to
a bank.

**For whom:** individuals considering buying a home with a mortgage.

**Important constraint:** this is a **single-page, purely client-side web app**. **There is
no backend server, no REST/GraphQL API calls, and no database access of any kind.** All
calculations run in the browser (JavaScript), and all state is lost on page refresh (no
history is saved).

## 2. Business Process / Usage Flow

1. The user opens the web page.
2. The user fills in the input form:
   - **Property Price** (in currency)
   - **Down Payment** — can be entered as a percentage (%) or a direct amount, linked to
     each other (changing one field auto-updates the other)
   - **Annual Interest Rate** (%)
   - **Loan Term** (in years, e.g. 5–30 years)
   - **Calculation Method** — choice of: *Flat* or *Annuity/Effective*
3. The user clicks **"Calculate"**.
4. The system displays a **Summary**:
   - Loan Principal (Property Price − Down Payment)
   - Monthly Installment
   - Total Interest over the term
   - Total Payment (Principal + Interest)
5. The system displays an **Amortization Table** per month: month number, principal
   portion, interest portion, remaining principal — with scroll/pagination since it can run
   to hundreds of rows (30-year term = 360 rows).
6. The system displays a **simple chart** (e.g. a pie/donut of Total Principal vs Total
   Interest composition, and/or a line chart of remaining principal declining over time).
7. A **"Reset"** button clears the form back to empty/default.
8. (Optional, nice-to-have) An **"Export to CSV"** button for the amortization table — the
   file is generated & downloaded directly in the browser (not uploaded to any server).

Validation to handle:
- All numeric fields are required and must be positive.
- Down payment must not exceed the property price (100%).
- Loan term must be at least 1 year.
- Inline error messages under the offending field, not a generic alert/popup.

## 3. Data Model (browser state, not a database)

No entity is persisted — only a *view model* that lives for as long as the browser
tab/session stays open:

- **LoanInput**: `propertyPrice`, `downPaymentPercent`, `downPaymentAmount`,
  `interestRatePerYear`, `termYears`, `method` (`flat` | `annuity`)
- **LoanSummary** (derived): `loanPrincipal`, `monthlyInstallment`, `totalInterest`,
  `totalPayment`
- **AmortizationRow[]** (derived): `month`, `principalPortion`, `interestPortion`,
  `remainingPrincipal`

Formulas:
- **Flat:** interest per month is computed from the original principal, staying constant
  every month.
  `principal_installment = principal / (term_years * 12)`,
  `interest_installment = principal * (annual_rate / 12)` (constant every month)
- **Annuity/Effective:** the monthly installment is fixed, and the principal:interest
  composition shifts each month based on the remaining principal (standard annuity
  formula).

## 4. UI Concept

- Single-page layout, two columns on desktop (input form on the left, results on the
  right); stacked vertically (form on top, results below) on mobile.
- Summary results shown as several **cards** with large numbers and clear labels.
- The amortization table sits below the summary, with a sticky header while scrolling.
- The chart is displayed alongside/near the summary cards.
- Currency values formatted with thousands separators everywhere.
- Responsive — must remain comfortably usable on a phone screen.

## 5. Tech Stack

- **Frontend only** — HTML + CSS + JavaScript (vanilla, or one lightweight framework such
  as Vue/React if it helps, bundled as a static site).
- **No backend/server**, no database, no external API calls (including no fetching interest
  rates from any bank API — the rate is always manual user input).
- A charting library may be used (e.g. Chart.js) as long as it can be bundled locally (not a
  runtime dependency on a third-party server for application data).
- Deployed as a static site (can be opened directly from a file or hosted on any static
  hosting).

## 6. Non-Goals (Explicitly Out of Scope)

- No user login/accounts.
- No saving of simulation history across sessions/visits.
- No real-time interest-rate integration from banks.
- No multi-currency — single currency only.
- No tax/insurance/bank provision-fee calculations (outside the scope of installment
  simulation).

## 7. Acceptance Criteria (Examples)

1. Input: Property Price 500,000,000, Down Payment 20%, interest 8%/year, term 10 years,
   Flat method → Loan Principal = 400,000,000, principal installment/month = 3,333,333
   (rounded), fixed interest installment/month = 2,666,667 (rounded).
2. Switching the method to Annuity with the same inputs produces a fixed monthly
   installment (same amount every month), with the interest portion decreasing and the
   principal portion increasing over time.
3. Entering a down payment of 120% of the property price is rejected with an inline error
   message; the "Calculate" button does not process it.
4. The amortization table for a 10-year term produces exactly 120 rows, and the last row's
   `remainingPrincipal` is near 0 (within rounding tolerance).
