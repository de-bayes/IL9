/**
 * Shared IL9Cast UI helpers (theme + mobile nav).
 * Include once per page: <script src="{{ url_for('static', filename='site.js') }}"></script>
 */
(function () {
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
