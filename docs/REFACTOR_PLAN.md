# Refactor Plan: Box Office Briefing App

**Status:** Approved, weekend execution (2026-06-20 / 2026-06-21)
**Audience:** One reader — the Binghamton Rumble Ponies box office manager. Reads cold, no guided walkthrough. Public deploy, so no personal names in the UI.
**Goal:** Reframe the existing analytics app so a busy stakeholder lands directly on a usable finding, can grab ammo for upcoming meetings, and can study peer teams in one click. Keep every existing page intact, moved behind a "Methodology & deeper analyses" group.

---

## Locked decisions

| # | Decision |
|---|---|
| 1 | 6 new pages: The Picture (landing), Saturdays, Sundays, The League is Shifting, About. Existing pages tucked under one new group. |
| 2 | No standalone Playbook page — "what to do" folds into each finding page. |
| 3 | Existing `utils/theme.py` palette. **No logos.** Not a sanctioned team product. Don't dress it as official. |
| 4 | Landing layout (a): headline + KPIs + finding cards above the fold; map below. |
| 5 | Don't name Molly anywhere in the UI. Personalization stays in the email. |
| 6 | Don't delete, rename, or move any existing page file. Restructure is `app.py`-only. |
| 7 | Don't touch the analytics pipeline, SQL, schema, or deploy config. |

---

## Nav structure

```
THE PICTURE                 ← new landing, default page
WHAT THE DATA SAYS
  ├─ Saturdays              ← lead finding
  ├─ Sundays                ← new finding
  └─ The League is Shifting ← context
ABOUT THIS REPORT           ← about page

METHODOLOGY & DEEPER ANALYSES
  ├─ Executive Overview     (existing)
  ├─ Attendance             (existing)
  ├─ Promotions             (existing)
  ├─ Weather                (existing)
  ├─ Opponents              (existing)
  ├─ Rehab Assignments      (existing)
  ├─ Scheduling             (existing)
  ├─ Promo Strategy         (existing)
  ├─ Team Report            (existing)
  ├─ Competitive Intel      (existing)
  ├─ Recommendations        (existing)
  ├─ Weekend Playbook       (existing)
  ├─ Peer Playbook          (existing)
  ├─ Hypothesis Lab         (existing)
  └─ Admin                  (existing)
```

Existing `Home.py` keeps its content (legacy flat-nav entry), but `app.py` default lands on the new `The Picture` page.

---

## Per-finding page template

Every finding page follows the same 6-section arc so the format is learned once:

1. **Headline** — single declarative sentence at the top
2. **What we see** — 1 chart + 1-line plain-English caption that names the number
3. **Why it matters** — short paragraph translating to seats/fans
4. **What's behind it** — 1–2 supporting charts (peers, time trend)
5. **What to do** — strategic vs tactical labeled. Diagnosis-led, not prescriptive.
6. **See also** — 2–3 links into Methodology pages for the curious reader

**No sidebar filter sprawl on finding pages.** Defaults are 2026 + Binghamton (team_id=505).

---

## The Picture (landing) — layout

**Above the fold:**
- Single sentence sub-headline: *"Four seasons of attendance, promotions, weather, and demographics on every MiLB team. Here's where the Rumble Ponies sit — and what the data says is behind it."*
- 3 KPI tiles:
  - **49% seats filled, 2026** (was 53% in 2023) — `capacity_utilization`
  - **−413 fans/wk Sat gap** (4 yrs running) — Fri − Sat
  - **#28 of 30 Double-A** in cap util — rank within sport_id=12
- 3 finding-card teasers (clickable):
  - **Saturdays** — Fri > Sat four years running
  - **Sundays** — 1,156 fans avg, lowest in 4 years
  - **The League is Shifting** — weekend premium compressed −7.7pp

**Below the fold:**
- Section header: *"Who else is out there"*
- Map of all 122 teams. **Two** color modes only: Capacity utilization, Momentum.
- Click a team → opens existing Team Report page.
- Side panel: *Top 5 Double-A by capacity utilization* (peer-study shortlist).
- Expander: *"How to read this map"* — one paragraph, plain English.

**Footer:**
- Data through `MAX(games.updated_at)`
- "About this report" link
- No logos, no team branding

---

## What CUTS from current Home.py for landing

- 6 color modes → 2 (capacity utilization, momentum)
- Demographic color modes (MSA pop, income, poverty) — moved to Methodology, not on landing
- Heavy sidebar filter set — collapsed
- Map as page-dominating focal point — demoted to "below the fold"

## What KEEPS from current Home.py

- The Plotly choropleth/scatter map and its team metadata
- 4-year season toggle (Historical / All / Current)
- Click-team → navigate behavior

---

## Visual / UX rules

- Plain English captions under every chart (*"each dot is one Saturday"*)
- One color story per page: RP in team color, peers muted
- No jargon visible to the reader: cap util → "% of seats filled", CF → "model estimate"
- No emojis, no animations, no big-number reveals
- Print-friendly — assume she screenshots panels into slides
- Caption must answer the question *"what should I be looking at?"*
- Each "What to do" recommendation labeled `STRATEGIC (next year)` or `TACTICAL (this season)`

---

## File map

New files:
```
streamlit_app/
  Picture.py                       ← new landing, default in app.py
  pages/findings/
    01_Saturdays.py
    02_Sundays.py
    03_The_League_Is_Shifting.py
  pages/About_This_Report.py
```

Modified files:
```
streamlit_app/app.py               ← nav regroup only, no file moves
```

Existing files untouched. The legacy `Home.py` keeps its map for the flat-nav entry point.

---

## Build order

| Block | Est. | What |
|---|---|---|
| 1 | 1 h | `app.py` nav regroup. Move existing pages under new Methodology group. Add placeholder new pages so nav renders. Smoke test. |
| 2 | 2–3 h | Build `Saturdays` page (hardest finding — get the template right first) |
| 3 | 1 h | Build `Sundays` page (clone template, swap evidence) |
| 4 | 1 h | Build `The League is Shifting` page (clone template, swap evidence) |
| 5 | 2 h | Build `The Picture` landing — KPI strip + finding cards + map (cut from Home.py) + top-5 panel |
| 6 | 1 h | Build `About This Report` page — who/why/how to read |
| 7 | 1 h | End-to-end read-through as the box office manager. Fix snags. |

Saturday: blocks 1–4. Sunday: blocks 5–7.

---

## What this refactor will NOT do

- Touch SQL, the DB schema, or any collector/enrichment script
- Modify deployment config
- Add new dependencies
- Delete or rename existing pages
- Change the analytics pipeline outputs

---

## Acceptance check (Sunday night)

1. Default landing on Streamlit Cloud is The Picture
2. Each finding page reads cold without sidebar interaction
3. Every existing page still reachable under Methodology
4. Map color toggle works for both modes; clicking a team opens Team Report
5. Footer data-freshness shows through 2026-06-20
6. No "Molly", logos, or branding in the UI
7. Mobile-tolerable (KPI tiles stack)
