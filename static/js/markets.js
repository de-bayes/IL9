document.addEventListener('click', (e) => {
        const hamburger = document.getElementById('hamburger');
        const mobileNav = document.getElementById('mobileNav');
        if (!hamburger.contains(e.target) && !mobileNav.contains(e.target)) {
            hamburger.classList.remove('active');
            mobileNav.classList.remove('active');
        }
    });

    // Share button functionality
    const shareButton = document.getElementById('shareButton');
    if (shareButton) {
        shareButton.addEventListener('click', async () => {
            const leaderName = document.getElementById('leaderName')?.textContent || 'Loading...';
            const leaderProb = document.getElementById('leaderAggregate')?.textContent || '--';

            const shareData = {
                title: 'IL9Cast - Illinois 9th District Primary Forecast',
                text: `${leaderName} leads at ${leaderProb} in the IL-9 Democratic Primary prediction markets`,
                url: window.location.href
            };

            // Try native share (mobile)
            if (navigator.share && navigator.canShare && navigator.canShare(shareData)) {
                try {
                    await navigator.share(shareData);
                } catch (err) {
                    if (err.name !== 'AbortError') {
                        console.error('Share failed:', err);
                        fallbackCopyLink();
                    }
                }
            } else {
                // Fallback: copy link to clipboard (desktop)
                fallbackCopyLink();
            }
        });
    }

    function fallbackCopyLink() {
        navigator.clipboard.writeText(window.location.href).then(() => {
            const shareText = document.querySelector('.share-text');
            const originalText = shareText.textContent;
            shareText.textContent = 'Link Copied!';
            setTimeout(() => {
                shareText.textContent = originalText;
            }, 2000);
        }).catch(err => {
            console.error('Copy failed:', err);
            alert('Share URL: ' + window.location.href);
        });
    }
});

// Central Time formatter utility
function formatCentralTime(date, options = {}) {
    const defaults = {
        timeZone: 'America/Chicago',
        hour12: true
    };
    const formatter = new Intl.DateTimeFormat('en-US', { ...defaults, ...options });
    return formatter.format(date) + ' CT';
}

let marketChart = null;
let candidatesData = [];
let manifoldData = {};
let kalshiData = {};
let currentChartPeriod = 'all';
let manifoldLastUpdate = null;
let kalshiLastUpdate = null;

// === ELECTION OVER: Display override (does NOT affect data collection/storage) ===
const ELECTION_OVER = true;
const ELECTION_WINNER = 'Daniel Biss';

// Candidate headshot mapping
const candidateImages = {
    'daniel biss': '/static/images/candidates/biss.jpg',
    'biss': '/static/images/candidates/biss.jpg',
    'bushra amiwala': '/static/images/candidates/bushra.jpg',
    'amiwala': '/static/images/candidates/bushra.jpg',
    'laura fine': '/static/images/candidates/fine.jpg',
    'fine': '/static/images/candidates/fine.jpg',
    'kat abugazaleh': '/static/images/candidates/katabu.jpg',
    'kat abughazaleh': '/static/images/candidates/katabu.jpg',
    'abugazaleh': '/static/images/candidates/katabu.jpg',
    'abughazaleh': '/static/images/candidates/katabu.jpg',
    'mike simmons': '/static/images/candidates/simmons.jpg',
    'simmons': '/static/images/candidates/simmons.jpg',
    'phil andrew': '/static/images/candidates/philandrew.jpg',
    'andrew': '/static/images/candidates/philandrew.jpg'
};

function getCandidateImage(name) {
    const normalized = name.toLowerCase().trim();
    // Try full name first
    if (candidateImages[normalized]) return candidateImages[normalized];
    // Try last name
    const lastName = normalized.split(' ').pop();
    if (candidateImages[lastName]) return candidateImages[lastName];
    // Default placeholder
    return null;
}

function toggleMarket(id) {
    const market = document.getElementById(id);
    market.classList.toggle('open');
}

function normalizeCandidate(name) {
    // Extract just the candidate name for matching
    let cleaned = name.toLowerCase()
        .replace(/^wil\s+/i, '') // Remove "Wil " prefix from Kalshi
        .replace(/^will\s+/i, '') // Remove "Will " prefix
        .replace(/\sbe\sthe\sdemocratic\snominee.*$/i, '') // Remove suffix
        .replace(/\swin.*$/i, '') // Remove "win..." suffix
        .replace(/dr\.\s+/i, '')
        .trim();

    // Handle name variations/misspellings
    const nameVariations = {
        'kat abughazaleh': 'kat abugazaleh',
        // Add more variations as needed
    };

    if (nameVariations[cleaned]) {
        cleaned = nameVariations[cleaned];
    }

    return cleaned;
}

// Candidates excluded from all displays
const EXCLUDED_CANDIDATES = ['mark su', 'nick pyati', 'sam polan', 'howard rosenblum', 'illinois senate'];

function isExcludedCandidate(name) {
    const lower = name.toLowerCase();
    return EXCLUDED_CANDIDATES.some(exc => lower.includes(exc));
}

function cleanCandidateName(name) {
    // Clean up display name for better readability
    return name
        .replace(/^wil\s+/i, '') // Remove "Wil " prefix
        .replace(/^will\s+/i, '') // Remove "Will " prefix
        .replace(/\sbe\sthe\sdemocratic\snominee.*$/i, '') // Remove question suffix
        .replace(/\?/g, '') // Remove question marks
        .trim();
}

