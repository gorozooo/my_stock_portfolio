// --- holdings.js (force-stable swipe + detail toggle) ---
(function () {
  const rows = Array.from(document.querySelectorAll('[data-swipe]'));
  if (!rows.length) return;

  const px = (n) => n + 'px';
  const getOpenW = (actions) => {
    const v = getComputedStyle(actions).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  const setOpen = (row, open) => {
    const a = row._actions, OPEN = row._OPEN;
    a.style.transition = 'right .18s ease-out';
    if (open) {
      row.classList.add('is-open');
      row._opened = true;
      a.style.right = '0px';
      a.style.pointerEvents = 'auto';
    } else {
      row.classList.remove('is-open');
      row._opened = false;
      a.style.right = px(-OPEN);
      a.style.pointerEvents = 'none';
    }
  };

  const closeAll = (except) => {
    rows.forEach(r => { if (r !== except) setOpen(r, false); });
  };

  rows.forEach(row => {
    const actions   = row.querySelector('.actions');
    const track     = row.querySelector('.track');
    const detailBtn = row.querySelector('[data-action="detail"]');

    // cache
    row._actions = actions;
    row._OPEN    = getOpenW(actions);
    row._opened  = row.classList.contains('is-open');

    // init position
    actions.style.right = px(-row._OPEN);
    actions.style.pointerEvents = 'none';

    // avoid closing when tapping inside actions
    actions.addEventListener('click', e => e.stopPropagation());

    // -------- swipe state
    let sx=0, sy=0, dragging=false, horiz=false, baseRight=0;

    const start = (clientX, clientY, fromActions) => {
      if (fromActions) return;                      // do not start from action buttons
      // ★ if detail is open, close it immediately then continue to swipe
      if (row.classList.contains('show-detail')) {
        row.classList.remove('show-detail');
      }
      dragging = true; horiz = false;
      sx = clientX; sy = clientY;
      actions.style.transition = 'none';
      baseRight = row._opened ? 0 : -row._OPEN;
      if (!row._opened) closeAll(row);             // keep one row open at a time
    };

    const move = (clientX, clientY, cancelable) => {
      if (!dragging) return;
      const dx = clientX - sx, dy = clientY - sy;
      if (!horiz) {
        if (Math.abs(dx) < 8) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz = true;
        else { dragging = false; actions.style.transition=''; return; }
      }
      if (cancelable) cancelable.preventDefault?.();

      let nr = baseRight - dx;                     // drag right to open
      if (nr > 0) nr = 0;
      if (nr < -row._OPEN) nr = -row._OPEN;
      actions.style.right = px(nr);
    };

    const end = () => {
      if (!dragging) return;
      dragging = false;
      actions.style.transition = 'right .18s ease-out';
      const cur = parseFloat(getComputedStyle(actions).right) || -row._OPEN;
      const willOpen = cur > -row._OPEN / 2;
      setOpen(row, willOpen);
    };

    // touch
    row.addEventListener('touchstart', (e)=>{
      const t = e.touches[0];
      start(t.clientX, t.clientY, !!e.target.closest('.actions'));
    }, {passive:true});
    row.addEventListener('touchmove',  (e)=> move(e.touches[0].clientX, e.touches[0].clientY, e), {passive:false});
    row.addEventListener('touchend', end);
    row.addEventListener('touchcancel', end);

    // mouse (for desktop操作)
    row.addEventListener('mousedown', (e)=>{
      start(e.clientX, e.clientY, !!e.target.closest('.actions'));
    });
    window.addEventListener('mousemove', (e)=> move(e.clientX, e.clientY, e));
    window.addEventListener('mouseup', end);

    // card tap -> close detail first, then close swipe if open
    track.addEventListener('click', (e)=>{
      if (row.classList.contains('show-detail')) {
        row.classList.remove('show-detail');
        e.stopPropagation();      // prevent immediate close/open race
        return;
      }
      if (row._opened) setOpen(row, false);
    });

    // detail button toggles detail and closes swipe
    detailBtn?.addEventListener('click', (e)=>{
      e.stopPropagation();
      row.classList.toggle('show-detail');
      setOpen(row, false);
    });
  });

  // tap outside -> close all
  document.addEventListener('click', (e)=>{
    if (!e.target.closest('[data-swipe]')) closeAll(null);
  });
})();