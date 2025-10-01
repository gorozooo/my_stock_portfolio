(() => {
  const grid = document.getElementById('calGrid');
  if (!grid) return;

  const fmtYen = n =>
    '¥' + (Math.round(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');

  const sheet = document.getElementById('daySheet');
  const shDate = document.getElementById('sheetDate');
  const shTotal = document.getElementById('sheetTotal');
  const shList = document.getElementById('sheetList');
  const shClose = document.getElementById('sheetClose');
  shClose.addEventListener('click',()=>sheet.classList.remove('open'));

  function render(payload){
    grid.innerHTML = '';
    const y = payload.year, m = payload.month;

    // 1日の曜日オフセット(0=Sun)
    const firstDow = new Date(y, m - 1, 1).getDay();
    for (let i=0;i<firstDow;i++) grid.appendChild(document.createElement('div'));

    const today = new Date();
    payload.days.forEach(b => {
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'day';

      if (y === today.getFullYear() &&
          m === (today.getMonth()+1) &&
          b.d === today.getDate()){
        cell.classList.add('today');
      }

      const num = document.createElement('div');
      num.className = 'day-num';
      num.textContent = b.d;
      cell.appendChild(num);

      if ((b.total||0) > 0){
        const badge = document.createElement('div');
        badge.className = 'badge';
        badge.innerHTML = `<small>計</small> ${fmtYen(b.total)}`;
        cell.appendChild(badge);

        cell.addEventListener('click', ()=>{
          document.querySelectorAll('.day.selected').forEach(x=>x.classList.remove('selected'));
          cell.classList.add('selected');
          shDate.textContent = `${y}年${m}月${b.d}日`;
          shTotal.textContent = `合計：${fmtYen(b.total)}`;
          shList.innerHTML = (b.items||[])
            .map(it=>`<li><span class="name">${it.name}</span><span class="amt">${fmtYen(it.net)}</span></li>`)
            .join('') || `<li><span class="name">内訳なし</span><span class="amt">—</span></li>`;
          sheet.classList.add('open');
        });
      }

      grid.appendChild(cell);
    });
  }

  // 初期 payload（サーバ埋め込み）→ 無ければ fetch
  let initial = null;
  try { initial = JSON.parse(document.getElementById('payload_json')?.textContent || 'null'); }
  catch(e){ initial = null; }

  if (initial && initial.days) {
    render(initial);
  } else {
    const qs = new URLSearchParams(location.search);
    fetch(`/dividends/calendar.json?${qs}`)
      .then(r=>r.json())
      .then(render)
      .catch(()=>{ /* 失敗時は何もしない */ });
  }
})();