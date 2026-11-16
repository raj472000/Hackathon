# Nifty 50 ticker: proof that it scrolls

Checked on 30 October 2026 against the live app:
`https://markets.greencliff-c30bacad.centralindia.azurecontainerapps.io/`

The browser engines are driven by Playwright 1.64: **WebKit 27.2** is the engine inside Safari, and **Chromium** is the engine inside Chrome and Edge.
The check records the ticker's position on every animation frame and reports how far it moved.

## Live results

| Run | Ticker code | Frames/s | Moved | Speed | Frames that didn't move | Stocks shown | Blank "—" values |
|---|---|---|---|---|---|---|---|
| WebKit, 1000 px, 10 s | new (transform) | 59.6 | 400 px | 40 px/s | 0 | 50 | 0 |
| WebKit, 390 px (phone), 75 s | new (transform) | 60 | 3000 px | 40 px/s | 0 | 50 | 0 |
| Chromium, 1000 px, 10 s | new (transform) | 59.8 | 400 px | 40 px/s | 0 | 50 | 0 |

- The 75-second run covers the 60-second data refresh and several loops of the list. The ticker never stopped, jumped back or reset.
- No page errors in any run.
- Screenshots `live-<engine>-<width>-t0.png` and `-t<seconds>.png` show the ticker at the start and end of each run.

## Why it got stuck, and the fix

The 10-second runs above never touched the ticker. `ticker_interaction_check.cjs` does: it clicks, taps and hovers, moves the pointer away, and then measures how far the ticker moves in 2 seconds (80 px means running, 0 means stuck).

The old pause rules were: pause on `mouseenter` or `focus`, and resume on `mouseleave` or `blur`. That left the ticker stuck in three cases:

1. **Touch screens** (iPhone, iPad, touch laptops) send `mouseenter` when you tap, but never `mouseleave`. One tap paused it for good.
2. **A click** on the strip or an arrow focuses it, and focus pauses it as well.
3. **Safari** doesn't send `pointerleave` while the page scrolls out from under a mouse that isn't moving.

| Live app, old code (WebKit) | px in 2 s |
|---|---|
| Untouched | 80 |
| After a tap on an arrow (touch) | **0 — stuck** |
| After a tap on the strip (touch) | **0 — stuck** |

The new rules hold the ticker only while a **mouse** pointer rests on it, while focus came from the **keyboard** (`:focus-visible`), or during a drag. Scrolling the page releases the mouse hold.

| New build (test fixture) | WebKit | Chromium |
|---|---|---|
| Untouched | 79.7 | 80.7 |
| After mouse hover and leave | 80.6 | 80.7 |
| After clicking the › arrow | 80.5 | 80.7 |
| After clicking the strip | 80.2 | 80.7 |
| Touch: after tapping the › arrow | 80 | 80 |
| Touch: after tapping the strip | 80 | 80.7 |
| Mouse resting on the ticker (pause wanted) | 0 | — |

## Earlier build check (test fixture, not live data)

`ticker_proof.cjs` runs the local `frontend/dist` build with a labelled test fixture, which includes one stock with no price. It confirmed the following:

- The unpriced stock is left out.
- Hovering pauses the ticker.
- The › arrow moves it 320 px.
- Both arrows fit at 1000 px and at 390 px.

Its screenshots are `webkit-*.png` and `chromium-*.png`.

## Re-run it

```bash
cd frontend && npx playwright install webkit chromium   # once
cd ..
node new_analysis/ticker-proof/ticker_live_check.cjs "$PWD/frontend" <app url> webkit 1000 10 new_analysis/ticker-proof
```

**Real Safari:** a device with **Reduce motion** turned on (macOS Accessibility settings, or *Animation effects* off in Windows) keeps the ticker still on purpose, for accessibility. You can still move it with the ‹ › arrows or by swiping.
