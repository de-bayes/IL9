#!/usr/bin/env python3
"""Apply UI + architecture optimizations (idempotent). Run from repo root."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def patch_file(path: Path, replacements: list[tuple[str, str]], label: str):
    t = path.read_text()
    n = 0
    for old, new in replacements:
        if old in t and new not in t:
            t = t.replace(old, new, 1)
            n += 1
    if n:
        path.write_text(t)
        print(f"  {path.name}: {n} patches")
    return n


def main():
    # --- performance.py immutable cache ---
    perf = ROOT / "performance.py"
    pt = perf.read_text()
    if "response.headers['Cache-Control']" not in pt:
        pt = pt.replace(
            "            response.headers.setdefault(\n"
            "                'Cache-Control', 'public, max-age=31536000, immutable'\n"
            "            )",
            "            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'",
            1,
        )
        perf.write_text(pt)
        print("  performance.py: immutable cache fix")

    # --- landing-style.css ---
    css = ROOT / "static/landing-style.css"
    ct = css.read_text()
    if "--page-gutter:" not in ct:
        ct = ct.replace(
            ":root {\n    /* Dark editorial palette",
            ":root {\n    --page-gutter: clamp(1rem, 4vw, 2.5rem);\n    --page-max: 820px;\n    --accent-color: var(--accent-gold);\n    --accent: var(--accent-blue);\n    /* Dark editorial palette",
            1,
        )
    if "html, body {" not in ct or "overflow-x: hidden" not in ct.split("html, body")[1][:80]:
        ct = ct.replace(
            "html {\n    scroll-behavior: smooth;\n}",
            "html {\n    scroll-behavior: smooth;\n}\n\nhtml, body {\n    overflow-x: hidden;\n}",
            1,
        )
    if ".text-link {" not in ct:
        ct += """

/* --- Shared utilities (reduce inline styles) --- */
.text-link {
    color: var(--accent-blue);
    text-decoration: underline;
    font-weight: inherit;
}
.text-link--strong { font-weight: 600; }
.section-footnote {
    margin-top: 1.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border-default);
    font-size: 0.85rem;
    color: var(--text-secondary);
    line-height: 1.5;
}
.section-footnote--center {
    text-align: center;
    font-size: 0.8rem;
    color: var(--text-tertiary);
}
.page-shell { margin-top: 1.5rem; }
.page-shell--center { text-align: center; }
.stat-value--numeric {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
    min-width: 5.5ch;
}
.content-visibility-auto {
    content-visibility: auto;
    contain-intrinsic-size: auto 400px;
}
.header-actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    order: 3;
    margin-left: auto;
}
.hamburger {
    min-width: 44px;
    min-height: 44px;
}
"""
    if "[data-theme=\"dark\"]" in ct and "Remove duplicate" not in ct:
        ct = re.sub(
            r"\n/\* Dark mode — explicit \(alias for default\) \*/\n\[data-theme=\"dark\"\] \{[^}]+\}\n",
            "\n",
            ct,
            count=1,
        )
    css.write_text(ct)
    print("  landing-style.css: utilities + tokens")

    # --- markets.html ---
    m = ROOT / "templates/markets.html"
    mt = m.read_text()
    mt = re.sub(r"\nasync function saveSnapshot\(candidates\) \{.*?\n\}\n", "\n", mt, count=1, flags=re.DOTALL)
    mt = re.sub(
        r"\n    // Save snapshot.*?saveSnapshot\(aggregated\);\n",
        "\n    // Archive: JSONL is read-only on the server.\n",
        mt,
        count=1,
        flags=re.DOTALL,
    )
    mt = re.sub(r"\nfunction getPollingInterval\(\) \{.*?\n\}\n", "\n", mt, count=1, flags=re.DOTALL)
    mt = re.sub(r"\nfunction updateStatusBadges\([^\)]*\) \{\}\n", "\n", mt)
    mt = re.sub(r"\s*updateStatusBadges\([^)]*\);\n", "\n", mt)
    if "chart.js@4.4.6" not in mt:
        mt = mt.replace(
            "https://cdn.jsdelivr.net/npm/chart.js",
            "https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js",
            1,
        )
        mt = mt.replace(
            "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns",
            "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js",
            1,
        )
    if " defer " not in mt and 'chart.js@4.4.6' in mt:
        mt = mt.replace("<script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.6", "<script defer src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.6", 1)
        mt = mt.replace(
            "<script src=\"https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0",
            "<script defer src=\"https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0",
            1,
        )
    if "response.status === 304" in mt and "hideChartLoading" not in mt:
        mt = mt.replace(
            "                return;\n            }\n\n            chartData = await response.json();",
            "                var el = document.getElementById('chartLoadingIndicator');\n"
            "                if (el) { el.style.opacity = '0'; setTimeout(function() { el.style.display = 'none'; }, 300); }\n"
            "                return;\n            }\n\n            chartData = await response.json();",
            1,
        )
    if "site.js" not in mt and "</body>" in mt:
        mt = mt.replace(
            "</body>",
            "    <script src=\"{{ url_for('static', filename='site.js') }}\"></script>\n</body>",
            1,
        )
    m.write_text(mt)
    print("  markets.html: cleanup + defer charts")

    # --- odds map ---
    o = ROOT / "templates/odds.html"
    ot = o.read_text()
    if "preferCanvas" not in ot and "L.map('precinct-map'" in ot:
        ot = ot.replace(
            "        var map = L.map('precinct-map', {",
            "        var canvasRenderer = L.canvas({ padding: 0.5 });\n        var map = L.map('precinct-map', {\n            preferCanvas: true,\n            renderer: canvasRenderer,",
            1,
        )
    if "debounce(renderFullTable" not in ot and "precinctSearch" in ot:
        ot = ot.replace(
            "        document.getElementById('precinctSearch').addEventListener('input', renderFullTable);",
            """        function debounce(fn, ms) {
            var t;
            return function() {
                var args = arguments, ctx = this;
                clearTimeout(t);
                t = setTimeout(function() { fn.apply(ctx, args); }, ms);
            };
        }
        document.getElementById('precinctSearch').addEventListener('input', debounce(renderFullTable, 200));""",
            1,
        )
    if "site.js" not in ot and "</body>" in ot:
        ot = ot.replace(
            "</body>",
            "    <script src=\"{{ url_for('static', filename='site.js') }}\"></script>\n</body>",
            1,
        )
    o.write_text(ot)
    print("  odds.html: canvas + debounce search")

    # site.js on more pages
    for name in ("landing_new.html", "about.html", "methodology.html", "fundraising.html", "updates.html"):
        p = ROOT / "templates" / name
        if not p.exists():
            continue
        t = p.read_text()
        if "site.js" in t or "toggleMobileMenu" not in t:
            continue
        if "</body>" in t:
            t = t.replace(
                "</body>",
                "    <script src=\"{{ url_for('static', filename='site.js') }}\"></script>\n</body>",
                1,
            )
            p.write_text(t)
            print(f"  {name}: site.js")

    print("done")


if __name__ == "__main__":
    main()
