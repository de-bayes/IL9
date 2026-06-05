window._chartEtags = {};  // period -> etag for conditional requests
        window._chartRenderedPeriod = null;  // tracks which period is currently displayed
        // Prefetch with 15-second timeout so page isn't stuck waiting on slow cold-cache
        window._prefetchedChart = (function() {
            var controller = new AbortController();
            var timeoutId = setTimeout(function() { controller.abort(); }, 15000);
            return fetch('/api/snapshots/chart?period=all&epsilon=0.5', { signal: controller.signal })
                .then(function(r) {
                    clearTimeout(timeoutId);
                    var etag = r.headers.get('ETag');
                    if (etag) window._chartEtags['all'] = etag;
                    return r.json();
                })
                .catch(function() { clearTimeout(timeoutId); return null; });
        })();