async function calculateAggregate() {
    // Match candidates across platforms and calculate aggregated probabilities
    const aggregated = [];
    const breakdowns = [];

    // Get all unique candidates
    const allCandidates = new Set();
    Object.keys(manifoldData).forEach(name => allCandidates.add(name));
    Object.keys(kalshiData).forEach(name => allCandidates.add(name));

    allCandidates.forEach(candidateKey => {
        // Filter out Jan Schakowsky and excluded candidates
        if (candidateKey.toLowerCase().includes('schakowsky') || isExcludedCandidate(candidateKey)) {
            return;
        }

        const manifoldProb = manifoldData[candidateKey]?.probability || 0;
        const kalshiInfo = kalshiData[candidateKey] || {};
        const kalshiLast = kalshiInfo.last_price || 0;
        const kalshiMid = kalshiInfo.midpoint || 0;

        const kalshiBid = kalshiInfo.yes_bid || 0;
        const kalshiAsk = kalshiInfo.yes_ask || 0;
        const hasTwoSidedBook = kalshiBid > 0 && kalshiAsk > 0;
        const hasUnlockedSpread = kalshiAsk > kalshiBid;

        // Treat Kalshi as active only when order book is actionable.
        const hasKalshi = hasTwoSidedBook && hasUnlockedSpread && (kalshiLast > 0 || kalshiMid > 0);

        // Calculate liquidity-weighted price
        // Use pre-calculated liquidity-weighted probability from fetchKalshiData
        const kalshiLiquidity = kalshiInfo.kalshiLiquidity || kalshiMid;

        let aggregate;

        // Spread-based weight throttle
        let isThrottled = false;
        if (hasKalshi) {
            const lastOutsideSpread = (
                kalshiBid > 0 && kalshiAsk > 0 &&
                (kalshiLast > kalshiAsk || kalshiLast < kalshiBid)
            );
            if (lastOutsideSpread) {
                // Throttled: reduce last_price weight, boost spread-based components
                aggregate = (0.40 * manifoldProb) + (0.20 * kalshiLast) + (0.28 * kalshiMid) + (0.12 * kalshiLiquidity);
                isThrottled = true;
            } else {
                // Normal weights
                aggregate = (0.40 * manifoldProb) + (0.42 * kalshiLast) + (0.12 * kalshiMid) + (0.06 * kalshiLiquidity);
            }
        } else {
            aggregate = manifoldProb;
        }

        if (aggregate > 0 || manifoldProb > 0) {
            // Use cleaned display name
            const displayName = manifoldData[candidateKey]?.displayName || kalshiInfo.displayName || candidateKey;
            const cleanName = cleanCandidateName(displayName);

            aggregated.push({
                name: cleanName,
                probability: aggregate,
                hasKalshi: hasKalshi
            });

            // Store breakdown for display with correct weights
            const wManifold = hasKalshi ? 0.40 : 1.0;
            const wLast = hasKalshi ? (isThrottled ? 0.20 : 0.42) : 0;
            const wMid = hasKalshi ? (isThrottled ? 0.28 : 0.12) : 0;
            const wLiq = hasKalshi ? (isThrottled ? 0.12 : 0.06) : 0;

            breakdowns.push({
                name: cleanName,
                manifoldProb: manifoldProb.toFixed(1),
                kalshiLast: kalshiLast.toFixed(1),
                kalshiMid: kalshiMid.toFixed(1),
                kalshiLiq: kalshiLiquidity.toFixed(1),
                manifoldContribution: (wManifold * manifoldProb).toFixed(2),
                kalshiLastContribution: (wLast * kalshiLast).toFixed(2),
                kalshiMidContribution: (wMid * kalshiMid).toFixed(2),
                kalshiLiqContribution: (wLiq * kalshiLiquidity).toFixed(2),
                aggregate: aggregate.toFixed(1),
                hasKalshi: hasKalshi,
                isThrottled: isThrottled,
                wLast: wLast,
                wMid: wMid,
                wLiq: wLiq
            });
        }
    });

    // Soft normalization - only apply 30% of adjustment to keep top candidates higher
    const NORMALIZATION_STRENGTH = 0.30; // 30% normalization, 70% raw values
    const total = aggregated.reduce((sum, c) => sum + c.probability, 0);
    if (total > 0) {
        aggregated.forEach(c => {
            const fullyNormalized = (c.probability / total) * 100;
            const adjustment = fullyNormalized - c.probability;
            c.probability = c.probability + (adjustment * NORMALIZATION_STRENGTH);
        });
        breakdowns.forEach(b => {
            const oldAggregate = parseFloat(b.aggregate);
            const fullyNormalized = (oldAggregate / total) * 100;
            const adjustment = fullyNormalized - oldAggregate;
            b.normalizedAggregate = (oldAggregate + (adjustment * NORMALIZATION_STRENGTH)).toFixed(1);
        });
    }

    aggregated.sort((a, b) => b.probability - a.probability);
    breakdowns.sort((a, b) => parseFloat(b.normalizedAggregate || b.aggregate) - parseFloat(a.normalizedAggregate || a.aggregate));

    // Store aggregated data for chart updates
    candidatesData = aggregated;

    renderBigOdds(aggregated);

    // Only render chart on first load (archive: chart data is static JSONL)
    if (!window._chartInitialized) {
        window._chartInitialized = true;
        renderMarketTrendChart(aggregated);
    }

    renderBreakdown(breakdowns);
    renderHeatmap(aggregated);
}

