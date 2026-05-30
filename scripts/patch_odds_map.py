#!/usr/bin/env python3
"""Apply precinct map UI/UX enhancements to templates/odds.html."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "templates" / "odds.html"
t = p.read_text()

# --- HTML ---
if "layer-pill" not in t:
    old_html = """                    <div class="layer-control" id="layerControl">
                        <div class="layer-control-label">Map Layer</div>
                        <label><input type="radio" name="layer" value="winner" checked> Winner</label>
                        <label><input type="radio" name="layer" value="margin"> Margin</label>
                        <label><input type="radio" name="layer" value="competitive"> Competitiveness</label>
                        <label><input type="radio" name="layer" value="biss"> Biss Heat</label>
                        <label><input type="radio" name="layer" value="fine"> Fine Heat</label>
                        <label><input type="radio" name="layer" value="abughazaleh"> Abughazaleh Heat</label>
                    </div>
                </div>
                <div class="map-container">
                    <div id="precinct-map"></div>"""
    new_html = """                    <div class="layer-control" id="layerControl">
                        <div class="layer-control-label">Map layer</div>
                        <div class="layer-pills" role="tablist" aria-label="Map layer">
                            <button type="button" class="layer-pill active" data-layer="winner" role="tab" aria-selected="true">Winner</button>
                            <button type="button" class="layer-pill" data-layer="margin" role="tab" aria-selected="false">Margin</button>
                            <button type="button" class="layer-pill" data-layer="competitive" role="tab" aria-selected="false">Competitive</button>
                            <button type="button" class="layer-pill" data-layer="biss" role="tab" aria-selected="false">Biss</button>
                            <button type="button" class="layer-pill" data-layer="fine" role="tab" aria-selected="false">Fine</button>
                            <button type="button" class="layer-pill" data-layer="abughazaleh" role="tab" aria-selected="false">Abughazaleh</button>
                        </div>
                    </div>
                </div>
                <div class="map-container">
                    <div class="map-toolbar">
                        <label class="map-search-wrap">
                            <span class="sr-only">Find precinct</span>
                            <input type="search" id="precinctMapSearch" placeholder="Find precinct…" autocomplete="off" spellcheck="false">
                        </label>
                        <button type="button" class="map-tool-btn" id="mapResetView" title="Reset map view">Reset view</button>
                    </div>
                    <div id="precinct-map"></div>"""
    t = t.replace(old_html, new_html, 1)

# --- CSS ---
if ".layer-pills {" not in t:
    old_css = """        .layer-control input[type="radio"] {
            accent-color: var(--accent-blue);
            margin: 0;
        }
        .map-container {
            position: relative;
            z-index: 0;
            isolation: isolate;
        }
        #precinct-map {
            width: 100%;
            height: 600px;
            border-radius: 6px;
            background: #0f172a;
        }
        #map-loading {
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            font-family: var(--sans);
            font-size: 1rem;
            color: #94a3b8;
            z-index: 600;
        }

        /* ===== MAP LEGEND ===== */
        #map-legend {
            position: absolute;
            bottom: 12px; right: 12px;
            z-index: 800;
            background: rgba(15,23,42,0.93);
            backdrop-filter: blur(8px);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 10px 12px;
            min-width: 140px;
            color: #e2e8f0;
            font-family: var(--sans);
        }
        #map-legend h4 {
            font-size: 0.65rem;
            color: #94a3b8;"""
    new_css = """        .layer-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
        }
        .layer-pill {
            font-family: var(--sans);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.4rem 0.75rem;
            border: 1px solid var(--border-default);
            background: var(--bg-elevated);
            color: var(--text-secondary);
            cursor: pointer;
            transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease, transform 0.15s ease;
        }
        .layer-pill:hover {
            color: var(--text-primary);
            border-color: var(--border-hover);
            background: var(--bg-surface-hover);
        }
        .layer-pill.active {
            color: var(--bg-primary);
            background: var(--text-primary);
            border-color: var(--text-primary);
        }
        .layer-pill:active { transform: scale(0.97); }
        .map-container {
            position: relative;
            z-index: 0;
            isolation: isolate;
            border: 1px solid var(--border-default);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 24px var(--shadow-soft);
        }
        .map-toolbar {
            position: absolute;
            top: 12px; left: 12px;
            z-index: 850;
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            align-items: center;
            max-width: calc(100% - 24px);
        }
        .map-search-wrap input {
            font-family: var(--sans);
            font-size: 0.8rem;
            padding: 0.45rem 0.75rem;
            min-width: 160px;
            border: 1px solid var(--border-default);
            background: color-mix(in srgb, var(--bg-surface) 94%, transparent);
            backdrop-filter: blur(8px);
            color: var(--text-primary);
            border-radius: 4px;
        }
        .map-search-wrap input::placeholder { color: var(--text-tertiary); }
        .map-search-wrap input:focus {
            outline: 2px solid var(--accent-blue);
            outline-offset: 1px;
            border-color: var(--accent-blue);
        }
        .map-tool-btn {
            font-family: var(--sans);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.45rem 0.85rem;
            border: 1px solid var(--border-default);
            background: color-mix(in srgb, var(--bg-surface) 94%, transparent);
            backdrop-filter: blur(8px);
            color: var(--text-secondary);
            cursor: pointer;
            border-radius: 4px;
        }
        .map-tool-btn:hover { color: var(--text-primary); border-color: var(--border-hover); }
        .sr-only {
            position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
            overflow: hidden; clip: rect(0,0,0,0); border: 0;
        }
        #precinct-map {
            width: 100%;
            height: clamp(380px, 55vh, 620px);
            background: #0f172a;
        }
        [data-theme="light"] #precinct-map { background: #e8e6e1; }
        #map-loading {
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            font-family: var(--sans);
            font-size: 0.9rem;
            color: var(--text-secondary);
            z-index: 600;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.75rem;
        }
        #map-loading::before {
            content: '';
            width: 28px; height: 28px;
            border: 2px solid var(--border-default);
            border-top-color: var(--accent-blue);
            border-radius: 50%;
            animation: mapSpin 0.8s linear infinite;
        }
        @keyframes mapSpin { to { transform: rotate(360deg); } }

        /* ===== MAP LEGEND ===== */
        #map-legend {
            position: absolute;
            bottom: 12px; right: 12px;
            z-index: 800;
            background: color-mix(in srgb, var(--bg-surface) 96%, transparent);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-default);
            border-radius: 8px;
            padding: 10px 12px;
            min-width: 140px;
            color: var(--text-primary);
            font-family: var(--sans);
            box-shadow: 0 4px 16px var(--shadow-soft);
        }
        #map-legend h4 {
            font-size: 0.65rem;
            color: var(--text-tertiary);"""
    t = t.replace(old_css, new_css, 1)
    t = t.replace(
        "color: #e2e8f0;\n        }",
        "color: var(--text-secondary);\n        }",
        1,
    )
    old_info = """        #info-panel {
            position: absolute;
            bottom: 12px; left: 12px;
            z-index: 800;
            background: rgba(15,23,42,0.93);
            backdrop-filter: blur(8px);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 14px 16px;
            width: 300px;
            display: none;
            color: #e2e8f0;
            font-family: var(--sans);
        }"""
    new_info = """        #info-panel {
            position: absolute;
            bottom: 12px; left: 12px;
            z-index: 800;
            background: color-mix(in srgb, var(--bg-surface) 96%, transparent);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-default);
            border-radius: 8px;
            padding: 14px 16px;
            width: min(300px, calc(100% - 24px));
            display: none;
            color: var(--text-primary);
            font-family: var(--sans);
            box-shadow: 0 8px 32px var(--shadow-soft);
            transform: translateY(8px);
            opacity: 0;
            transition: opacity 0.25s ease, transform 0.25s ease;
        }
        #info-panel.is-visible {
            transform: translateY(0);
            opacity: 1;
        }"""
    t = t.replace(old_info, new_info, 1)
    t = t.replace(
        """        /* ===== LEAFLET OVERRIDES (dark map) ===== */
        .leaflet-container { background: #0f172a !important; }""",
        """        /* ===== LEAFLET OVERRIDES ===== */
        .leaflet-container { background: #0f172a !important; }
        [data-theme="light"] .leaflet-container { background: #e8e6e1 !important; }
        .leaflet-interactive { cursor: pointer; }""",
        1,
    )

