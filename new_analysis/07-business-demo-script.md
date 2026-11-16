# Business Demo Script — Markets Intelligence

**Length:** about 10 minutes, plus 5 minutes of questions
**Audience:** business stakeholders, not engineers
**Goal:** show that the product saves research time, explains its reasoning, and can be trusted because every number has a source

> Tip: open every page in a browser tab before you start. Have one stock ready (for example HDFCBANK) and one theme ready (for example "Quality and compounders").

---

## 0. Before you start (checklist)

- [ ] App is running and **Data status** shows NSE MCP as healthy
- [ ] Kite / Tickertape logged in, if you plan to show fallback data
- [ ] LLM status is green, so the Research board and Concall analyzer work
- [ ] The latest RRG snapshot is less than 12 hours old
- [ ] You know which numbers on screen are live and which are stored. **Never read out a number you haven't checked on screen.**

---

## 1. Opening: the problem (1 min)

> "An analyst covering Indian equities spends hours every week pulling prices from NSE, reading results filings, comparing ratios across peers and listening to con-calls. That data sits in a dozen places, and most screening tools give you a number without saying where it came from.
>
> Markets Intelligence brings all of it into one place and explains every score it shows. It's a research tool: it does not place trades or give personal advice."

**Key point to land:** *faster research, and you can trust it because nothing is a black box.*

---

## 2. Overview: the morning view (1 min)

**Show:** Overview page

> "This is what a user sees first thing in the morning: index levels, which sectors are moving, which stocks lead each theme, and their watchlist.
>
> Look at the timestamps and the freshness badges. Every card says when its data was fetched and where it came from."

**Then try Ask the market.** Type a plain question, such as *"strongest stocks in the last 3 days"*.

> "No filters, no query language. You ask in plain English and the answer is built from live NSE data. When it falls back to stored data, it says so."

---

## 3. Screener and Themes: finding ideas (2 min)

**Show:** Screener

> "This covers the whole NSE equity universe. Filter, sort, save the screen, and export to CSV with the source and as-of time on every row."

**Show:** Themes. Open a built-in theme.

> "Themes are ready-made investment ideas: momentum, quality, value, income, growth, low risk and more. Each one shows its rules, its current members, and why each stock qualifies.
>
> Membership refreshes every month, and old snapshots are never overwritten, so you can always see what the list looked like last month."

**Show:** create a custom theme in natural language, for example:
*"NSE companies with improving revenue and ROCE, low debt and moderate volatility."*

> "The AI turns that sentence into filters, and the user **reviews and confirms them before anything is saved**. The AI never runs queries on its own."

---

## 4. Instrument detail and the five-pillar score (2 min)

**Show:** open one stock

> "Everything about one company on one page: price chart, quarterly and annual financials, valuation, technicals, risk and peers."

**Point at the score:**

> "The overall score combines five pillars at **20% each**: fundamentals, valuation, price action, risk and market context. Each stock is ranked against its own peers, so a bank is compared with banks, not with steel companies.
>
> If data is missing, we **don't count it as zero**. We leave it out and show the coverage, so you know how complete the picture is."

**Click a metric to show its formula and source.**

> "Any number can be traced back to its formula, its inputs, and the NSE filing or MCP call it came from."

---

## 5. Research board and Entry setup: the AI analyst (1.5 min)

**Show:** Research board for the same stock

> "This is where the AI comes in. It reads a bundle of retrieved evidence (prices, financials, filings, news) and writes pros, cons, catalysts and risks.
>
> Every claim has a citation, and the system **checks that each citation points to a source it actually retrieved**. If the AI makes up a source, the answer is rejected."

**Point at Entry setup:**

> "Instead of a 'buy' button, we show the conditions: Favorable, Watch, Unfavorable or Insufficient data. It lists every rule that passed or failed and what would invalidate the thesis. It's a checklist, not a tip."

---

## 6. Concall analyzer: the differentiator (1.5 min)

**Show:** Concalls page for a company with two consecutive transcripts

> "This is the feature analysts ask about most. Management makes promises on every earnings call: new capacity, margin targets, launches.
>
> The analyzer reads last quarter's transcript, pulls out those commitments, checks them against this quarter's call and the news, and gives a **follow-through score**.
>
> Every quote shown must appear **word for word** in the transcript, so you can verify it yourself."

**Key point to land:** *it answers "does this management deliver what it says?" in minutes instead of hours.*

---

## 7. Sector rotation and Market pulse (1 min)

**Show:** Sector rotation (RRG)

> "This chart shows money moving between sectors against the NIFTY benchmark. Sectors move through four stages: Leading, Weakening, Lagging, Improving. Press play to watch the trails. It refreshes every 12 hours."

**Quickly flip through:** News · Macro · FII/DII flows · F&O.

> "All the context a desk needs in one place: announcements, crude and currency, institutional flows and derivatives open interest."

---

## 8. Trust and operations (30 sec)

**Show:** Data status page

> "Here is the health of every data source: latency, last success, errors. NSE is the primary source and Zerodha Kite fills gaps. When they disagree, NSE wins and the conflict is shown to the user.
>
> The system is **read-only by design**. Order and account tools are blocked in code."

---

## 9. Close (30 sec)

> "To sum up:
> 1. **One place** for prices, fundamentals, themes, sectors, news and con-calls.
> 2. **Explainable**: every score shows its formula, sources and timestamp.
> 3. **AI that is checked**: citations are verified and quotes are verbatim, and the AI never does the maths.
> 4. **Ready to deploy**: Docker, CI/CD and Azure Container Apps.
>
> What would you like to see in more detail?"

---

## Likely questions and suggested answers

| Question | Answer |
|---|---|
| *Is this investment advice?* | No. It's a research and screening tool. Scores are research classifications, not recommendations or predictions. |
| *Where does the data come from?* | NSE MCP first, then Zerodha Kite MCP for gaps, and Tickertape for missing fundamentals. Every value shows its source. |
| *What if a data source goes down?* | The app shows a clear "degraded" or "stale" state and keeps the last good snapshot. It never invents data. |
| *Can the AI hallucinate numbers?* | All calculations are deterministic code with unit tests. The AI only summarises evidence it was given, and its citations and quotes are validated. |
| *Can it place trades?* | No. Trading tools are blocked in code. |
| *Can it expand beyond India?* | Yes. Data providers sit behind interfaces, so other exchanges can be added as new adapters. |
| *How much does the AI cost to run?* | LLM calls only happen on user request, with token tracking and a configurable daily budget cap. |

---

## Things to avoid saying

- "Guaranteed", "will go up", "best stock to buy"
- Any price, ratio or score you haven't just read off the screen
- "Real-time", unless the badge on screen says so (most NSE data is delayed or end-of-day)