function renderBreakdown(breakdowns) {
    const container = document.getElementById('breakdownContent');

    container.innerHTML = breakdowns.map((b, idx) => {
        const asterisk = !b.hasKalshi ? '*' : '';
        const finalProb = b.normalizedAggregate || b.aggregate;

        let calculationHTML;
        if (b.hasKalshi) {
            const throttleNote = b.isThrottled ? `<div class="breakdown-line" style="margin-top: 0.4rem; color: var(--accent-color); font-size: 0.85em;"><em>Last price outside spread — weight throttled</em></div>` : '';
            calculationHTML = `
                <div class="breakdown-line"><strong>Manifold:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${b.manifoldProb}%</span></div>
                <div class="breakdown-line"><strong>Kalshi Last Price:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${b.kalshiLast}%</span>${b.isThrottled ? ' <span style="color: var(--accent-color); font-size: 0.85em;">(outside spread)</span>' : ''}</div>
                <div class="breakdown-line"><strong>Kalshi Midpoint:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${b.kalshiMid}%</span></div>
                <div class="breakdown-line"><strong>Kalshi Liquidity-Weighted:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${b.kalshiLiq}%</span></div>
                ${throttleNote}
                <div class="breakdown-line" style="margin-top: 0.8rem;">
                    <strong>Calculation:</strong><br>
                    (0.40 × ${b.manifoldProb}) + (${b.wLast} × ${b.kalshiLast}) + (${b.wMid} × ${b.kalshiMid}) + (${b.wLiq} × ${b.kalshiLiq})<br>
                    = ${b.manifoldContribution} + ${b.kalshiLastContribution} + ${b.kalshiMidContribution} + ${b.kalshiLiqContribution}
                </div>
                <div class="breakdown-result">= ${b.aggregate}%</div>
                ${b.normalizedAggregate ? `<div class="breakdown-line" style="margin-top: 0.5rem; color: var(--text-secondary);"><strong>Soft Normalized (30%):</strong> ${b.normalizedAggregate}%</div>` : ''}
            `;
        } else {
            calculationHTML = `
                <div class="breakdown-line"><strong>Manifold:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${b.manifoldProb}%</span></div>
                <div class="breakdown-line" style="color: var(--text-secondary);"><strong>Kalshi:</strong> No market available</div>
                <div class="breakdown-line" style="margin-top: 0.8rem;">
                    <strong>Calculation:</strong><br>
                    Using 100% of Manifold probability (no Kalshi data)<br>
                    = ${b.manifoldProb}%
                </div>
                ${b.normalizedAggregate ? `<div class="breakdown-line" style="margin-top: 0.5rem; color: var(--text-secondary);"><strong>Soft Normalized (30%):</strong> ${b.normalizedAggregate}%<br><em style="font-size: 0.85em;">Light normalization applied to prevent drift while preserving market values.</em></div>` : ''}
            `;
        }

        return `
        <div class="candidate-breakdown" id="breakdown-${idx}">
            <button class="breakdown-toggle" onclick="toggleBreakdown(${idx})">
                <span class="breakdown-header">${b.name}${asterisk}</span>
                <span class="breakdown-preview">${finalProb}%</span>
                <span class="breakdown-icon">+</span>
            </button>
            <div class="breakdown-details">
                ${calculationHTML}
            </div>
        </div>
        `;
    }).join('');
}

function toggleBreakdown(idx) {
    const breakdown = document.getElementById(`breakdown-${idx}`);
    breakdown.classList.toggle('open');
}

function toggleCalcFoldout() {
    const body = document.getElementById('calc-foldout-body');
    const arrow = document.getElementById('calc-foldout-arrow');
    const foldout = document.getElementById('calculationBreakdown');
    body.classList.toggle('open');
    arrow.classList.toggle('open');
    foldout.classList.toggle('active');
}

function renderHeatmap(candidates) {
    const container = document.getElementById('heatmapContent');
    if (!candidates || candidates.length < 2) {
        container.innerHTML = '<p style="color: var(--text-secondary);">Waiting for market data...</p>';
        return;
    }

    // Sort by probability descending, take top 6
    const sorted = candidates.slice().sort((a, b) => b.probability - a.probability).slice(0, 6);
    const n = sorted.length;

    // Compute conditional probability matrix
    // P(row=1st, col=2nd) = P(row wins) * P(col wins) / (1 - P(row wins))
    const matrix = [];
    let maxVal = 0;
    let topPair = { val: 0, i: 0, j: 0 };
    for (let i = 0; i < n; i++) {
        matrix[i] = [];
        const pWin = sorted[i].probability / 100;
        for (let j = 0; j < n; j++) {
            if (i === j) {
                matrix[i][j] = null;
            } else {
                const pOther = sorted[j].probability / 100;
                const denom = 1 - pWin;
                const val = denom > 0 ? (pWin * pOther / denom) * 100 : 0;
                matrix[i][j] = val;
                if (val > maxVal) { maxVal = val; topPair = { val, i, j }; }
            }
        }
    }

    // Color ramp: low values get subtle tint, high values get strong teal
    function cellStyle(val, isTop) {
        if (val === null) return '';
        const t = maxVal > 0 ? val / maxVal : 0;
        const alpha = 0.05 + t * 0.6;
        const borderGlow = isTop ? 'box-shadow: inset 0 0 0 1.5px rgba(49, 176, 181, 0.8);' : '';
        return `background: rgba(49, 176, 181, ${alpha.toFixed(3)}); ${borderGlow}`;
    }

    function textStyle(val) {
        if (val === null) return '';
        const t = maxVal > 0 ? val / maxVal : 0;
        if (t > 0.6) return 'color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,0.3);';
        if (t > 0.25) return 'color: rgba(255,255,255,0.9);';
        return 'color: var(--text-secondary);';
    }

    function lastName(name) {
        const parts = name.trim().split(' ');
        return parts[parts.length - 1];
    }

    // Build HTML
    let html = '';

    // Most likely result callout
    html += `<div style="display: flex; align-items: center; gap: 0.8rem; margin-bottom: 1.4rem; padding: 0.75rem 1rem; background: var(--bg-elevated); border-left: 3px solid var(--accent-gold);">`;
    html += `<span style="font-size: 0.78rem; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600;">Most likely result</span>`;
    html += `<span style="font-size: 0.95rem; color: var(--accent-blue); font-weight: 600;">${lastName(sorted[topPair.i].name)} 1st, ${lastName(sorted[topPair.j].name)} 2nd</span>`;
    html += `<span style="font-size: 0.85rem; color: var(--text-secondary);">${topPair.val.toFixed(1)}%</span>`;
    html += `</div>`;

    // Axis labels
    html += '<div class="heatmap-label-row">';
    html += '<span class="heatmap-axis-label">&#8595; Finishes 1st</span>';
    html += '<span class="heatmap-axis-label">Finishes 2nd &#8594;</span>';
    html += '</div>';

    // Table
    html += '<div class="heatmap-wrapper"><table class="heatmap-table"><thead><tr>';
    html += '<th class="heatmap-corner"></th>';
    for (let j = 0; j < n; j++) {
        html += `<th class="heatmap-col-header">${lastName(sorted[j].name)}</th>`;
    }
    html += '</tr></thead><tbody>';

    for (let i = 0; i < n; i++) {
        html += '<tr>';
        html += `<th class="heatmap-row-header">${lastName(sorted[i].name)}</th>`;
        for (let j = 0; j < n; j++) {
            const val = matrix[i][j];
            if (val === null) {
                html += '<td class="heatmap-diagonal">&mdash;</td>';
            } else {
                const isTop = (i === topPair.i && j === topPair.j);
                const display = val < 0.1 ? '<0.1' : val.toFixed(1);
                html += `<td style="${cellStyle(val, isTop)} ${textStyle(val)}" title="${sorted[i].name} 1st, ${sorted[j].name} 2nd: ${val.toFixed(2)}%">${display}%</td>`;
            }
        }
        html += '</tr>';
    }

    html += '</tbody></table></div>';
    container.innerHTML = html;
}

