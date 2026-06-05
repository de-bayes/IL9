// Email subscription form and modal removed in archive mode.

    // Fundraising callout popup
    async function initFundraisingCallout() {
        const callout = document.getElementById('fundraisingCallout');
        const closeBtn = document.getElementById('closeCallout');

        // Don't show on homepage
        if (window.location.pathname === '/' || window.location.pathname === '') {
            return;
        }

        // Check if already dismissed this session
        if (localStorage.getItem('il9-callout-dismissed') === 'true') {
            return;
        }

        // Archive mode: sum of candidates' final reported totals, no extrapolation.
        try {
            const resp = await fetch('/api/fec/candidates');
            const data = await resp.json();
            const candidates = data.candidates || data;

            if (!Array.isArray(candidates)) return;

            let totalRaised = 0;
            for (let i = 0; i < candidates.length; i++) {
                totalRaised += candidates[i].total_raised || 0;
            }

            const formatted = new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD',
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            }).format(totalRaised);

            document.getElementById('candidatesTotal').textContent = formatted;

            // Show popup after a short delay
            setTimeout(() => {
                callout.classList.add('show');
            }, 800);

        } catch (e) {
            console.error('Failed to load fundraising data:', e);
        }

        // Close button handler
        closeBtn.addEventListener('click', () => {
            callout.classList.remove('show');
            localStorage.setItem('il9-callout-dismissed', 'true');
        });

        // Skip/Maybe Later button
        const skipBtn = document.getElementById('skipCallout');
        if (skipBtn) {
            skipBtn.addEventListener('click', () => {
                callout.classList.remove('show');
                localStorage.setItem('il9-callout-dismissed', 'true');
            });
        }

        // Close on overlay click
        callout.addEventListener('click', (e) => {
            if (e.target === callout) {
                callout.classList.remove('show');
                localStorage.setItem('il9-callout-dismissed', 'true');
            }
        });
    }

    // Initialize fundraising callout on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initFundraisingCallout);
    } else {
        initFundraisingCallout();
    }
