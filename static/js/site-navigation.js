// Shared, progressively enhanced navigation. No auth or cross-domain scripting.
(() => {
  for (const header of document.querySelectorAll('[data-site-header]')) {
    const nav = header.querySelector('.nav');
    const brand = header.querySelector('.brand');
    if (!nav || !brand) continue;
    const mobile = window.matchMedia('(max-width: 1100px)');
    nav.id ||= 'nysfiretools-primary-nav';
    brand.setAttribute('aria-label', 'NYS Fire Tools home');
    const row = document.createElement('div');
    row.className = 'site-brand-row';
    header.insertBefore(row, nav);
    row.append(brand);
    const home = document.createElement('a');
    home.className = 'site-home-link';
    home.href = brand.href;
    home.textContent = 'Home';
    row.append(home);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'site-menu-toggle';
    toggle.setAttribute('aria-controls', nav.id);
    toggle.setAttribute('aria-label', 'NYS Fire Tools menu');
    row.append(toggle);
    function setOpen(open, focus = false) {
      toggle.setAttribute('aria-expanded', String(open));
      toggle.textContent = open ? 'Close' : 'Menu';
      nav.hidden = mobile.matches && !open;
      if (focus) toggle.focus();
    }
    setOpen(false);
    toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
    mobile.addEventListener('change', () => setOpen(false));
    window.addEventListener('pageshow', () => setOpen(false));
    header.addEventListener('keydown', event => {
      if (event.key === 'Escape') setOpen(false, true);
    });
    nav.addEventListener('click', event => {
      if (event.target.closest('a')) setOpen(false);
    });
    document.addEventListener('click', event => {
      if (!header.contains(event.target)) setOpen(false);
    });
    // Let full-height tools reserve the actual header height at every viewport.
    const measure = () => document.documentElement.style.setProperty('--site-header-height', header.offsetHeight + 'px');
    new ResizeObserver(measure).observe(header);
    measure();
  }
})();
