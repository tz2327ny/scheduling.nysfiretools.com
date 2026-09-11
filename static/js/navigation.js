(() => {
  const menu = document.querySelector('.scheduler-navigation');
  if (!menu) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  const reset = () => { menu.open = !mobile.matches; };
  reset();
  mobile.addEventListener('change', reset);
  window.addEventListener('pageshow', reset);
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mobile.matches) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
  const links = [...menu.querySelectorAll('a')];
  const exact = links.find(link => new URL(link.href).pathname === location.pathname);
  if (exact) links.forEach(link => link.classList.toggle('active', link === exact));
  links.filter(link => link.classList.contains('active')).forEach(link => link.setAttribute('aria-current', 'page'));
})();
