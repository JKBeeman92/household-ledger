# The check-in

Once a month (and, on the last month of a quarter or a year, a little longer) a household sits down with the ledger: sees where it
stands, decides what to adjust, and keeps the direction in view. **Check-in** in the top bar opens it for the latest month loaded
(`/checkin/YYYY-MM`). It is a short list of steps, each with a status: *to do*, *done*, *skipped* or *needs attention*. A step opens in
place; the page remembers where you are, so it can be done in one sitting or across a weekend. **Done** is enabled once no step is
to do; it records who pressed it and when, and tells your webhook (`checkin.completed`, [WEBHOOKS.md](WEBHOOKS.md)).

## A month

Set aside half an hour, with last month's statements to hand (or your bank's app).

1. **Files.** Export the month's two files from your budget tool and upload them here. The step is done once both are loaded and the
   ledger has rebuilt.
2. **Clean books.** Every envelope's transactions should add up to what the budget tool says was spent, and every item should have a
   mapping. Fix what you can (map a new item, re-export a month); a difference you have decided to live with can be acknowledged.
3. **Balances.** One figure per account: what it stood at on the last day of the month. Each field is filled in with a suggestion
   (last month's balance; for a debt, less the month's payment, plus interest at its APR). Saving a suggestion unchanged confirms it.
   The step appears once you have accounts (Settings → Accounts). Under each debt, its estimated payoff month at last month's
   payment and how it moved since last month's estimate (README, "Payoff estimates and coming up").
4. **Goals to confirm.** The standing figures the app asks about each month (gross pay, pre-tax saving, and any goal whose note says
   to ask). Confirm each as it is, or give the new value.
5. **Where we stand.** The month in a few lines: what was kept, net worth and how it moved (what you did, what happened to you), the
   emergency fund, debt-to-income. Read it together; opening it marks it read. The Last month tab has the detail.
6. **Coming up.** What falls in the next three months: estimated payoffs and life events already dated, and, as they arrive, the
   yearly bills and tax deadlines. Read it together; opening it marks it read.
7. **Review.** If a review provider is set up, have the month read back to you, and read it. Without one, the step is skipped.

Then **Done**. The Last month tab says "October check-in: done 2 Nov", or how many steps are left.

Coming in a later release, as its own steps: last month's decisions and up to three for next month (#155).

## Catching up

Skipped a month or two? The page says which months have no check-in ("September and October have no check-in") and offers
**Catch up**. Each earlier month gets a short form, files, clean books and balances, oldest first; the latest month gets the full
sit-down. For an earlier month, an account that had no balance yet is optional (it may not have existed then); enter past
statement balances under Settings → Accounts → Balances history if you have them.

## A quarter

On the last budget month of a quarter the check-in runs in **quarter** mode. The quarter's own steps (how the quarter went, estimated
tax) arrive with #147 and #153; until then a quarter's check-in is a month's. A good habit meanwhile: look at the Goals tab's lines
over the last three months, not just the last one.

## A year

On the year's last budget month (December, unless the `year_start_month` rule says your year starts elsewhere) it runs in **year**
mode. The yearly checklist and the year in review arrive with #157. Meanwhile: set the coming year's goals on Settings → Goals, and
make sure each account has a December balance, so next year's net worth has its year-over-year change.

## Reopening

A done check-in can be reopened (a late correction, a forgotten balance). Its first completion date is kept, and Done records it
again. A step added by a later release appears on check-ins not yet done, never on done ones.

From a shell: `python -m app.admin checkin status [YYYY-MM]` prints the steps and exits 0 when done, 1 while steps are left.
Completing stays in the browser: it is the household's sign-off.