async function fetchManifoldData() {
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        const response = await fetch('/api/manifold', { signal: controller.signal });
        clearTimeout(timeout);
        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        // Extract answers with probabilities
        const answers = data.answers || [];
        const manifoldList = answers
            .map(a => ({
                name: a.text,
                probability: isExcludedCandidate(a.text) ? 0 : Math.round(a.probability * 1000) / 10,
                _excluded: isExcludedCandidate(a.text)
            }))
            .filter(a => a.name !== 'Other' && !a.name.toLowerCase().includes('schakowsky'))
            .sort((a, b) => b.probability - a.probability);

        // Store in lookup object (skip excluded candidates so they don't affect aggregate)
        manifoldList.forEach(c => {
            if (c._excluded) return;
            const key = normalizeCandidate(c.name);
            manifoldData[key] = {
                displayName: c.name,
                probability: c.probability
            };
        });

        renderManifoldExpanded(manifoldList);

        // Final archived update timestamp
        const updated = new Date(data.lastBetTime || data.lastUpdatedTime);
        manifoldLastUpdate = updated.getTime();
        document.getElementById('manifoldUpdated').textContent = `Final update ${formatCentralTime(updated, {
            month: 'short', day: 'numeric', year: 'numeric',
            hour: 'numeric', minute: '2-digit'
        })}`;

        // Calculate aggregate if both data sources are ready
        if (Object.keys(kalshiData).length > 0 && Object.keys(manifoldData).length > 0) {
            await calculateAggregate();
        } else {
            // Waiting for both data sources
        }

    } catch (error) {
        console.error('Error fetching Manifold data:', error);
        document.getElementById('bigOddsDisplay').innerHTML = '<p style="color: var(--text-secondary);">Error loading market data</p>';
    }
}

function renderBigOdds(candidates) {
    if (ELECTION_OVER) {
        // Override display for election results — data collection continues unaffected
        const photoEl = document.getElementById('leaderPhoto');
        const nameEl = document.getElementById('leaderName');
        const aggregateEl = document.getElementById('leaderAggregate');
        const manifoldEl = document.getElementById('leaderManifold');
        const kalshiLastEl = document.getElementById('leaderKalshiLast');
        const kalshiMidEl = document.getElementById('leaderKalshiMid');
        const kalshiLiqEl = document.getElementById('leaderKalshiLiq');

        const winnerImage = getCandidateImage(ELECTION_WINNER);
        if (winnerImage) {
            photoEl.src = winnerImage;
            photoEl.alt = ELECTION_WINNER;
            photoEl.style.display = 'block';
        }
        nameEl.textContent = ELECTION_WINNER;
        aggregateEl.textContent = '99.99%';

        // Show Manifold as closed, Kalshi as final
        manifoldEl.textContent = 'Closed';
        kalshiLastEl.textContent = '99.0%';
        kalshiMidEl.textContent = '99.0%';
        kalshiLiqEl.textContent = '99.0%';

        // Show other candidates at 0%
        const others = candidates.filter(c => c.name !== ELECTION_WINNER);
        const container = document.getElementById('bigOddsDisplay');
        container.innerHTML = others.slice(0, 5).map((c) => `
            <div class="big-odds-candidate">
                <span class="big-percent">0%</span>
                <span class="big-name">${c.name}</span>
            </div>
        `).join('');
        return;
    }

    // Update leader featured card
    if (candidates.length > 0) {
        const leader = candidates[0];
        const leaderImage = getCandidateImage(leader.name);
        const photoEl = document.getElementById('leaderPhoto');
        const nameEl = document.getElementById('leaderName');
        const aggregateEl = document.getElementById('leaderAggregate');
        const manifoldEl = document.getElementById('leaderManifold');
        const kalshiLastEl = document.getElementById('leaderKalshiLast');
        const kalshiMidEl = document.getElementById('leaderKalshiMid');
        const kalshiLiqEl = document.getElementById('leaderKalshiLiq');

        if (leaderImage) {
            photoEl.src = leaderImage;
            photoEl.alt = leader.name;
            photoEl.style.display = 'block';
        } else {
            photoEl.style.display = 'none';
        }
        nameEl.textContent = leader.name;
        aggregateEl.textContent = Math.round(leader.probability) + '%';

        // Get Manifold and Kalshi data for leader
        const leaderKey = normalizeCandidate(leader.name);
        const manifoldProb = manifoldData[leaderKey]?.probability || 0;
        const kalshiInfo = kalshiData[leaderKey] || {};
        const kalshiLast = kalshiInfo.last_price || 0;
        const kalshiMid = kalshiInfo.midpoint || 0;
        const kalshiLiq = kalshiInfo.kalshiLiquidity || 0;

        manifoldEl.textContent = manifoldProb > 0 ? manifoldProb.toFixed(1) + '%' : '--';
        kalshiLastEl.textContent = kalshiLast > 0 ? kalshiLast.toFixed(1) + '%' : '--';
        kalshiMidEl.textContent = kalshiMid > 0 ? kalshiMid.toFixed(1) + '%' : '--';
        kalshiLiqEl.textContent = kalshiLiq > 0 ? kalshiLiq.toFixed(1) + '%' : '--';
    }

    // Update other candidates grid (skip leader)
    const container = document.getElementById('bigOddsDisplay');
    container.innerHTML = candidates.slice(1, 6).map((c) => {
        const asterisk = !c.hasKalshi ? '*' : '';
        return `
        <div class="big-odds-candidate">
            <span class="big-percent">${Math.round(c.probability)}%</span>
            <span class="big-name">${c.name}${asterisk}</span>
        </div>
        `;
    }).join('');
}

