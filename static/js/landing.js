async function loadStats() {
    try {
        const resp = await fetch('/api/snapshots/count');
        const data = await resp.json();
        const snapshots = data.snapshots || data.count || 0;
        const dataPoints = data.data_points || 0;

        animateValue('dataPointCount', dataPoints);
        const snapshotEl = document.getElementById('snapshotCount');
        if (snapshotEl) {
            snapshotEl.textContent = snapshots.toLocaleString();
        }
    } catch (e) {
        console.error('Failed to fetch stats:', e);
    }
}

function animateValue(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el || !target) return;

    const duration = 1500;
    const startTime = Date.now();

    function easeOut(t) {
        return 1 - Math.pow(1 - t, 3);
    }

    function update() {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const current = Math.floor(easeOut(progress) * target);
        el.textContent = current.toLocaleString();

        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = target.toLocaleString();
        }
    }

    update();
}

window.addEventListener('load', loadStats);