# --- JS ---
if "syncMapBasemap" not in t:
    t = t.replace(
        """            if (isLight) {
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('il9-theme', 'dark');
            } else {
                document.documentElement.setAttribute('data-theme', 'light');
                localStorage.setItem('il9-theme', 'light');
            }
        }""",
        """            if (isLight) {
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('il9-theme', 'dark');
            } else {
                document.documentElement.setAttribute('data-theme', 'light');
                localStorage.setItem('il9-theme', 'light');
            }
            if (typeof syncMapBasemap === 'function') syncMapBasemap();
        }""",
        1,
    )
    old_init = """        // ===== MAP INIT =====
        var map = L.map('precinct-map', { zoomControl: true, attributionControl: true }).setView([42.00, -87.72], 11);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19, subdomains: 'abcd'
        }).addTo(map);

        var currentLayer = 'winner';
        var selectedFeature = null;
        var matchedLayer, unmatchedLayer;"""
    new_init = """        // ===== MAP INIT =====
        var mapRenderer = L.canvas({ padding: 0.5 });
        var map = L.map('precinct-map', {
            zoomControl: false,
            attributionControl: true,
            preferCanvas: true,
            zoomAnimation: true,
            fadeAnimation: true
        }).setView([42.00, -87.72], 11);
        L.control.zoom({ position: 'topright' }).addTo(map);

        var basemapDark = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19, subdomains: 'abcd', attribution: '&copy; CARTO'
        });
        var basemapLight = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19, subdomains: 'abcd', attribution: '&copy; CARTO'
        });
        var activeBasemap = basemapDark;
        function syncMapBasemap() {
            var useLight = document.documentElement.getAttribute('data-theme') === 'light';
            var next = useLight ? basemapLight : basemapDark;
            if (next === activeBasemap) return;
            map.removeLayer(activeBasemap);
            next.addTo(map);
            activeBasemap = next;
        }
        activeBasemap.addTo(map);

        var currentLayer = 'winner';
        var selectedFeature = null;
        var matchedLayer, unmatchedLayer;
        var districtBounds = null;
        var precinctIndex = {};
        var highlightLayer = null;

        function refreshAllStyles(animate) {
            if (!matchedLayer) return;
            matchedLayer.eachLayer(function(layer) {
                var target = getStyle(layer.feature);
                if (animate) {
                    var cur = layer.options.fillOpacity != null ? layer.options.fillOpacity : 0.75;
                    layer.setStyle({ fillOpacity: cur * 0.35 });
                    requestAnimationFrame(function() { layer.setStyle(target); });
                } else {
                    layer.setStyle(target);
                }
            });
            if (selectedFeature) {
                selectedFeature.setStyle({ color: '#ffffff', weight: 3, fillOpacity: 0.92 });
                selectedFeature.bringToFront();
            }
        }

        function setActiveLayerPill(layerName) {
            document.querySelectorAll('.layer-pill').forEach(function(btn) {
                var on = btn.dataset.layer === layerName;
                btn.classList.toggle('active', on);
                btn.setAttribute('aria-selected', on ? 'true' : 'false');
            });
        }"""
    t = t.replace(old_init, new_init, 1)
    t = t.replace(
        """        function showInfo(p) {
            document.getElementById('info-panel').style.display = 'block';""",
        """        function showInfo(p) {
            var panel = document.getElementById('info-panel');
            panel.style.display = 'block';
            requestAnimationFrame(function() { panel.classList.add('is-visible'); });""",
        1,
    )
    old_close = """        document.getElementById('close-info').onclick = function() {
            document.getElementById('info-panel').style.display = 'none';
            if (selectedFeature) {
                selectedFeature.setStyle({ color: getStyle(selectedFeature.feature).color, weight: getStyle(selectedFeature.feature).weight });
                selectedFeature = null;
            }
        };

        // ===== LAYER SWITCHING =====
        document.querySelectorAll('input[name=layer]').forEach(function(radio) {
            radio.addEventListener('change', function() {
                currentLayer = this.value;
                if (matchedLayer) {
                    matchedLayer.eachLayer(function(layer) {
                        layer.setStyle(getStyle(layer.feature));
                    });
                }
                if (selectedFeature) {
                    selectedFeature.setStyle({ color: '#ffffff', weight: 3 });
                    selectedFeature.bringToFront();
                }
                updateLegend();
            });
        });

        // Click map to deselect
        map.on('click', function() {
            if (selectedFeature) {
                selectedFeature.setStyle({ color: getStyle(selectedFeature.feature).color, weight: getStyle(selectedFeature.feature).weight });
                selectedFeature = null;
            }
            document.getElementById('info-panel').style.display = 'none';
        });"""
    new_close = Path(__file__).read_text().split("NEW_HANDLERS = '''")[1].split("'''")[0] if False else ""
    new_close = """        function hideInfoPanel() {
            var panel = document.getElementById('info-panel');
            panel.classList.remove('is-visible');
            setTimeout(function() {
                if (!panel.classList.contains('is-visible')) panel.style.display = 'none';
            }, 220);
        }

        document.getElementById('close-info').onclick = function() {
            hideInfoPanel();
            if (selectedFeature) {
                selectedFeature.setStyle(getStyle(selectedFeature.feature));
                selectedFeature = null;
            }
        };

        document.querySelectorAll('.layer-pill').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var next = this.dataset.layer;
                if (next === currentLayer) return;
                currentLayer = next;
                setActiveLayerPill(next);
                refreshAllStyles(true);
                updateLegend();
            });
        });

        document.getElementById('mapResetView').addEventListener('click', function() {
            if (districtBounds) {
                map.flyToBounds(districtBounds, { padding: [28, 28], duration: 0.8, easeLinearity: 0.25 });
            }
            hideInfoPanel();
            if (selectedFeature) {
                selectedFeature.setStyle(getStyle(selectedFeature.feature));
                selectedFeature = null;
            }
        });

        var searchDebounce;
        document.getElementById('precinctMapSearch').addEventListener('input', function() {
            var q = this.value.trim().toLowerCase();
            clearTimeout(searchDebounce);
            if (!q) {
                if (highlightLayer) { map.removeLayer(highlightLayer); highlightLayer = null; }
                return;
            }
            searchDebounce = setTimeout(function() {
                var matchKey = precinctIndex[q] ? q : Object.keys(precinctIndex).find(function(k) { return k.indexOf(q) !== -1; });
                if (!matchKey) return;
                var layer = precinctIndex[matchKey];
                if (!layer) return;
                var center = layer.getBounds().getCenter();
                map.flyTo(center, Math.max(map.getZoom(), 13), { duration: 0.6 });
                if (highlightLayer) map.removeLayer(highlightLayer);
                highlightLayer = L.circleMarker(center, {
                    radius: 10, color: '#ED1C24', weight: 2, fillColor: '#ED1C24', fillOpacity: 0.15
                }).addTo(map);
                layer.fire('click');
            }, 280);
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                hideInfoPanel();
                if (selectedFeature) {
                    selectedFeature.setStyle(getStyle(selectedFeature.feature));
                    selectedFeature = null;
                }
            }
        });

        map.on('click', function() {
            if (selectedFeature) {
                selectedFeature.setStyle(getStyle(selectedFeature.feature));
                selectedFeature = null;
            }
            hideInfoPanel();
            if (highlightLayer) { map.removeLayer(highlightLayer); highlightLayer = null; }
        });"""
    t = t.replace(old_close, new_close, 1)
    old_hover = """                        layer.on('mouseover', function() {
                            if (selectedFeature !== layer) {
                                layer.setStyle({ color: '#94a3b8', weight: 2 });
                            }
                            layer.bringToFront();
                        });
                        layer.on('mouseout', function() {
                            if (selectedFeature !== layer) {
                                layer.setStyle({ color: getStyle(feature).color, weight: getStyle(feature).weight });
                            }
                        });
                        layer.on('click', function(e) {
                            L.DomEvent.stopPropagation(e);
                            if (selectedFeature) {
                                selectedFeature.setStyle({ color: getStyle(selectedFeature.feature).color, weight: getStyle(selectedFeature.feature).weight });
                            }
                            selectedFeature = layer;
                            layer.setStyle({ color: '#ffffff', weight: 3 });
                            layer.bringToFront();
                            showInfo(p);
                        });
                    }
                }).addTo(map);

                map.fitBounds(matchedLayer.getBounds(), { padding: [20, 20] });
                updateLegend();"""
    new_hover = """                        var key = (p.JoinField || '').toLowerCase();
                        if (key) precinctIndex[key] = layer;

                        layer.on('mouseover', function() {
                            if (selectedFeature !== layer) {
                                var s = getStyle(feature);
                                layer.setStyle({
                                    color: '#f8fafc',
                                    weight: 2,
                                    fillOpacity: Math.min((s.fillOpacity || 0.75) + 0.12, 0.95)
                                });
                            }
                            layer.bringToFront();
                        });
                        layer.on('mouseout', function() {
                            if (selectedFeature !== layer) {
                                layer.setStyle(getStyle(feature));
                            }
                        });
                        layer.on('click', function(e) {
                            L.DomEvent.stopPropagation(e);
                            if (selectedFeature && selectedFeature !== layer) {
                                selectedFeature.setStyle(getStyle(selectedFeature.feature));
                            }
                            selectedFeature = layer;
                            layer.setStyle({ color: '#ffffff', weight: 3, fillOpacity: 0.92 });
                            layer.bringToFront();
                            showInfo(p);
                        });
                    }
                }, { renderer: mapRenderer }).addTo(map);

                districtBounds = matchedLayer.getBounds();
                map.flyToBounds(districtBounds, { padding: [28, 28], duration: 1.1, easeLinearity: 0.2 });
                updateLegend();
                syncMapBasemap();"""
    t = t.replace(old_hover, new_hover, 1)

t = t.replace(
    """        .comp-card {
            text-align: center;
            padding: 0.75rem 0.5rem;
            border-radius: 6px;
            border: 1px solid var(--border-default);
        }""",
    """        .comp-card {
            text-align: center;
            padding: 0.9rem 0.65rem;
            border-radius: 4px;
            border: 1px solid var(--border-default);
            background: var(--bg-surface);
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
        }
        .comp-grid .comp-card:hover {
            transform: translateY(-2px);
            border-color: var(--border-hover);
            box-shadow: 0 6px 18px var(--shadow-soft);
        }""",
    1,
)

p.write_text(t)
assert "syncMapBasemap" in t and "layer-pill" in t
print("OK patched", p.stat().st_size)
