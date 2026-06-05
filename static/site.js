/**
 * Shared IL9Cast UI helpers (theme + mobile nav).
 * Pages may set window.onThemeChange before or after load for chart/map re-render.
 */
(function () {
  window.onThemeChange = window.onThemeChange || null;

  function toggleTheme() {
    var html = document.documentElement;
    var isLight = html.getAttribute('data-theme') === 'light';
    if (isLight) {
      html.removeAttribute('data-theme');
      localStorage.setItem('il9-theme', 'dark');
    } else {
      html.setAttribute('data-theme', 'light');
      localStorage.setItem('il9-theme', 'light');
    }
    if (typeof window.onThemeChange === 'function') {
      window.onThemeChange();
    }
  }

  function toggleMobileMenu() {
    var hamburger = document.getElementById('hamburger');
    var mobileNav = document.getElementById('mobileNav');
    if (!hamburger || !mobileNav) return;
    hamburger.classList.toggle('active');
    mobileNav.classList.toggle('active');
  }

  function closeMobileMenu() {
    var hamburger = document.getElementById('hamburger');
    var mobileNav = document.getElementById('mobileNav');
    if (!hamburger || !mobileNav) return;
    hamburger.classList.remove('active');
    mobileNav.classList.remove('active');
  }

  window.toggleTheme = toggleTheme;
  window.toggleMobileMenu = toggleMobileMenu;

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('#mobileNav a').forEach(function (link) {
      link.addEventListener('click', closeMobileMenu);
    });
    document.addEventListener('click', function (e) {
      var hamburger = document.getElementById('hamburger');
      var mobileNav = document.getElementById('mobileNav');
      if (!hamburger || !mobileNav) return;
      if (!hamburger.contains(e.target) && !mobileNav.contains(e.target)) {
        closeMobileMenu();
      }
    });
  });
})();