function renderManifoldExpanded(candidates) {
    // Update preview
    const top = candidates[0];
    document.getElementById('manifoldPreview').textContent = `Top: ${top.name.split(' ')[1]} ${Math.round(top.probability)}%`;

    // Update expanded list
    const container = document.getElementById('manifoldCandidates');
    container.innerHTML = candidates.map(c => `
        <div class="market-candidate">
            <span class="candidate-name">${c.name}</span>
            <span class="candidate-odds">${c.probability}%</span>
        </div>
    `).join('');
}


// Known gap ranges (populated from API response)
let knownGaps = [];
// Interpolated/estimated data ranges (populated from API response)
let interpolatedRanges = [];

function isInGap(t0, t1) {
    // Check if the segment between t0 and t1 crosses a known data gap
    for (const gap of knownGaps) {
        // Segment crosses gap if it starts before gap ends and ends after gap starts
        if (t0 < gap.end && t1 > gap.start) {
            return true;
        }
    }
    return false;
}

async function renderMarketTrendChart(candidates) {
    const canvas = document.getElementById('marketTrendChart');
    if (!canvas) { console.error('Chart canvas not found'); return; }
    const ctx = canvas.getContext('2d');
    const period = currentChartPeriod || 'all';

    // Fetch chart data. Use prefetched promise on first load, otherwise fetch fresh.
    // ETag/304 optimization: only skip re-render when SAME period is already displayed.
    // Without the period check, switching All→7d→All would 304 and keep showing 7d data.
    let chartData = {};
    try {
        if (window._prefetchedChart && period === 'all') {
            const prefetched = await window._prefetchedChart;
            window._prefetchedChart = null; // Use only once
            if (prefetched) {
                chartData = prefetched;
            } else {
                const response = await fetch(`/api/snapshots/chart?period=${period}&epsilon=0.5`);
                const etag = response.headers.get('ETag');
                if (etag) window._chartEtags[period] = etag;
                chartData = await response.json();
            }
        } else {
            const headers = {};
            // Only use 304 when chart is already showing THIS period's data
            var samePeriodDisplayed = window._chartRenderedPeriod === period;
            if (samePeriodDisplayed && window._chartEtags && window._chartEtags[period]) {
                headers['If-None-Match'] = window._chartEtags[period];
            }
            const response = await fetch(`/api/snapshots/chart?period=${period}&epsilon=0.5`, { headers });
            if (response.status === 304) return; // Same period, same data — skip
            const etag = response.headers.get('ETag');
            if (etag) window._chartEtags[period] = etag;
            chartData = await response.json();
        }
    } catch (error) {
        console.error('Error fetching chart data:', error);
    }

    // If no candidates passed (early render before aggregate), derive from chart data
    // and also populate the leader card + big odds so users see data immediately
    if (!candidates || candidates.length === 0) {
        const snapshots = chartData.snapshots || [];
        if (snapshots.length > 0) {
            // Collect all candidates that ever appeared, using their peak probability for sort order
            const peakProb = {};
            for (const snap of snapshots) {
                for (const c of (snap.candidates || [])) {
                    if (!peakProb[c.name] || c.probability > peakProb[c.name]) {
                        peakProb[c.name] = c.probability;
                    }
                }
            }
            candidates = Object.entries(peakProb)
                .filter(([, p]) => p > 0)
                .sort((a, b) => b[1] - a[1])
                .map(([name, probability]) => ({ name, probability }));
        }
        if (!candidates || candidates.length === 0) return;

        // Show snapshot-derived odds immediately instead of "Loading..."
        // Live API data will overwrite these once both sources respond
        const nameEl = document.getElementById('leaderName');
        if (nameEl && nameEl.textContent === 'Loading...') {
            try {
                renderBigOdds(candidates);
                renderHeatmap(candidates);
            } catch (e) {
                console.error('Early render error:', e);
            }
        }
    }

    const snapshots = chartData.snapshots || [];
    // Parse gap ranges from backend (detected on raw data, not RDP-simplified)
    knownGaps = (chartData.gaps || []).map(g => ({
        start: new Date(g.start).getTime(),
        end: new Date(g.end).getTime()
    }));
    // Parse interpolated/estimated data ranges
    interpolatedRanges = (chartData.interpolated_ranges || []).map(r => ({
        start: new Date(r.start).getTime(),
        end: new Date(r.end).getTime()
    }));


    // Theme-aware chart colors
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    const chartTextColor = isDark ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.7)';
    const chartGridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
    const chartLegendColor = isDark ? 'rgba(255,255,255,0.8)' : 'rgba(0,0,0,0.7)';
    const tooltipBg = isDark ? 'rgba(0,0,0,0.9)' : 'rgba(30,30,30,0.9)';

    if (snapshots.length < 2) {
        if (marketChart) marketChart.destroy();
        ctx.font = '14px Arial';
        ctx.fillStyle = chartTextColor;
        ctx.textAlign = 'center';
        ctx.fillText('Collecting historical data... Check back soon for trend charts.', ctx.canvas.width / 2, ctx.canvas.height / 2);
        var loadingEl2 = document.getElementById('chartLoadingIndicator');
        if (loadingEl2) loadingEl2.style.display = 'none';
        return;
    }

    try {
    // Parse all timestamps to Date objects
    const parsedSnapshots = snapshots.map(s => {
        const ts = s.timestamp;
        const date = new Date(ts.endsWith('Z') ? ts : ts + 'Z');
        return { date, snapshot: s };
    }).filter(p => !isNaN(p.date.getTime()));


    // Candidate-aligned palette (matches model page tokens)
    const candidateColorMap = {
        'Daniel Biss': '#C41E3A',
        'Laura Fine': '#1E3A8A',
        'Kat Abughazaleh': '#2D6A4F',
        'Katheryn Abughazaleh': '#2D6A4F',
        'Jan Schakowsky': '#6B4C9A',
        'Mike Simmons': '#B8860B',
        'Benjamin Yassky': '#C75B12',
    };
    const fallbackColors = ['#31B0B5', '#40B040', '#F25446', '#F2C306', '#8B5CF6', '#9BA1A6'];

    // Build datasets with {x, y} point format for time scale
    const topCandidates = candidates.slice(0, 6);
    const datasets = topCandidates.map((c, idx) => {
        const data = [];
        parsedSnapshots.forEach(({ date, snapshot }) => {
            const candidateData = snapshot.candidates.find(sc => sc.name === c.name);
            if (candidateData) {
                data.push({ x: date.getTime(), y: candidateData.probability });
            }
        });

        const baseColor = candidateColorMap[c.name] || fallbackColors[idx % fallbackColors.length];
        const r = parseInt(baseColor.slice(1, 3), 16);
        const g = parseInt(baseColor.slice(3, 5), 16);
        const b = parseInt(baseColor.slice(5, 7), 16);
        const fadedColor = `rgba(${r}, ${g}, ${b}, 0.4)`;

        return {
            label: c.name.split(' ').pop() || c.name,
            data: data,
            borderColor: baseColor,
            backgroundColor: 'transparent',
            borderWidth: 2.5,
            cubicInterpolationMode: 'monotone',
            tension: 0.65,
            fill: false,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointBackgroundColor: baseColor,
            pointBorderColor: '#fff',
            pointBorderWidth: 1.5,
            spanGaps: true,
            segment: {
                borderColor: ctx2 => {
                    const p0 = ctx2.p0;
                    const p1 = ctx2.p1;
                    if (p0 && p1 && p0.parsed && p1.parsed) {
                        if (isInGap(p0.parsed.x, p1.parsed.x)) {
                            return fadedColor;
                        }
                    }
                    return baseColor;
                },
                borderDash: ctx2 => {
                    const p0 = ctx2.p0;
                    const p1 = ctx2.p1;
                    if (p0 && p1 && p0.parsed && p1.parsed) {
                        if (isInGap(p0.parsed.x, p1.parsed.x)) {
                            return [6, 4];
                        }
                    }
                    return [];
                }
            }
        };
    });

    if (marketChart) marketChart.destroy();

    // Determine time unit based on period
    let timeUnit, tooltipFormat;
    if (period === '1d') {
        timeUnit = 'hour';
        tooltipFormat = 'MMM d, h:mm a';
    } else if (period === '7d') {
        timeUnit = 'day';
        tooltipFormat = 'MMM d, h:mm a';
    } else {
        timeUnit = 'day';
        tooltipFormat = 'MMM d, yyyy';
    }

    // X-axis bounds: first to last data point (archive — never extend past last snapshot)
    const firstTime = parsedSnapshots[0].date.getTime();
    const nowTime = parsedSnapshots[parsedSnapshots.length - 1].date.getTime();

    // Interpolated data shaded region plugin
    const interpolatedRegionPlugin = {
        id: 'interpolatedRegion',
        beforeDraw(chart) {
            if (!interpolatedRanges.length) return;
            const xScale = chart.scales.x;
            if (!xScale) return;
            const chartArea = chart.chartArea;
            const ctx2 = chart.ctx;
            const isDarkNow = document.documentElement.getAttribute('data-theme') !== 'light';

            for (const range of interpolatedRanges) {
                // Skip ranges outside visible area
                if (range.end < xScale.min || range.start > xScale.max) continue;

                const xStart = xScale.getPixelForValue(Math.max(range.start, xScale.min));
                const xEnd = xScale.getPixelForValue(Math.min(range.end, xScale.max));
                const width = xEnd - xStart;
                if (width < 1) continue;

                ctx2.save();

                // Subtle shaded background
                ctx2.fillStyle = isDarkNow
                    ? 'rgba(255, 255, 255, 0.03)'
                    : 'rgba(0, 0, 0, 0.03)';
                ctx2.fillRect(xStart, chartArea.top, width, chartArea.bottom - chartArea.top);

                // Subtle border lines at edges
                ctx2.strokeStyle = isDarkNow
                    ? 'rgba(255, 255, 255, 0.12)'
                    : 'rgba(0, 0, 0, 0.10)';
                ctx2.lineWidth = 1;
                ctx2.setLineDash([4, 4]);
                ctx2.beginPath();
                ctx2.moveTo(xStart, chartArea.top);
                ctx2.lineTo(xStart, chartArea.bottom);
                ctx2.moveTo(xEnd, chartArea.top);
                ctx2.lineTo(xEnd, chartArea.bottom);
                ctx2.stroke();

                // Label below the chart area
                ctx2.setLineDash([]);
                ctx2.font = "9px 'Source Sans 3', sans-serif";
                ctx2.fillStyle = isDarkNow
                    ? 'rgba(255, 255, 255, 0.40)'
                    : 'rgba(0, 0, 0, 0.35)';
                ctx2.textAlign = 'center';
                const centerX = xStart + width / 2;
                ctx2.fillText('This data may not be fully accurate', centerX, chartArea.bottom + 30);

                ctx2.restore();
            }
        }
    };

    // Methodology update vertical line plugin
    const methodologyLinePlugin = {
        id: 'methodologyLine',
        afterDraw(chart) {
            const updateTimestamp = new Date('2026-02-06T03:30:00Z').getTime();
            const xScale = chart.scales.x;
            if (!xScale) return;
            const xMin = xScale.min;
            const xMax = xScale.max;
            if (updateTimestamp < xMin || updateTimestamp > xMax) return;

            const xPixel = xScale.getPixelForValue(updateTimestamp);
            const chartArea = chart.chartArea;
            const ctx2 = chart.ctx;
            const isDarkNow = document.documentElement.getAttribute('data-theme') !== 'light';

            ctx2.save();
            ctx2.beginPath();
            ctx2.setLineDash([6, 4]);
            ctx2.strokeStyle = isDarkNow ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.3)';
            ctx2.lineWidth = 1.5;
            ctx2.moveTo(xPixel, chartArea.top);
            ctx2.lineTo(xPixel, chartArea.bottom);
            ctx2.stroke();

            ctx2.setLineDash([]);
            ctx2.font = "10px 'Source Sans 3', sans-serif";
            ctx2.fillStyle = isDarkNow ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)';
            ctx2.textAlign = 'center';
            ctx2.fillText('Methodology update', xPixel, chartArea.top - 6);
            ctx2.restore();
        }
    };

    // Left-to-right reveal animation plugin
    const revealPlugin = {
        id: 'revealAnimation',
        _progress: 0,
        _startTime: null,
        _duration: 2000,
        beforeDraw(chart) {
            if (this._progress >= 1) return;
            if (!this._startTime) this._startTime = Date.now();
            const elapsed = Date.now() - this._startTime;
            // Ease-out cubic for smooth deceleration
            const t = Math.min(elapsed / this._duration, 1);
            this._progress = 1 - Math.pow(1 - t, 3);
            const { ctx: c, chartArea } = chart;
            const clipWidth = chartArea.left + (chartArea.right - chartArea.left) * this._progress;
            c.save();
            c.beginPath();
            c.rect(chartArea.left, chartArea.top - 10, clipWidth - chartArea.left, chartArea.bottom - chartArea.top + 30);
            c.clip();
        },
        afterDraw(chart) {
            if (this._progress >= 1) return;
            chart.ctx.restore();
            if (this._progress < 1) {
                requestAnimationFrame(() => chart.draw());
            }
        }
    };

    marketChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        plugins: [revealPlugin, interpolatedRegionPlugin, methodologyLinePlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            layout: {
                padding: {
                    bottom: interpolatedRanges.length > 0 ? 18 : 0
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        usePointStyle: false,
                        boxWidth: 12,
                        boxHeight: 12,
                        padding: 15,
                        color: chartLegendColor,
                        font: {
                            family: "'Source Sans 3', sans-serif",
                            size: 12,
                            weight: 'normal'
                        }
                    }
                },
                tooltip: {
                    backgroundColor: tooltipBg,
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    padding: 10,
                    cornerRadius: 0,
                    titleFont: {
                        family: "'Source Sans 3', sans-serif",
                        size: 12,
                        weight: 'bold'
                    },
                    bodyFont: {
                        family: "'Source Sans 3', sans-serif",
                        size: 11
                    },
                    callbacks: {
                        title: function(items) {
                            if (items.length > 0) {
                                const date = new Date(items[0].parsed.x);
                                return formatCentralTime(date, {
                                    month: 'short', day: 'numeric', year: 'numeric',
                                    hour: 'numeric', minute: '2-digit'
                                });
                            }
                            return '';
                        },
                        label: function(context) {
                            return `${context.dataset.label}: ${context.parsed.y?.toFixed(1) || '--'}%`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        stepSize: 10,
                        callback: (value) => value + '%',
                        color: chartTextColor,
                        font: {
                            family: "'Source Sans 3', sans-serif",
                            size: 11
                        },
                        padding: 8
                    },
                    grid: {
                        color: chartGridColor,
                        drawBorder: false,
                        lineWidth: 1
                    },
                    border: {
                        display: false
                    }
                },
                x: {
                    type: 'time',
                    min: firstTime,
                    max: nowTime,
                    time: {
                        unit: timeUnit,
                        tooltipFormat: tooltipFormat,
                        displayFormats: {
                            hour: 'h a',
                            day: 'MMM d'
                        }
                    },
                    ticks: {
                        color: chartTextColor,
                        autoSkip: true,
                        maxTicksLimit: period === '1d' ? 12 : (period === '7d' ? 7 : 10),
                        font: {
                            family: "'Source Sans 3', sans-serif",
                            size: 10
                        },
                        maxRotation: 0,
                        callback: function(value, index, ticks) {
                            const utcDate = new Date(value);
                            const formatter = new Intl.DateTimeFormat('en-US', {
                                timeZone: 'America/Chicago',
                                hour: timeUnit === 'hour' ? 'numeric' : undefined,
                                day: timeUnit === 'day' ? 'numeric' : undefined,
                                month: timeUnit === 'day' ? 'short' : undefined,
                                hour12: true
                            });
                            return formatter.format(utcDate);
                        }
                    },
                    grid: {
                        color: chartGridColor,
                        drawBorder: false
                    },
                    border: {
                        display: false
                    }
                }
            }
        }
    });

    // Track which period is displayed so 304 only fires for same-period refreshes
    window._chartRenderedPeriod = period;
    // Fade out loading indicator
    var loadingEl = document.getElementById('chartLoadingIndicator');
    if (loadingEl) {
        loadingEl.style.opacity = '0';
        setTimeout(function() { loadingEl.style.display = 'none'; }, 300);
    }

    } catch (chartError) {
        console.error('Chart rendering failed:', chartError);
        // Show error state instead of blank canvas
        try {
            ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
            ctx.font = '14px Arial';
            ctx.fillStyle = isDark ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.7)';
            ctx.textAlign = 'center';
            ctx.fillText('Chart temporarily unavailable. Retrying...', ctx.canvas.width / 2, ctx.canvas.height / 2);
        } catch (e) { /* canvas draw failed too */ }
        // Retry once after 5 seconds
        if (!window._chartRetried) {
            window._chartRetried = true;
            setTimeout(function() { window._chartRetried = false; renderMarketTrendChart(candidates); }, 5000);
        }
    }
}

