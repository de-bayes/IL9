# IL9Cast Archive Design Spec v2 (synchronized)

**Direction:** Formal statistical forecast archive — not editorial magazine, not startup landing page.
Think: Census bureau report, academic model documentation, official election data portal.

## DO NOT CHANGE
- Footer markup and styles (`_site_footer.html`, `.site-footer`, `.footer-content`, `.flag-footer-image`)
- Footer-related rules in `landing-style.css` (lines ~940–1050)

## Global tokens (all agents must use these exactly)

```css
/* Palette — muted, official */
--bg-primary: #141418;        /* dark default */
--bg-surface: #1C1C22;
--bg-elevated: #24242C;
--border-default: rgba(255,255,255,0.10);
--text-primary: #E8E8E4;
--text-secondary: #9A9A96;
--text-tertiary: #6E6E6A;
--accent-stat: #4A7C9B;      /* single cool accent for links/active — replaces gold/teal */
--accent-data: #C4C4BE;      /* for emphasis numbers only */
--font-sans: 'Source Sans 3', system-ui, sans-serif;
--font-mono: 'JetBrains Mono', 'SF Mono', monospace;
--radius: 0;                 /* square corners everywhere */
```

Light mode: `--bg-primary: #F4F4F0`, surfaces `#ECECE8`, text `#1A1A18`.

## Shared components (components.css — Agent 1 owns, others consume)

1. **`.archive-page-header`** — left-aligned, not centered hero
   - `.archive-page-header__kicker` — uppercase 0.7rem tracked label: "IL-09 Democratic Primary · Archive"
   - `.archive-page-header__title` — sans-serif 1.75rem weight 600, no serif
   - `.archive-page-header__meta` — mono 0.8rem grey: coverage dates, snapshot count

2. **`.stat-table`** — bordered grid for key metrics (replaces colorful stat-boxes)
   - Header row uppercase labels
   - Values in `font-mono` tabular-nums

3. **`.data-panel`** — flat bordered rectangle, no hover lift, no accent borders
   - `.data-panel__label` — section label uppercase tracked

4. **`.prose-block`** — body text max-width 68ch, 0.95rem, secondary color

5. **Remove/avoid globally:**
   - Gold accents, teal accents, serif headlines on data pages
   - `fadeIn`, `fadeInUp`, card arrows (→), emoji in buttons
   - Rounded photo cards, donation CTAs, "Support" inline promos (footer link OK)
   - Centered magazine-style heroes on data pages

## Page assignments

| Agent | Pages | Focus |
|-------|-------|-------|
| 1 | Global CSS + Landing | Tokens, header, banner, landing → tabular archive index |
| 2 | Markets | Chart section, leader card → results table, remove promos |
| 3 | Model (/odds) | Headline cards → forecast summary table, map chrome |
| 4 | Money + Candidates | FEC tables, candidate grid → directory listing |
| 5 | Methodology, About, 404, Updates, Case study | Documentation prose style |

## HTML patterns

Replace centered `<section class="hero">` with:
```html
<header class="archive-page-header">
  <p class="archive-page-header__kicker">…</p>
  <h1 class="archive-page-header__title">…</h1>
  <p class="archive-page-header__meta">Coverage: … · Last snapshot: …</p>
</header>
```

Replace stat-box grids with `<table class="stat-table">` or `<dl class="stat-dl">`.

## Typography rules
- Page titles: sans 600, not Libre Baskerville
- All percentages/probabilities: `font-family: var(--font-mono); font-variant-numeric: tabular-nums`
- Section labels: `font-size: 0.7rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-tertiary)`
