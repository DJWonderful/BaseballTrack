# Findings & Estimated Impact

A summary of every finding from the analysis, sized as honestly as I can size
them from public data alone. Read this as a follow-up to the briefing-book site.

---

## How to read this document

**The dollar numbers are estimates, not promises.** Three things to know up
front:

1. **One finding is modeled, the rest are observational.** The fireworks-swap
   finding came out of a trained counterfactual model (XGBoost S-learner)
   that controls for opponent, weather, day of week, and other slot quality
   factors. Every other finding is observational — meaning the lift is
   measured by comparing games with and without a promo, which is *always*
   exaggerated by selection bias (teams put promos on the nights they think
   will draw best). I've discounted observational lifts by 30–50% from
   their raw values to be honest.

2. **All revenue estimates use $30 per fan.** That's $16 ticket + ~$10
   concessions + ~$4 parking/merch — in the middle of the industry per-cap
   range for a Double-A team. If RP's actual per-cap is different (and it
   probably is, in one direction or the other), scale everything
   proportionally.

3. **Don't add the rows together blindly.** Some findings interact. For
   example, swapping fireworks to Saturday and stopping Saturday matinees
   both affect Saturday — they aren't fully additive. The "honest stack"
   section below addresses that.

Confidence labels:

- **HIGH** — measured directly or from a trained counterfactual model
- **MEDIUM** — observational lift, discounted for selection bias
- **LOW** — already partly programmed, limited headroom, or small sample
- **UNKNOWN** — diagnostic gap, needs more analysis before sizing

---

## The findings table

All figures use $30 per fan for the dollar columns.

| # | Finding | Per-game impact | Games / year affected | Fans / year | $ / year | Confidence |
|---|---|---|---|---|---|---|
| 1 | **Fireworks Fri → Sat (2027 calendar)** | +350 on Sat, −149 on Fri | ~12 home weekends | +2,000 to +2,400 net | **$60K–$72K** | HIGH |
| 2 | **Stop Saturday 1pm matinees** (2027 calendar) | +700 to +900 per matinee fixed | 3–4 matinees moved to evening | +2,000 to +3,000 | **$60K–$90K** | HIGH |
| 3 | **Move giveaways off Sat → Tue/Wed/Sun** | +150–250 on the new night | 6–8 weekday redeployments | +1,200 to +2,000 | **$36K–$60K** | MEDIUM |
| 4 | **Add theme nights on Wed** | +200–300 per Wed | ~10 home Wednesdays | +1,000 to +2,000 | **$30K–$60K** | MEDIUM |
| 5 | **Sustain kids events on Sun** | minimal (already programmed) | already on 92% of Sundays | +200 to +500 | **$6K–$15K** | LOW |
| 6 | **Sunday gap diagnosis** (Portland/Somerset peer study) | unsized | up to 13 home Sundays | unknown | **unsized** | UNKNOWN |
| 7 | **Group sales program** | unsized | unknown | unknown | **unsized** | OUT OF SCOPE for public data |

---

## What the per-game numbers mean

The "per-game impact" column is the lift expected on the *specific games the
change touches*, not the season-wide game average. Examples:

- Finding 1 affects only home weekend Fridays and Saturdays — so the +350
  Saturday number is on each of those ~12 Saturdays, not on all 70 home games.
- Finding 4 affects home Wednesdays — so +200–300 each Wednesday, not on
  Tuesday or weekend games.

This is the right unit for promo-card planning, but it's why you can't add
the per-game numbers to get a season total.

---

## The honest stack — what these add up to

If RP makes the changes that are reasonable to make:

**2027 calendar changes alone (Findings 1 + 2):**
- These both affect Saturdays. They're not fully additive because the
  matinee fix and the fireworks swap aren't completely independent — if a
  Saturday matinee gets moved to evening, fireworks help even more there.
- Honest combined estimate: **+3,000 to +4,500 fans / year, ~$90K–$135K
  direct revenue**.

**This-season tactical changes (Findings 3 + 4):**
- Mostly independent. Different days, different mechanisms.
- Honest combined estimate: **+2,000 to +3,500 fans / year, ~$60K–$105K
  direct revenue**.

**Sustained kids programming (Finding 5):**
- Already happening. The estimate is marginal upside only.
- Honest estimate: **+200–500 fans, ~$6K–$15K** — basically noise relative
  to the others.

**Honest grand total for the next 12–18 months:**
**~$150K–$240K of direct revenue, with most coming in the 2027 season
once the calendar fixes land.**

---

## What's not in those numbers (real money, just not measurable here)

- **Sponsor renewals.** Industry research consistently shows that higher
  attendance pressures up sponsor renewal pricing. The standard rule of
  thumb is 30–50% additional revenue uplift over a 2–3 year horizon. So
  the $150K–$240K figure above might be more like $200K–$350K all-in over
  a few years.
- **Season-ticket halo.** When the ballpark looks full on visible nights
  (Saturdays especially), next-year season-ticket package sales tend to
  rise. Real effect, hard to size from outside.
- **Schedule leverage.** Once RP has a documented record of "Saturday plays"
  numbers, the negotiation with the city for fireworks dates gets stronger.
- **Group sales programming.** Probably real, but the public data can't
  measure it. See Finding 7.

---

## What I'd want to do next to tighten these numbers

If any of this is useful and worth pursuing, here's where the analysis
would benefit most from sharper work:

1. **Run the counterfactual model for giveaways and theme nights**, the
   same way we ran it for fireworks. That would move Findings 3 and 4 from
   MEDIUM to HIGH confidence and replace the ranges with single defensible
   numbers.
2. **Diagnose the Sunday gap.** Portland Sea Dogs and Somerset Patriots
   run Sunday at ~95% capacity. RP is at 28%. Until we understand what
   they do differently, Finding 6 stays UNKNOWN.
3. **Internal ticketing data, if available, would unlock Finding 7.**
   Group sales are likely a real lever; we just can't see them from outside.

---

## Sanity check on the per-fan revenue assumption

The $30 per-cap figure is meant to be honest, not aggressive. Some context:

| Per-fan revenue line | Assumed | Industry range |
|---|---|---|
| Ticket | $16 | $12–$20 for Double-A |
| Concessions (F&B) | $10 | typically 50–70% of ticket price |
| Parking, merch, misc | $4 | varies widely |
| **Per-fan total** | **$30** | $22–$35 typical at Double-A |

If RP's true per-cap is closer to $22, scale all the dollar columns down
by 25%. If it's $35 (premium F&B operation), scale up about 15%. The
*ranking* of findings doesn't change with the per-cap assumption — only
the dollar magnitudes.

---

## Bottom line, plainly

The single biggest finding is the fireworks scheduling change, and it's
the only one with a real counterfactual model behind it. Everything else
is directionally useful but should be treated as ranges rather than
specific numbers until the next round of analysis is done.

If even half of the estimated upside is real, this analysis is worth
several times what the season-ticket office could spend on consulting work
to derive equivalent findings. If none of it lands, the cost was a few
weekends of an analyst's time. The downside is bounded; the upside isn't.

— Bill
