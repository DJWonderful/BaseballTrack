You are helping design a data pipeline for analyzing Minor League Baseball attendance and promotions.

Your FIRST task is NOT to write code. Your job is to research and map available data sources on the public web. You may write code to fulfil this first task.

---

## 🎯 OBJECTIVE

Identify reliable, repeatable sources of the following data for Minor League Baseball (MiLB) teams:

1. Game-level attendance
2. Game promotions (fireworks, giveaways, theme nights, etc.)
3. Game schedules (dates, opponents, home/away)

We are especially interested in teams like the Binghamton Rumble Ponies, but want to scale across multiple MiLB teams.

---

## 🔍 TASK 1 — FIND DATA SOURCES

Search and identify:

A. Websites that contain GAME RECAPS or BOX SCORES with attendance. If any databases or APIs exist, prioritise those.

* Official MiLB team websites
* MiLB.com
* Local news sites
* Sports aggregators

B. Websites that list PROMOTIONS

* Team promotional calendars
* Schedule pages with theme nights
* Social media (note but deprioritize if hard to scrape)

C. Structured or semi-structured data sources

* APIs (if any exist)
* Consistent HTML patterns across teams
* Downloadable schedules or feeds

For EACH source you find, provide:

* URL
* Type of data available (attendance, promotions, schedule)
* Structure (HTML page, API, PDF, etc.)
* Consistency (always present, sometimes missing, rare)
* Difficulty to extract (easy / medium / hard)
* Example snippet (if possible)

---

## 📊 TASK 2 — IDENTIFY PATTERNS

Across multiple teams, identify:

* Do MiLB teams follow a common website structure?
* Is attendance consistently reported? Where?
* Are promotions standardized or inconsistent?
* Which teams appear to have the BEST data availability?

Group findings into:

* High reliability sources
* Medium reliability
* Low reliability / inconsistent

---

## ⚠️ TASK 3 — IDENTIFY GAPS

Explain what data is NOT reliably available:

* Which teams do NOT publish attendance?
* Where data is missing or inconsistent
* Any reliance on manual sources (e.g., Twitter, PDFs)

---

## 🧠 TASK 4 — HIGH LEVEL STRATEGY (NO CODE)

Based on your findings, propose a HIGH-LEVEL data collection strategy:

* Which sources should we prioritize?
* What should be the “primary source” vs “fallback source”?
* Should we start with a subset of teams? If so, which ones and why?
* What is the minimum viable dataset we can realistically build?

DO NOT WRITE CODE.

---

## 📦 OUTPUT FORMAT

Respond in structured sections:

1. Data Sources (table format preferred)
2. Patterns Across Teams
3. Data Gaps / Challenges
4. Recommended Strategy

Be concise but specific. Focus on practical, usable insights—not theory.

---

Important:

* Prioritize sources that can scale across MANY teams
* Avoid one-off or manual-only sources unless necessary
* Think like a data engineer building a repeatable pipeline

---

## END
