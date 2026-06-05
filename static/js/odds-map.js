// ===== CANDIDATE COLORS =====
        var COLORS = {
            'Biss': '#C41E3A',
            'Fine': '#1E3A8A',
            'Abughazaleh': '#2D6A4F',
            'Simmons': '#FF8C00',
            'Amiwala': '#800080',
            'Huynh': '#008080',
            'Andrew': '#C2B280'
        };
        var CAND_FIELDS = ['avg_biss','avg_abughazaleh','avg_fine','avg_simmons','avg_amiwala','avg_huynh','avg_andrew'];
        var CAND_NAMES = {
            'avg_biss': 'Biss',
            'avg_abughazaleh': 'Abughazaleh',
            'avg_fine': 'Fine',
            'avg_simmons': 'Simmons',
            'avg_amiwala': 'Amiwala',
            'avg_huynh': 'Huynh',
            'avg_andrew': 'Andrew'
        };

        function whenMapReady(fn) {
            if (window.L) { fn(); return; }
            function run() { if (window.L) fn(); }
            window.addEventListener('load', run);
            var s = document.querySelector('script[src*="leaflet"]');
            if (s) s.addEventListener('load', run);
        }

        whenMapReady(function initPrecinctMap() {
        // ===== MAP INIT =====
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
        }

        // ===== STYLE FUNCTIONS =====
        function winnerStyle(feature) {
            var p = feature.properties;
            return { fillColor: COLORS[p.winner] || '#475569', fillOpacity: 0.75, color: '#1e293b', weight: 0.8, opacity: 0.6 };
        }

        function marginStyle(feature) {
            var p = feature.properties;
            var m = p.margin || 0;
            var opacity = m >= 20 ? 1 : (m < 5 ? 0.35 : 0.35 + (m - 5) / 15 * 0.65);
            return { fillColor: COLORS[p.winner] || '#475569', fillOpacity: opacity, color: '#1e293b', weight: 0.8, opacity: 0.6 };
        }

        function heatStyle(field, hue) {
            return function(feature) {
                var val = feature.properties[field] || 0;
                var pct = Math.min(val / 45, 1);
                var lightness = 90 - pct * 60;
                var sat = 30 + pct * 70;
                return { fillColor: 'hsl(' + hue + ',' + sat + '%,' + lightness + '%)', fillOpacity: 0.8, color: '#1e293b', weight: 0.8, opacity: 0.6 };
            };
        }

        function competitiveStyle(feature) {
            var m = feature.properties.margin || 99;
            var c;
            if (m < 3) c = '#dc2626';
            else if (m < 5) c = '#f97316';
            else if (m < 8) c = '#eab308';
            else if (m < 12) c = '#84cc16';
            else c = '#22c55e';
            return { fillColor: c, fillOpacity: 0.75, color: '#1e293b', weight: 0.8, opacity: 0.6 };
        }

        function getStyle(feature) {
            switch (currentLayer) {
                case 'winner': return winnerStyle(feature);
                case 'margin': return marginStyle(feature);
                case 'biss': return heatStyle('avg_biss', 0)(feature);
                case 'fine': return heatStyle('avg_fine', 215)(feature);
                case 'abughazaleh': return heatStyle('avg_abughazaleh', 155)(feature);
                case 'competitive': return competitiveStyle(feature);
                default: return winnerStyle(feature);
            }
        }

        // ===== LEGEND =====
        function updateLegend() {
            var el = document.getElementById('map-legend');
            var html = '';
            switch (currentLayer) {
                case 'winner':
                    html = '<h4>Predicted Winner</h4>';
                    ['Biss','Fine','Abughazaleh','Simmons','Amiwala','Huynh','Andrew'].forEach(function(n) {
                        html += '<div class="legend-item"><div class="legend-swatch" style="background:' + COLORS[n] + '"></div><span class="legend-label">' + n + '</span></div>';
                    });
                    html += '<div class="legend-item"><div class="legend-swatch" style="background:#1e293b;opacity:0.5"></div><span class="legend-label">No Data</span></div>';
                    break;
                case 'margin':
                    html = '<h4>Win Margin</h4>';
                    html += '<div class="legend-item"><div class="legend-swatch" style="background:#94a3b8;opacity:1"></div><span class="legend-label">20+ pts</span></div>';
                    html += '<div class="legend-item"><div class="legend-swatch" style="background:#94a3b8;opacity:0.55"></div><span class="legend-label">~10 pts</span></div>';
                    html += '<div class="legend-item"><div class="legend-swatch" style="background:#94a3b8;opacity:0.35"></div><span class="legend-label">&lt;5 pts</span></div>';
                    html += '<div style="font-size:0.6rem;color:#64748b;margin-top:3px;">Color = winner</div>';
                    break;
                case 'biss':
                    html = '<h4>Biss Vote Share</h4>';
                    html += '<div class="legend-grad" style="background:linear-gradient(to right,hsl(0,30%,90%),hsl(0,100%,30%))"></div>';
                    html += '<div class="legend-range"><span>0%</span><span>45%</span></div>';
                    break;
                case 'fine':
                    html = '<h4>Fine Vote Share</h4>';
                    html += '<div class="legend-grad" style="background:linear-gradient(to right,hsl(215,30%,90%),hsl(215,100%,30%))"></div>';
                    html += '<div class="legend-range"><span>0%</span><span>45%</span></div>';
                    break;
                case 'abughazaleh':
                    html = '<h4>Abughazaleh Vote Share</h4>';
                    html += '<div class="legend-grad" style="background:linear-gradient(to right,hsl(155,30%,90%),hsl(155,100%,30%))"></div>';
                    html += '<div class="legend-range"><span>0%</span><span>45%</span></div>';
                    break;
                case 'competitive':
                    html = '<h4>Competitiveness</h4>';
                    [['#dc2626','&lt;3 pts (Toss-up)'],['#f97316','3\u20135 pts'],['#eab308','5\u20138 pts'],['#84cc16','8\u201312 pts'],['#22c55e','12+ pts (Safe)']].forEach(function(x) {
                        html += '<div class="legend-item"><div class="legend-swatch" style="background:' + x[0] + '"></div><span class="legend-label">' + x[1] + '</span></div>';
                    });
                    break;
            }
            el.innerHTML = html;
        }

        // ===== INFO PANEL =====
        function showInfo(p) {
            var panel = document.getElementById('info-panel');
            panel.style.display = 'block';
            requestAnimationFrame(function() { panel.classList.add('is-visible'); });
            document.getElementById('info-precinct').textContent = p.JoinField;
            document.getElementById('info-winner').innerHTML =
                '<span style="color:' + COLORS[p.winner] + '">' + p.winner + '</span> \u2014 ' + p.winner_pct + '%';

            var cands = CAND_FIELDS.map(function(f) { return { name: CAND_NAMES[f], val: p[f] || 0, color: COLORS[CAND_NAMES[f]] }; });
            cands.sort(function(a, b) { return b.val - a.val; });
            var maxVal = Math.max.apply(null, cands.map(function(c) { return c.val; }));
            var barsHtml = '';
            cands.forEach(function(c) {
                var w = (c.val / (maxVal * 1.1)) * 100;
                barsHtml += '<div class="bar-row"><div class="bar-label">' + c.name + '</div>' +
                    '<div class="bar-track"><div class="bar-fill" style="width:' + w + '%;background:' + c.color + '"></div>' +
                    '<div class="bar-val">' + c.val.toFixed(1) + '%</div></div></div>';
            });
            document.getElementById('info-bars').innerHTML = barsHtml;
            document.getElementById('info-margin').innerHTML =
                'Margin over ' + p.runner_up + ': <strong>+' + p.margin + ' pts</strong>';

            var wr = '';
            if (p.pct_biss_wins != null) wr += '<span style="color:' + COLORS['Biss'] + '">Biss ' + p.pct_biss_wins.toFixed(1) + '%</span>';
            if (p.pct_fine_wins != null) wr += ' &middot; <span style="color:' + COLORS['Fine'] + '">Fine ' + p.pct_fine_wins.toFixed(1) + '%</span>';
            if (p.pct_abughazaleh_wins != null) wr += ' &middot; <span style="color:' + COLORS['Abughazaleh'] + '">Abu. ' + p.pct_abughazaleh_wins.toFixed(1) + '%</span>';
            document.getElementById('info-winrates').innerHTML = wr;
        }

        function hideInfoPanel() {
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
        });

        // ===== NERD SECTION TOGGLE =====
        function toggleNerdSection() {
            var body = document.getElementById('nerdBody');
            var arrow = document.getElementById('nerdArrow');
            body.classList.toggle('open');
            arrow.classList.toggle('open');
        }

        // ===== NERD DATA (populated after GeoJSON loads) =====
        var allPrecinctData = [];
        var currentSort = { col: 'margin', asc: true }; // tightest first

        function buildCompGrid(data) {
            var buckets = [
                { label: 'Toss-up', range: '<3 pts', color: '#dc2626', count: 0 },
                { label: 'Lean', range: '3-5 pts', color: '#f97316', count: 0 },
                { label: 'Likely', range: '5-8 pts', color: '#eab308', count: 0 },
                { label: 'Favored', range: '8-12 pts', color: '#84cc16', count: 0 },
                { label: 'Safe', range: '12+ pts', color: '#22c55e', count: 0 }
            ];
            data.forEach(function(p) {
                var m = p.margin || 99;
                if (m < 3) buckets[0].count++;
                else if (m < 5) buckets[1].count++;
                else if (m < 8) buckets[2].count++;
                else if (m < 12) buckets[3].count++;
                else buckets[4].count++;
            });
            var html = '';
            buckets.forEach(function(b) {
                html += '<div class="comp-card" style="background:' + b.color + '15;">' +
                    '<div class="comp-count">' + b.count + '</div>' +
                    '<div class="comp-label" style="color:' + b.color + '">' + b.label + '</div>' +
                    '<div class="comp-range">' + b.range + '</div></div>';
            });
            document.getElementById('compGrid').innerHTML = html;
        }

        function buildBattlegroundTable(data) {
            var sorted = data.slice().sort(function(a, b) { return (a.margin || 99) - (b.margin || 99); });
            var top10 = sorted.slice(0, 10);
            var tbody = document.querySelector('#battlegroundTable tbody');
            var html = '';
            top10.forEach(function(p) {
                html += '<tr>' +
                    '<td>' + p.JoinField + '</td>' +
                    '<td class="winner-cell" style="color:' + COLORS[p.winner] + '">' + p.winner + '</td>' +
                    '<td>' + p.runner_up + '</td>' +
                    '<td><strong>' + (p.margin != null ? p.margin.toFixed(1) : '-') + '</strong></td>' +
                    '<td>' + (p.pct_biss_wins != null ? p.pct_biss_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '<td>' + (p.pct_fine_wins != null ? p.pct_fine_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '<td>' + (p.pct_abughazaleh_wins != null ? p.pct_abughazaleh_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '</tr>';
            });
            tbody.innerHTML = html;
        }

        function renderFullTable() {
            var search = document.getElementById('precinctSearch').value.toLowerCase();
            var winnerFilter = document.getElementById('winnerFilter').value;

            var filtered = allPrecinctData.filter(function(p) {
                if (search && p.JoinField.toLowerCase().indexOf(search) === -1) return false;
                if (winnerFilter && p.winner !== winnerFilter) return false;
                return true;
            });

            // Sort
            filtered.sort(function(a, b) {
                var va = a[currentSort.col];
                var vb = b[currentSort.col];
                if (va == null) va = currentSort.asc ? Infinity : -Infinity;
                if (vb == null) vb = currentSort.asc ? Infinity : -Infinity;
                if (typeof va === 'string') {
                    return currentSort.asc ? va.localeCompare(vb) : vb.localeCompare(va);
                }
                return currentSort.asc ? va - vb : vb - va;
            });

            var tbody = document.querySelector('#fullTable tbody');
            var html = '';
            filtered.forEach(function(p) {
                var demPct = p.PresTot > 0 ? ((p.PresDem / p.PresTot) * 100).toFixed(0) + '%' : '-';
                html += '<tr>' +
                    '<td>' + p.JoinField + '</td>' +
                    '<td class="winner-cell" style="color:' + COLORS[p.winner] + '">' + p.winner + '</td>' +
                    '<td><strong>' + (p.margin != null ? p.margin.toFixed(1) : '-') + '</strong></td>' +
                    '<td>' + (p.runner_up || '-') + '</td>' +
                    '<td>' + (p.avg_biss != null ? p.avg_biss.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_fine != null ? p.avg_fine.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_abughazaleh != null ? p.avg_abughazaleh.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_simmons != null ? p.avg_simmons.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_andrew != null ? p.avg_andrew.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_amiwala != null ? p.avg_amiwala.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.avg_huynh != null ? p.avg_huynh.toFixed(1) : '-') + '</td>' +
                    '<td>' + (p.pct_biss_wins != null ? p.pct_biss_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '<td>' + (p.pct_fine_wins != null ? p.pct_fine_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '<td>' + (p.pct_abughazaleh_wins != null ? p.pct_abughazaleh_wins.toFixed(1) + '%' : '-') + '</td>' +
                    '<td>' + demPct + '</td>' +
                    '<td>' + (p.PresTot || '-') + '</td>' +
                    '</tr>';
            });
            tbody.innerHTML = html;
            document.getElementById('tableFooter').textContent = 'Showing ' + filtered.length + ' of ' + allPrecinctData.length + ' precincts';
        }

        // Sort click handler
        document.querySelectorAll('#fullTable th.sortable').forEach(function(th) {
            th.addEventListener('click', function() {
                var col = this.dataset.col;
                if (currentSort.col === col) {
                    currentSort.asc = !currentSort.asc;
                } else {
                    currentSort.col = col;
                    currentSort.asc = col === 'JoinField' || col === 'winner' || col === 'runner_up';
                }
                // Update header classes
                document.querySelectorAll('#fullTable th.sortable').forEach(function(h) {
                    h.classList.remove('sorted-asc', 'sorted-desc');
                });
                this.classList.add(currentSort.asc ? 'sorted-asc' : 'sorted-desc');
                renderFullTable();
            });
        });

        // Search & filter handlers
        function debounce(fn, ms) {
            var t;
            return function() {
                var args = arguments, ctx = this;
                clearTimeout(t);
                t = setTimeout(function() { fn.apply(ctx, args); }, ms);
            };
        }
        document.getElementById('precinctSearch').addEventListener('input', debounce(renderFullTable, 200));
        document.getElementById('winnerFilter').addEventListener('change', renderFullTable);

        // ===== LOAD GEOJSON =====
        fetch('/api/model/precincts')
            .then(function(r) {
                if (!r.ok) throw new Error('Precinct data HTTP ' + r.status);
                return r.json();
            })
            .then(function(geojson) {
                document.getElementById('map-loading').style.display = 'none';

                var matchedFeatures = [];
                var unmatchedFeatures = [];
                geojson.features.forEach(function(f) {
                    if (f.properties.winner === 'No Data') {
                        unmatchedFeatures.push(f);
                    } else {
                        matchedFeatures.push(f);
                    }
                });

                // Unmatched (context boundaries)
                unmatchedLayer = L.geoJSON({ type: 'FeatureCollection', features: unmatchedFeatures }, {
                    style: { fillColor: '#1e293b', fillOpacity: 0.3, color: '#1e293b', weight: 0.5, opacity: 0.4 },
                    interactive: false
                }).addTo(map);

                // Matched precincts
                matchedLayer = L.geoJSON({ type: 'FeatureCollection', features: matchedFeatures }, {
                    style: getStyle,
                    onEachFeature: function(feature, layer) {
                        var p = feature.properties;

                        // Build rich tooltip with mini bar chart
                        var cands = CAND_FIELDS.map(function(f) {
                            return { name: CAND_NAMES[f], val: p[f] || 0, color: COLORS[CAND_NAMES[f]] };
                        }).sort(function(a, b) { return b.val - a.val; });
                        var topMax = cands[0].val;

                        var barsHtml = '';
                        cands.forEach(function(c) {
                            var w = topMax > 0 ? (c.val / topMax) * 100 : 0;
                            barsHtml += '<div class="tip-bar-row">' +
                                '<span class="tip-bar-name">' + c.name + '</span>' +
                                '<div class="tip-bar-track"><div class="tip-bar-fill" style="width:' + w + '%;background:' + c.color + '"></div></div>' +
                                '<span class="tip-bar-val">' + c.val.toFixed(1) + '%</span></div>';
                        });

                        var winRates = '';
                        if (p.pct_biss_wins != null) winRates += '<span style="color:#C41E3A">Biss ' + p.pct_biss_wins.toFixed(0) + '%</span>';
                        if (p.pct_fine_wins != null) winRates += ' &middot; <span style="color:#00008B">Fine ' + p.pct_fine_wins.toFixed(0) + '%</span>';
                        if (p.pct_abughazaleh_wins != null) winRates += ' &middot; <span style="color:#228B22">Abu. ' + p.pct_abughazaleh_wins.toFixed(0) + '%</span>';

                        var tip = '<div class="tip-header">' + p.JoinField + '</div>' +
                            '<div class="tip-winner"><span style="color:' + COLORS[p.winner] + ';font-weight:700;">' + p.winner + '</span> wins &middot; +' + p.margin + ' pts over ' + p.runner_up + '</div>' +
                            '<div style="font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:2px;">Avg. Vote Share</div>' +
                            '<div class="tip-bars">' + barsHtml + '</div>' +
                            '<div style="font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-top:6px;margin-bottom:2px;">Sim. Win Rate</div>' +
                            '<div class="tip-meta" style="color:#e2e8f0;">' + winRates + '</div>';

                        layer.bindTooltip(tip, { sticky: true, direction: 'top', offset: [0, -12] });

                        var key = (p.JoinField || '').toLowerCase();
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
                syncMapBasemap();

                // Populate nerd section
                allPrecinctData = matchedFeatures.map(function(f) { return f.properties; });
                buildCompGrid(allPrecinctData);
                buildBattlegroundTable(allPrecinctData);
                renderFullTable();
            })
            .catch(function(err) {
                var el = document.getElementById('map-loading');
                el.style.display = 'flex';
                el.setAttribute('aria-live', 'assertive');
                el.textContent = 'Could not load precinct map. Please try again later.';
                console.error(err);
            });
        }); // end whenMapReady / initPrecinctMap


window.onThemeChange = function () {
    if (typeof syncMapBasemap === "function") syncMapBasemap();
};
