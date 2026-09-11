(() => {
  const toggle = document.querySelector('.menu-toggle');
  const sidebar = document.querySelector('#scheduler-sidebar');
  const mobile = window.matchMedia('(max-width: 760px)');
  if (!toggle || !sidebar) return;

  function setOpen(open, restoreFocus = false) {
    toggle.setAttribute('aria-expanded', String(open));
    toggle.firstChild.textContent = open ? 'Close ' : 'Menu ';
    sidebar.hidden = mobile.matches && !open;
    document.querySelector('#main-content').inert = mobile.matches && open;
    document.body.classList.toggle('navigation-open', mobile.matches && open);
    if (restoreFocus) toggle.focus();
  }
  toggle.hidden = false;
  setOpen(false);
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  mobile.addEventListener('change', () => setOpen(false));
  window.addEventListener('pageshow', () => setOpen(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') setOpen(false, true);
  });
  document.addEventListener('focusin', event => {
    if (mobile.matches && toggle.getAttribute('aria-expanded') === 'true' &&
        !sidebar.contains(event.target) && !event.target.closest('.mobile-navigation')) setOpen(false);
  });
  const links = [...sidebar.querySelectorAll('.nav-item')];
  const exact = links.find(link => new URL(link.href).pathname === location.pathname);
  if (exact) links.forEach(link => link.classList.toggle('active', link === exact));
  links.filter(link => link.classList.contains('active')).forEach(link => link.setAttribute('aria-current', 'page'));
})();
