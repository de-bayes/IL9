/* /small-model exhibits. Fig 1: interactive SVG choropleth with hover cards
   and a click-to-pin detail card (ecological turnout estimates, with the
   caveat attached). Fig 2: precinct beeswarm. Fig 3: 10,000 simulations as
   dots with a scenario-map hover. Data: { precincts: [...], sims: [...] }. */
(function () {
  'use strict';

  var mapWrap = document.getElementById('sm-map');
  if (!mapWrap || !window.fetch) return;

  var BLUE = [53, 97, 143],
    RED = [180, 83, 79],
    PAPER = [242, 239, 233];
  var CAND_D = 'Daniel Biss',
    CAND_R = 'John Elleson';
  var D_COLOR = 'rgb(53,97,143)',
    R_COLOR = 'rgb(180,83,79)';
  var SERIF = "'Lora', Georgia, serif";
  var MEDIAN_SHARE = 73.9;

  function ramp(margin) {
    var t = Math.max(-1, Math.min(1, margin / 50));
    var from = PAPER,
      to = t < 0 ? RED : BLUE,
      f = Math.pow(Math.abs(t), 0.72);
    var c = [0, 0, 0];
    for (var i = 0; i < 3; i++) c[i] = Math.round(from[i] + (to[i] - from[i]) * f);
    return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
  }

  function mulberry(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      var z = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      z = (z + Math.imul(z ^ (z >>> 7), 61 | z)) ^ z;
      return ((z ^ (z >>> 14)) >>> 0) / 4294967296;
    };
  }

  var fontsReady =
    document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();

  Promise.all([
    fetch('/static/sm/il9-model.json?v=7').then(function (r) {
      return r.json();
    }),
    fontsReady,
  ])
    .then(function (loaded) {
      var precincts = loaded[0].precincts;
      buildMap(precincts);
      buildSwarm(precincts);
      var shares = loaded[0].sims.map(function (s) {
        return s[0];
      });
      buildSims(shares, precincts);
      buildScenario(shares);
      buildOutcomes(shares, loaded[0].district);
      buildClosest(precincts);
    })
    .catch(function () {
      /* the figures degrade to their captions; nothing to recover */
    });

  /* ------------------------------------------------------------------ */
  /* shared: the hover/detail card                                       */
  /* ------------------------------------------------------------------ */

  function cardRow(color, name, pct) {
    return (
      '<div class="sm-tip__row"><span class="sm-tip__dot" style="background:' +
      color +
      '"></span><span class="sm-tip__name">' +
      name +
      '</span><span class="sm-tip__pct">' +
      pct.toFixed(1) +
      '%</span></div>'
    );
  }

  function cardBasics(p) {
    var dPct = p.s26,
      rPct = Math.round((100 - dPct) * 10) / 10;
    var margin = Math.round((dPct - rPct) * 10) / 10;
    var lead = margin >= 0 ? 'D+' + margin.toFixed(1) : 'R+' + Math.abs(margin).toFixed(1);
    return (
      '<p class="sm-tip__place">' +
      p.n +
      ' <small>' +
      p.c +
      ' County</small></p>' +
      cardRow(D_COLOR, CAND_D, dPct) +
      cardRow(R_COLOR, CAND_R, rPct) +
      '<div class="sm-tip__foot"><span>&asymp;' +
      p.t.toLocaleString() +
      ' ballots</span><span class="sm-tip__margin" style="color:' +
      (margin >= 0 ? D_COLOR : R_COLOR) +
      '">' +
      lead +
      '</span></div>'
    );
  }

  function drawPrecinctDist(cv, p) {
    var ctx = cv.getContext('2d');
    ctx.scale(2, 2);
    var W = cv.width / 2,
      H = cv.height / 2;
    var rand = mulberry((p.n.length * 7919 + p.t) | 0);
    function gauss() {
      return (rand() + rand() + rand() - 1.5) * 2;
    }
    var s24 = Math.min(99, Math.max(1, p.s24));
    var bl = Math.log(s24 / (100 - s24));
    var draws = [];
    for (var i = 0; i < 240; i++) {
      var u = rand(),
        margin = u < 0.4 ? 9.5 : u < 0.75 ? 7.0 : 4.0;
      var env = 50 + margin / 2 - 48.6 + gauss() * 2.5;
      var shock = env + gauss() * 1.5 + gauss() * 3;
      draws.push(100 / (1 + Math.exp(-(bl + shock / 25))));
    }
    draws.sort(function (a, b) {
      return a - b;
    });
    var lo = draws[0],
      hi = draws[draws.length - 1];
    var base = H - 12;
    var NB = 40;
    var BIN = (hi - lo) / NB || 1;
    var counts = {},
      tallest = 1;
    draws.forEach(function (v) {
      var k = Math.min(NB, Math.round((v - lo) / BIN));
      counts[k] = (counts[k] || 0) + 1;
      if (counts[k] > tallest) tallest = counts[k];
    });
    // dots stacked per bin, sized so the tallest stack fits the strip
    var r = Math.min(2.1, ((W - 12) / NB) * 0.42, (base - 4) / (2 * tallest));
    Object.keys(counts).forEach(function (k) {
      var x = 6 + (k / NB) * (W - 12);
      for (var j = 0; j < counts[k]; j++) {
        ctx.beginPath();
        ctx.arc(x, base - r - j * 2 * r, r, 0, Math.PI * 2);
        ctx.fillStyle = ramp(2 * (lo + k * BIN) - 100);
        ctx.globalAlpha = 0.9;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    });
    var med = draws[Math.floor(draws.length / 2)];
    ctx.font = "italic 500 8.5px 'Lora', Georgia, serif";
    ctx.fillStyle = 'rgba(20,22,26,0.6)';
    ctx.textAlign = 'left';
    ctx.fillText(lo.toFixed(0) + '%', 4, H - 2);
    ctx.textAlign = 'right';
    ctx.fillText(hi.toFixed(0) + '%', W - 4, H - 2);
    ctx.textAlign = 'center';
    ctx.fillText('median ' + med.toFixed(1) + '%', W / 2, H - 2);
  }

  function cardDetail(p) {
    var retention = Math.round((100 * p.t) / p.t24);
    return (
      cardBasics(p) +
      '<div class="sm-tip__detail">' +
      '2024: ' +
      p.s24.toFixed(1) +
      '% D two-party on ' +
      p.t24.toLocaleString() +
      ' ballots (' +
      retention +
      '% expected to return)<br>' +
      'type: ' +
      p.cl +
      '<br>' +
      'voting-age mix: ' +
      p.vap.w +
      '% white &middot; ' +
      p.vap.h +
      '% Hispanic &middot; ' +
      p.vap.b +
      '% Black &middot; ' +
      p.vap.a +
      '% Asian<br>' +
      'est. ballots cast: ' +
      p.ei.w +
      '% white &middot; ' +
      p.ei.h +
      '% Hispanic &middot; ' +
      p.ei.b +
      '% Black &middot; ' +
      p.ei.a +
      '% Asian &middot; ' +
      p.ei.o +
      '% other' +
      '</div>' +
      '<canvas class="sm-tip__dist" width="560" height="96" aria-label="This precinct across the simulations"></canvas>' +
      '<p class="sm-tip__caveat">Ecological estimates: about places, not people. A one-night toy; the gold standard lives at VoteHub.</p>'
    );
  }

  var ALL_TIPS = [];
  document.addEventListener('click', function (e) {
    ALL_TIPS.forEach(function (entry) {
      if (!entry.container.contains(e.target)) entry.tip.unpin();
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    ALL_TIPS.forEach(function (entry) {
      entry.tip.unpin();
    });
  });

  function makeTip(container) {
    var tip = document.createElement('div');
    tip.className = 'sm-tip';
    tip.setAttribute('aria-hidden', 'true');
    container.appendChild(tip);
    var pinned = false;
    var api = {
      hover: function (p, e) {
        if (pinned) return;
        tip.classList.remove('sm-tip--pin');
        tip.innerHTML = cardBasics(p);
        this.place(e);
        tip.classList.add('is-on');
        tip.setAttribute('aria-hidden', 'false');
      },
      pin: function (p, e) {
        pinned = true;
        tip.classList.add('sm-tip--pin');
        tip.innerHTML = cardDetail(p);
        var dist = tip.querySelector('.sm-tip__dist');
        if (dist && dist.getContext) drawPrecinctDist(dist, p);
        this.place(e);
        tip.classList.add('is-on');
        tip.setAttribute('aria-hidden', 'false');
      },
      unpin: function () {
        pinned = false;
        tip.classList.remove('is-on', 'sm-tip--pin');
        tip.setAttribute('aria-hidden', 'true');
      },
      place: function (e) {
        var box = container.getBoundingClientRect();
        var x = e.clientX - box.left,
          y = e.clientY - box.top;
        var w = tip.offsetWidth || 260,
          h = tip.offsetHeight || 130;
        tip.style.left = Math.max(0, Math.min(x + 14, box.width - w)) + 'px';
        tip.style.top = Math.max(0, Math.min(y + 14, box.height - h)) + 'px';
      },
      hide: function () {
        if (!pinned) {
          tip.classList.remove('is-on');
          tip.setAttribute('aria-hidden', 'true');
        }
      },
    };
    ALL_TIPS.push({ tip: api, container: container });
    return api;
  }

  /* ------------------------------------------------------------------ */
  /* fig 1: the map                                                      */
  /* ------------------------------------------------------------------ */

  function bounds(precincts) {
    var b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    precincts.forEach(function (p) {
      p.g.forEach(function (ring) {
        ring.forEach(function (pt) {
          if (pt[0] < b.x0) b.x0 = pt[0];
          if (pt[0] > b.x1) b.x1 = pt[0];
          if (pt[1] < b.y0) b.y0 = pt[1];
          if (pt[1] > b.y1) b.y1 = pt[1];
        });
      });
    });
    return b;
  }

  function projector(b, w, h, pad) {
    var kx = Math.cos((((b.y0 + b.y1) / 2) * Math.PI) / 180);
    var s = Math.min((w - 2 * pad) / ((b.x1 - b.x0) * kx), (h - 2 * pad) / (b.y1 - b.y0));
    var ox = (w - (b.x1 - b.x0) * kx * s) / 2,
      oy = (h - (b.y1 - b.y0) * s) / 2;
    return function (pt) {
      return [ox + (pt[0] - b.x0) * kx * s, oy + (b.y1 - pt[1]) * s];
    };
  }

  function buildMap(precincts) {
    var W = 1040,
      H = 660;
    var proj = projector(bounds(precincts), W, H, 8);
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    var tip = makeTip(mapWrap);

    precincts.forEach(function (p) {
      var d = '';
      p.g.forEach(function (ring) {
        ring.forEach(function (pt, i) {
          var xy = proj(pt);
          d += (i === 0 ? 'M' : 'L') + xy[0].toFixed(1) + ' ' + xy[1].toFixed(1);
        });
        d += 'Z';
      });
      var path = document.createElementNS(svgNS, 'path');
      path.setAttribute('d', d);
      path.setAttribute('fill', ramp(2 * p.s26 - 100));
      path.setAttribute('fill-rule', 'evenodd');
      path.addEventListener('mousemove', function (e) {
        tip.hover(p, e);
      });
      path.addEventListener('mouseleave', function () {
        tip.hide();
      });
      path.addEventListener('click', function (e) {
        tip.unpin();
        tip.pin(p, e);
        e.stopPropagation();
      });
      path.setAttribute('tabindex', '0');
      // a mouse click must not focus the path (some browsers ring any
      // focused SVG element); keyboard tabbing is unaffected
      path.addEventListener('mousedown', function (e) {
        e.preventDefault();
      });
      var pm = Math.round((2 * p.s26 - 100) * 10) / 10;
      path.setAttribute(
        'aria-label',
        p.n +
          ', ' +
          p.c +
          ' County, ' +
          (pm >= 0 ? 'D+' + pm : 'R+' + -pm) +
          ', about ' +
          p.t +
          ' ballots'
      );
      path.addEventListener('focus', function () {
        var r = path.getBoundingClientRect();
        tip.hover(p, { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
      });
      path.addEventListener('blur', function () {
        tip.hide();
      });
      path.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        var r = path.getBoundingClientRect();
        tip.unpin();
        tip.pin(p, { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
      });
      svg.appendChild(path);
    });

    mapWrap.appendChild(svg);
  }

  /* ------------------------------------------------------------------ */
  /* fig 2: the precinct beeswarm                                        */
  /* ------------------------------------------------------------------ */

  function buildSwarm(precincts) {
    var cv = document.getElementById('sm-swarm');
    var wrap = document.getElementById('sm-swarm-wrap');
    if (!cv || !cv.getContext || !wrap) return;
    var ctx = cv.getContext('2d');
    ctx.scale(2, 2);
    var W = cv.width / 2,
      H = cv.height / 2;
    var M0 = -22,
      M1 = 92;
    var baseline = H - 56;

    function xOf(m) {
      return 30 + ((m - M0) / (M1 - M0)) * (W - 60);
    }

    var maxT = 0;
    precincts.forEach(function (p) {
      if (p.t > maxT) maxT = p.t;
    });

    var dots = [];
    precincts
      .slice()
      .sort(function (a, bb) {
        return bb.t - a.t;
      })
      .forEach(function (p, idx) {
        var m = 2 * p.s26 - 100;
        var r = 2.2 + 7.5 * Math.sqrt(p.t / maxT);
        // a deterministic wiggle breaks the vertical strands without physics
        var jitter = ((((idx + 1) * 2654435761) % 997) / 997 - 0.5) * 7;
        var x = xOf(m) + jitter,
          y = baseline - r;
        for (var guard = 0; guard < 4000; guard++) {
          var hit = null;
          for (var i = 0; i < dots.length; i++) {
            var dx = x - dots[i].x,
              dy = y - dots[i].y,
              rr = r + dots[i].r + 0.7;
            if (dx * dx + dy * dy < rr * rr) {
              hit = dots[i];
              break;
            }
          }
          if (!hit) break;
          y =
            hit.y -
            Math.sqrt(
              Math.max(1, (r + hit.r + 0.7) * (r + hit.r + 0.7) - (x - hit.x) * (x - hit.x))
            ) -
            0.2;
        }
        dots.push({ x: x, y: y, r: r, p: p, m: m });
      });

    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = 'rgba(20,22,26,0.25)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(16, baseline + 12);
      ctx.lineTo(W - 16, baseline + 12);
      ctx.stroke();
      ctx.font = 'italic 500 14px ' + SERIF;
      ctx.textAlign = 'center';
      [-20, 0, 20, 40, 60, 80].forEach(function (m) {
        var label = m === 0 ? 'even' : m < 0 ? 'R+' + -m : 'D+' + m;
        ctx.fillStyle = m === 0 ? 'rgba(20,22,26,0.75)' : 'rgba(20,22,26,0.62)';
        ctx.fillText(label, xOf(m), baseline + 34);
        if (m === 0) {
          ctx.strokeStyle = 'rgba(20,22,26,0.3)';
          ctx.setLineDash([3, 4]);
          ctx.beginPath();
          ctx.moveTo(xOf(0), 24);
          ctx.lineTo(xOf(0), baseline + 12);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      });

      dots.forEach(function (d) {
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fillStyle = ramp(d.m);
        ctx.globalAlpha = 0.92;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = '#fbfbfc';
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });

      var medianMargin = 2 * MEDIAN_SHARE - 100;
      var cxx = xOf(medianMargin);
      ctx.strokeStyle = 'rgba(20,22,26,0.55)';
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(cxx, 20);
      ctx.lineTo(cxx, baseline + 12);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(251,251,252,0.85)';
      roundRect(ctx, cxx - 120, 24, 240, 62, 8);
      ctx.fill();
      ctx.fillStyle = D_COLOR;
      ctx.font = '600 32px ' + SERIF;
      ctx.fillText('D+' + medianMargin.toFixed(1), cxx, 58);
      ctx.fillStyle = 'rgba(20,22,26,0.66)';
      ctx.font = 'italic 500 13px ' + SERIF;
      ctx.fillText('median projected margin', cxx, 78);
    }

    draw();

    var tip = makeTip(wrap);
    function dotAt(e) {
      var box = cv.getBoundingClientRect();
      var mx = ((e.clientX - box.left) * W) / box.width,
        my = ((e.clientY - box.top) * H) / box.height;
      var best = null,
        bestD = Infinity;
      for (var i = 0; i < dots.length; i++) {
        var dx = mx - dots[i].x,
          dy = my - dots[i].y,
          d2 = dx * dx + dy * dy;
        if (d2 < bestD) {
          bestD = d2;
          best = dots[i];
        }
      }
      return best && Math.sqrt(bestD) <= best.r + 6 ? best : null;
    }
    cv.addEventListener('mousemove', function (e) {
      var d = dotAt(e);
      if (!d) {
        tip.hide();
        return;
      }
      tip.hover(d.p, e);
    });
    cv.addEventListener('mouseleave', function () {
      tip.hide();
    });
    cv.addEventListener('click', function (e) {
      var d = dotAt(e);
      if (d) {
        tip.unpin();
        tip.pin(d.p, e);
        e.stopPropagation();
      }
    });
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  /* ------------------------------------------------------------------ */
  /* the closest precincts: median + simulated range, dumbbell rows      */
  /* ------------------------------------------------------------------ */

  function precinctDraws(p, n) {
    var rand = mulberry((p.n.length * 7919 + p.t) | 0);
    function gauss() {
      return (rand() + rand() + rand() - 1.5) * 2;
    }
    var s24 = Math.min(99, Math.max(1, p.s24));
    var bl = Math.log(s24 / (100 - s24));
    var draws = [];
    for (var i = 0; i < n; i++) {
      var u = rand(),
        margin = u < 0.4 ? 9.5 : u < 0.75 ? 7.0 : 4.0;
      var env = 50 + margin / 2 - 48.6 + gauss() * 2.5;
      var shock = env + gauss() * 1.5 + gauss() * 3;
      draws.push(100 / (1 + Math.exp(-(bl + shock / 25))));
    }
    return draws.sort(function (a, b) {
      return a - b;
    });
  }

  function buildClosest(precincts) {
    var box = document.getElementById('sm-closest');
    if (!box) return;
    var rows = precincts
      .slice()
      .sort(function (a, b) {
        return Math.abs(2 * a.s26 - 100) - Math.abs(2 * b.s26 - 100);
      })
      .slice(0, 8);
    var DOM = 14; // track domain: R+14 to D+14
    rows.forEach(function (p) {
      var draws = precinctDraws(p, 240);
      var q10 = 2 * draws[Math.floor(draws.length * 0.1)] - 100;
      var q90 = 2 * draws[Math.floor(draws.length * 0.9)] - 100;
      var med = 2 * p.s26 - 100;
      function pos(m) {
        return 50 + (Math.max(-DOM, Math.min(DOM, m)) / DOM) * 50;
      }
      /* 538-style: two solid rectangles meeting at the even line */
      var lo = pos(Math.min(q10, q90)),
        hi = pos(Math.max(q10, q90));
      var bars = '';
      if (lo < 50)
        bars +=
          '<span class="sm-close-row__bar" style="left:' +
          lo.toFixed(1) +
          '%;right:' +
          (100 - Math.min(hi, 50)).toFixed(1) +
          '%;background:#b4534f"></span>';
      if (hi > 50)
        bars +=
          '<span class="sm-close-row__bar" style="left:' +
          Math.max(lo, 50).toFixed(1) +
          '%;right:' +
          (100 - hi).toFixed(1) +
          '%;background:#35618f"></span>';
      var lead = med >= 0 ? 'D+' + med.toFixed(1) : 'R+' + Math.abs(med).toFixed(1);
      var row = document.createElement('div');
      row.className = 'sm-close-row';
      row.innerHTML =
        '<span class="sm-close-row__who">' +
        p.n +
        '<small>' +
        p.c +
        ' County</small></span>' +
        '<span class="sm-close-row__track">' +
        bars +
        '<span class="sm-close-row__even"></span>' +
        '<span class="sm-close-row__dot" style="left:' +
        pos(med).toFixed(1) +
        '%;border-color:' +
        (med >= 0 ? '#35618f' : '#b4534f') +
        '"></span></span>' +
        '<span class="sm-close-row__m" style="color:' +
        (med >= 0 ? '#35618f' : '#b4534f') +
        '">' +
        lead +
        '</span>';
      box.appendChild(row);
    });
  }

  /* ------------------------------------------------------------------ */
  /* table 1: unusual and not-so-unusual outcomes                        */
  /* ------------------------------------------------------------------ */

  function buildOutcomes(shares, district) {
    var list = document.getElementById('sm-outcomes-list');
    if (!list || !district) return;

    function pOf(test) {
      var n = 0;
      for (var i = 0; i < shares.length; i++) if (test(shares[i])) n++;
      return n / shares.length;
    }
    function fmt(p) {
      var n = Math.round(p * 100);
      if (n < 1) return '<1 out of 100';
      if (n > 99) return '>99 out of 100';
      return n + ' out of 100';
    }

    var rows = [
      { head: 'the chances, from 10,000 simulations' },
      { label: 'Biss wins', odds: '>99 out of 100', tone: 'd' },
      {
        label: 'Biss clears three quarters of the two-party vote',
        odds: fmt(pOf((v) => v >= 75)),
        tone: 'd',
      },
      {
        label: 'Biss beats the top of his 80% interval (76.2%)',
        odds: fmt(pOf((v) => v > 76.2)),
        tone: 'd',
      },
      {
        label: 'The district lands within a point of the median',
        odds: fmt(pOf((v) => v >= 73 && v < 75)),
        tone: 'n',
      },
      { label: 'Biss comes in under 72%', odds: fmt(pOf((v) => v < 72)), tone: 'r' },
      {
        label: 'Elleson cracks 30% of the two-party vote',
        odds: fmt(pOf((v) => v < 70)),
        tone: 'r',
      },
      { label: 'Elleson wins', odds: '<1 out of 100', tone: 'r' },
      { head: 'the electorate, from the ecological turnout estimates' },
      { label: 'Ballots cast by white voters', odds: district.ei.w + ' of 100', tone: 'n' },
      { label: 'Ballots cast by Black voters', odds: district.ei.b + ' of 100', tone: 'n' },
      { label: 'Ballots cast by Asian voters', odds: district.ei.a + ' of 100', tone: 'n' },
      { label: 'Ballots cast by Hispanic voters', odds: district.ei.h + ' of 100', tone: 'n' },
      {
        label: '2024 voters expected to vote again in 2026',
        odds: district.ret + ' of 100',
        tone: 'n',
      },
    ];

    rows.forEach(function (r) {
      var div = document.createElement('div');
      if (r.head) {
        div.className = 'sm-outcome sm-outcome--head';
        div.innerHTML = '<span>' + r.head + '</span>';
      } else {
        div.className = 'sm-outcome';
        div.innerHTML =
          '<span class="sm-outcome__label">' +
          r.label +
          '</span><span class="sm-outcome__odds sm-outcome__odds--' +
          r.tone +
          '">' +
          r.odds +
          '</span>';
      }
      list.appendChild(div);
    });
  }

  /* ------------------------------------------------------------------ */
  /* the scenario box: 538-style odds, cycled from the simulation draws   */
  /* ------------------------------------------------------------------ */

  function buildScenario(sims) {
    var box = document.getElementById('sm-scenario');
    var oddsEl = document.getElementById('sm-scenario-odds');
    var whatEl = document.getElementById('sm-scenario-what');
    var btn = document.getElementById('sm-scenario-btn');
    if (!box || !oddsEl || !whatEl || !btn || !sims.length) return;

    function pOf(test) {
      var n = 0;
      for (var i = 0; i < sims.length; i++) if (test(sims[i])) n++;
      return n / sims.length;
    }

    function odds(p) {
      var n = Math.round(p * 100);
      if (n < 1) return 'less than 1-in-100';
      if (n > 99) return 'better than 99-in-100';
      return n + '-in-100';
    }

    var SCENARIOS = [
      { what: 'Biss clears three quarters of the two-party vote', p: pOf((v) => v >= 75) },
      { what: 'Biss beats the top of his own 80% interval (76.2%)', p: pOf((v) => v > 76.2) },
      { what: 'Elleson holds Biss under 71%', p: pOf((v) => v < 71) },
      { what: 'Biss comes in under 72% of the two-party vote', p: pOf((v) => v < 72) },
      {
        what: 'the district lands within a point of the median (73 to 75)',
        p: pOf((v) => v >= 73 && v < 75),
      },
      { what: 'the margin tops D+50', p: pOf((v) => 2 * v - 100 > 50) },
      { what: 'the margin lands between D+40 and D+50', p: pOf((v) => v >= 70 && v < 75) },
      { what: 'Biss lands outside his own 80% interval', p: pOf((v) => v < 71.3 || v > 76.2) },
      { what: 'Biss improves on the 2024 two-party baseline (68.3%)', p: pOf((v) => v > 68.3) },
      { what: 'Elleson cracks 30% of the two-party vote', p: pOf((v) => v < 70) },
      { what: 'Biss doubles a 25-point win (D+50 or better)', p: pOf((v) => v >= 75) },
      {
        what: 'the model lands within half a point of its median',
        p: pOf((v) => Math.abs(v - 73.9) <= 0.5),
      },
      { what: 'Biss wins', p: 1 },
      { what: 'Elleson has a better night than any of the 10,000 simulations', p: 0 },
    ];

    var last = -1;
    function show() {
      var i;
      do {
        i = Math.floor(Math.random() * SCENARIOS.length);
      } while (i === last);
      last = i;
      oddsEl.textContent = odds(SCENARIOS[i].p);
      whatEl.textContent = SCENARIOS[i].what;
    }
    show();
    btn.addEventListener('click', show);
  }

  /* ------------------------------------------------------------------ */
  /* fig 3: 10,000 simulations as dots                                   */
  /* ------------------------------------------------------------------ */

  function buildSims(sims, precincts) {
    var cv = document.getElementById('sm-sims');
    var wrap = document.getElementById('sm-sims-wrap');
    if (!cv || !cv.getContext || !sims || !sims.length) return;
    var ctx = cv.getContext('2d');
    ctx.scale(2, 2);
    var W = cv.width / 2,
      H = cv.height / 2;
    var S0 = 66,
      S1 = 78;
    var baseline = H - 52;

    function xOf(s) {
      return 30 + ((s - S0) / (S1 - S0)) * (W - 60);
    }

    // bin the draws and stack a dot per draw; the radius is set so both the
    // bins tile the axis and the tallest stack still fits the canvas
    var BIN = 0.15,
      counts = {},
      tallest = 0;
    sims.forEach(function (s) {
      var k = Math.round(s / BIN);
      counts[k] = (counts[k] || 0) + 1;
      if (counts[k] > tallest) tallest = counts[k];
    });
    var r = Math.min((0.5 * BIN * (W - 60)) / (S1 - S0) - 0.2, (baseline - 100) / (2 * tallest));

    ctx.strokeStyle = 'rgba(20,22,26,0.25)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(16, baseline + 10);
    ctx.lineTo(W - 16, baseline + 10);
    ctx.stroke();
    ctx.font = 'italic 500 14px ' + SERIF;
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(20,22,26,0.62)';
    for (var s = S0; s <= S1; s += 2) ctx.fillText(s + '%', xOf(s), baseline + 30);

    var dots = [];
    var stacked = {};
    sims.forEach(function (v, idx) {
      var k = Math.round(v / BIN);
      stacked[k] = (stacked[k] || 0) + 1;
      var x = xOf(k * BIN),
        y = baseline - r - (stacked[k] - 1) * 2 * r;
      dots.push({ x: x, y: y, share: v, seed: idx + 1 });
      ctx.beginPath();
      ctx.arc(x, y, Math.max(1.3, r - 0.25), 0, Math.PI * 2);
      ctx.fillStyle = ramp(2 * v - 100);
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
    });

    var mx = xOf(MEDIAN_SHARE);
    ctx.strokeStyle = 'rgba(20,22,26,0.55)';
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(mx, 18);
    ctx.lineTo(mx, baseline + 10);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(251,251,252,0.85)';
    roundRect(ctx, mx - 108, 20, 216, 58, 8);
    ctx.fill();
    ctx.fillStyle = D_COLOR;
    ctx.font = '600 30px ' + SERIF;
    ctx.fillText(MEDIAN_SHARE + '%', mx, 52);
    ctx.fillStyle = 'rgba(20,22,26,0.66)';
    ctx.font = 'italic 500 13px ' + SERIF;
    ctx.fillText('median Biss two-party share', mx, 71);

    if (!wrap) return;

    /* the hover card: the district repainted under the hovered outcome.
       The model's own machinery in miniature: a uniform logit swing solved
       so the turnout-weighted share hits the dot, plus deterministic
       per-precinct noise seeded by the dot so each draw looks like a draw. */
    var MW = 250,
      MH = 160;
    var mproj = projector(bounds(precincts), MW, MH, 4);
    var shapes = precincts.map(function (p) {
      var path = new Path2D();
      p.g.forEach(function (ring) {
        ring.forEach(function (pt, i) {
          var xy = mproj(pt);
          if (i === 0) path.moveTo(xy[0], xy[1]);
          else path.lineTo(xy[0], xy[1]);
        });
        path.closePath();
      });
      return {
        path: path,
        bl: Math.log(p.s26 / (100 - p.s26)),
        w: p.t,
      };
    });
    var totalW = 0;
    shapes.forEach(function (s) {
      totalW += s.w;
    });

    function districtShare(delta, noise) {
      var acc = 0;
      for (var i = 0; i < shapes.length; i++) {
        acc += (shapes[i].w * 100) / (1 + Math.exp(-(shapes[i].bl + (delta + noise[i]) / 25)));
      }
      return acc / totalW;
    }

    function scenarioShares(target, seed) {
      var rand = mulberry(seed * 2654435761);
      var noise = shapes.map(function () {
        // sum of three uniforms approximates a normal; sigma about 3 pp
        return (rand() + rand() + rand() - 1.5) * 6;
      });
      var lo = -30,
        hi = 30;
      for (var it = 0; it < 18; it++) {
        var mid = (lo + hi) / 2;
        if (districtShare(mid, noise) < target) lo = mid;
        else hi = mid;
      }
      var delta = (lo + hi) / 2;
      return shapes.map(function (s, i) {
        return 100 / (1 + Math.exp(-(s.bl + (delta + noise[i]) / 25)));
      });
    }

    var card = document.createElement('div');
    card.className = 'sm-tip sm-simcard';
    card.setAttribute('aria-hidden', 'true');
    card.innerHTML =
      '<p class="sm-tip__place">one simulated outcome</p>' +
      '<div class="sm-tip__row"><span class="sm-tip__dot" style="background:' +
      D_COLOR +
      '"></span><span class="sm-tip__name">' +
      CAND_D +
      '</span><span class="sm-tip__pct" id="sm-sim-d"></span></div>' +
      '<div class="sm-tip__row"><span class="sm-tip__dot" style="background:' +
      R_COLOR +
      '"></span><span class="sm-tip__name">' +
      CAND_R +
      '</span><span class="sm-tip__pct" id="sm-sim-r"></span></div>' +
      '<canvas width="' +
      MW * 2 +
      '" height="' +
      MH * 2 +
      '"></canvas>';
    wrap.appendChild(card);
    var mini = card.querySelector('canvas');
    var mctx = mini.getContext('2d');
    mctx.scale(2, 2);
    var dEl = card.querySelector('#sm-sim-d'),
      rEl = card.querySelector('#sm-sim-r');

    var lastSeed = 0;
    function showScenario(dot, e) {
      if (dot.seed !== lastSeed) {
        lastSeed = dot.seed;
        var shares = scenarioShares(dot.share, dot.seed);
        mctx.clearRect(0, 0, MW, MH);
        shapes.forEach(function (s, i) {
          mctx.fillStyle = ramp(2 * shares[i] - 100);
          mctx.fill(s.path, 'evenodd');
        });
        dEl.textContent = dot.share.toFixed(1) + '%';
        rEl.textContent = (100 - dot.share).toFixed(1) + '%';
      }
      var box = wrap.getBoundingClientRect();
      var x = e.clientX - box.left,
        y = e.clientY - box.top;
      var cw = card.offsetWidth || 280,
        ch = card.offsetHeight || 250;
      card.style.left = Math.max(0, Math.min(x + 14, box.width - cw)) + 'px';
      card.style.top = Math.max(0, Math.min(y + 14, box.height - ch)) + 'px';
      card.classList.add('is-on');
    }

    cv.addEventListener('mousemove', function (e) {
      var box = cv.getBoundingClientRect();
      var mxp = ((e.clientX - box.left) * W) / box.width,
        myp = ((e.clientY - box.top) * H) / box.height;
      var best = null,
        bestD = Infinity;
      for (var i = 0; i < dots.length; i++) {
        var dx = mxp - dots[i].x,
          dy = myp - dots[i].y,
          d2 = dx * dx + dy * dy;
        if (d2 < bestD) {
          bestD = d2;
          best = dots[i];
        }
      }
      if (!best || Math.sqrt(bestD) > Math.max(6, r + 4)) {
        card.classList.remove('is-on');
        return;
      }
      showScenario(best, e);
    });
    cv.addEventListener('mouseleave', function () {
      card.classList.remove('is-on');
    });
  }
})();
