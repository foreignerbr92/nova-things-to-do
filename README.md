# NOVA Things To Do — v4.1 Live

A free static web app + scheduled event collector for discovering things to do within a **maximum ~2-hour drive of Fairfax, Virginia**.

## What changed in v4
- Drive-time filter: 30 / 60 / 90 / 120 minutes from central Fairfax.
- Expanded categories: theatre, comedy, music, park events, festivals, experiences, arts/markets, cultural events.
- Expanded discovery area: NOVA + DC + nearby Maryland + Warrenton/Fredericksburg + Winchester/Shenandoah-side destinations that fit the planning window.
- Automatic adapters now attempt Fairfax County Parks, ArtsFairfax, and Winchester-Frederick CVB daily.
- Priority source registry includes Wolf Trap, NextStop, Workhouse, Signature, GMU CFA, Hylton, 1st Stage, State Theatre, Arlington Drafthouse and NOVA Parks for continued adapter expansion.
- Price display rule: FREE, exact price, Starting at $X, and only then Check price.

## Important limitation
Drive times are **planning estimates, not live traffic**. This keeps v4 free and avoids requiring a paid routing API/key. Event pages remain the authority for price, availability, date and cancellation status.

## Deploy free on GitHub Pages
1. Upload the *contents* of this folder to a public GitHub repository.
2. Settings → Pages → Deploy from a branch → `main` / root.
3. Actions → enable workflows if GitHub asks.
4. Run **Refresh NOVA events** once manually to test. It also runs daily.

The workflow writes refreshed results to `data/events.json`; the browser loads that file on every visit.


## Local preview

Open `index.html` directly in a browser. Version 4.1 embeds a fallback event snapshot, so filters and Surprise Me work under `file://`. When hosted (for example on GitHub Pages), it automatically prefers `data/events.json`; if that feed fails, it falls back to the embedded snapshot.