async function fetchKalshiData() {
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        const response = await fetch('/api/kalshi', { signal: controller.signal });
        clearTimeout(timeout);
        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        // Extract markets - each market is a candidate
        const markets = data.markets || [];

        const kalshiList = [];

        markets.forEach(m => {
            // Extract candidate name from subtitle or title
            let displayName = m.subtitle || m.title;

            // Filter out Jan Schakowsky
            if (displayName.toLowerCase().includes('schakowsky')) {
                return;
            }

            // Excluded candidates: show in display at 0% but skip from aggregate data
            if (isExcludedCandidate(displayName)) {
                kalshiList.push({
                    name: cleanCandidateName(displayName),
                    probability: 0
                });
                return;
            }

            // Handle both old cent-based fields and new dollar-based fields
            const last_price = m.last_price != null ? m.last_price
                : (m.last_price_dollars != null ? parseFloat(m.last_price_dollars) * 100 : 0);
            const yes_bid = m.yes_bid != null ? m.yes_bid
                : (m.no_ask_dollars != null ? (1 - parseFloat(m.no_ask_dollars)) * 100 : 0);
            const yes_ask = m.yes_ask != null ? m.yes_ask
                : (m.no_bid_dollars != null ? (1 - parseFloat(m.no_bid_dollars)) * 100 : 0);

            // Only compute a real midpoint when both sides have orders.
            // If yes_bid is 0 (no buy-side interest), midpoint between 0
            // and the ask is meaningless — fall back to last_price.
            const midpoint = (yes_bid > 0 && yes_ask > 0) ? (yes_bid + yes_ask) / 2 : last_price;

            // Extract order book volume data
            const yes_bid_volume = m.yes_bid_size || 0;
            const no_bid_volume = m.no_bid_size || 0;

            if (last_price > 0 || midpoint > 0) {
                const key = normalizeCandidate(displayName);
                const localMid = midpoint;

                // Liquidity-weighted price based on last trade position within spread
                // Only use spread when both bid and ask exist
                const spread = (yes_bid > 0 && yes_ask > 0) ? (yes_ask - yes_bid) : 0;
                let kalshiLiquidity = localMid;


                if (spread > 0 && last_price > 0) {
                    // Liquidity weighting: where did the last trade occur within the spread?
                    // - Last price near ask (1.0) → buyers aggressive → shift up
                    // - Last price near bid (0.0) → sellers aggressive → shift down
                    // - Last price at mid (0.5) → balanced → no shift

                    // Position in spread: 0 = at bid, 0.5 = at mid, 1 = at ask
                    const positionInSpread = Math.max(0, Math.min(1, (last_price - yes_bid) / spread));

                    // Offset from midpoint: -0.5 to +0.5
                    const offsetFromMid = positionInSpread - 0.5;

                    // Spread dampening: wider spreads = less confidence in the signal
                    // At 0pp spread: factor = 1.0 (full shift potential)
                    // At 10pp spread: factor = 0.2 (heavily dampened)
                    const spreadFactor = Math.max(0.2, 1 - (spread / 10) * 0.8);

                    // Multiply by spread (not a fixed constant) so the shift is
                    // proportional to the spread width and can never leave [bid, ask].
                    const priceShift = offsetFromMid * spread * spreadFactor;

                    kalshiLiquidity = Math.max(yes_bid, Math.min(yes_ask, localMid + priceShift));

                } else {
                }

                kalshiData[key] = {
                    displayName: displayName,
                    last_price: last_price,
                    yes_bid: yes_bid,
                    yes_ask: yes_ask,
                    midpoint: midpoint,
                    yes_bid_volume: yes_bid_volume,
                    no_bid_volume: no_bid_volume,
                    kalshiLiquidity: kalshiLiquidity
                };

                kalshiList.push({
                    name: cleanCandidateName(displayName),
                    probability: Math.round(last_price * 10) / 10
                });
            }
        });

        kalshiList.sort((a, b) => b.probability - a.probability);
        renderKalshiExpanded(kalshiList);


        // Archive mode: show the race-call time rather than a rolling "now".
        kalshiLastUpdate = Date.UTC(2026, 2, 18, 2, 30);  // Mar 17 2026, 9:30 PM CT
        document.getElementById('kalshiUpdated').textContent = 'Final archived snapshot — Mar 17, 2026, 9:30 PM CT';

        // Calculate aggregate if both Manifold and Kalshi data are ready
        if (Object.keys(manifoldData).length > 0 && Object.keys(kalshiData).length > 0) {
            await calculateAggregate();
        } else {
            // Waiting for both data sources
        }

    } catch (error) {
        console.error('Error fetching Kalshi data:', error);
        document.getElementById('kalshiPreview').textContent = 'Error loading';
        document.getElementById('kalshiCandidates').innerHTML = '<p style="color: var(--text-secondary);">Error loading Kalshi data</p>';
    }
}

function renderKalshiExpanded(candidates) {
    if (candidates.length === 0) {
        document.getElementById('kalshiPreview').textContent = 'No data';
        return;
    }

    // Update preview - use last name only
    const top = candidates[0];
    const nameParts = top.name.split(' ');
    const topName = nameParts[nameParts.length - 1]; // Get last name
    document.getElementById('kalshiPreview').textContent = `Top: ${topName} ${Math.round(top.probability)}%`;

    // Update expanded list
    const container = document.getElementById('kalshiCandidates');
    container.innerHTML = candidates.map(c => `
        <div class="market-candidate">
            <span class="candidate-name">${c.name}</span>
            <span class="candidate-odds">${c.probability}%</span>
        </div>
    `).join('');
}

function setupPolling() {
    // Archive mode: render chart + winner from the last snapshot; no polling.
    renderMarketTrendChart(null);
    renderBigOdds([]);

    // One-shot fetch of the final cached Manifold/Kalshi responses to fill in
    // the expandable "Market Sources" panels with the last-known per-candidate
    // numbers. No setInterval — data is frozen as of March 17, 2026.
    fetchManifoldData();
    fetchKalshiData();
}

document.addEventListener('DOMContentLoaded', () => {
    // Set up chart toggle buttons
    var _chartLoading = false;
    document.querySelectorAll('.toggle-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            if (_chartLoading) return; // debounce
            var clicked = e.target.closest('.toggle-btn');
            if (!clicked || clicked.classList.contains('active')) return;

            // Update button states immediately
            document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
            clicked.classList.add('active');
            clicked.style.opacity = '0.6';

            currentChartPeriod = clicked.dataset.period;
            _chartLoading = true;

            try {
                await renderMarketTrendChart(candidatesData);
            } finally {
                _chartLoading = false;
                clicked.style.opacity = '';
            }
        });
    });

    setupPolling();
});


window.onThemeChange = function () {
    if (candidatesData && candidatesData.length > 0) {
        renderMarketTrendChart(candidatesData);
    }
};
