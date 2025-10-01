(() => {
  const grid = document.getElementById('calGrid');
  if (!grid) return;

  // 目に入る情報をシンプルに
  const fmtY = n => '¥' + Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');

  // ---- ボトムシート ----
  const sheet     = document.getElementById('daySheet');
  const shDate    = document.getElementById('sheetDate');
  const shTotal   = document.getElementById('sheetTotal');
  const shList    = document.getElementById('sheetList');
  const shClose   = document.getElementById('sheetClose');
  shClose.addEventListener('click', () => sheet.classList.remove('open'));

  // ---- 描画：HTML一括生成で高速化 ----
  function render(payload){
    const y = payload.year, m = payload.month;
    const firstDow = new Date(y, m - 1, 1).getDay();
    const today = new Date();
    const isToday = (d) => (y===today.getFullYear() && m===(today.getMonth()+1) && d===today.getDate());

    // クリックに使う辞書
    const byDay = {};
    payload.days.forEach(b => { byDay[b.d] = b; });

    let html = '';
    // 空白（前月ぶん）
    for (let i=0;i<firstDow;i++) html += '<div></div>';

    // 日セル
    for (const b of payload.days){
      const d = b.d;
      const has = (b.total||0) > 0;
      const cls = 'day' + (isToday(d) ? ' today' : '');
      html += `<div class="${cls}" data-d="${d}">
        <span class="day-num">${d}</span>
        ${has ? `<span class="badge">${fmtY(b.total)}</span>` : ``}
      </div>`;
    }
    grid.innerHTML = html;

    // イベント委譲（1つだけで済む）
    grid.onclick = (e)=>{
      const cell = e.target.closest('.day');
      if (!cell) return;
      const d = Number(cell.dataset.d);
      const bucket = byDay[d];
      if (!bucket || (bucket.total||0)<=0) return;

      grid.querySelectorAll('.day.selected').forEach(x=>x.classList.remove('selected'));
      cell.classList.add('selected');

      shDate.textContent  = `${y}年${m}月${d}日`;
      shTotal.textContent = `合計：${fmtY(bucket.total)}`;
      shList.innerHTML = (bucket.items||[])
        .map(it => `<li><span class="name">${it.name}</span><span class="amt">${fmtY(it.net)}</span></li>`)
        .join('') || `<li><span class="name">内訳なし</span><span class="amt">—</span></li>`;
      sheet.classList.add('open');
    };
  }

  // ---- 初期 payload を使い、無ければ fetch（GET のまま） ----
  let initial = null;
  try { initial = JSON.parse(document.getElementById('payload_json')?.textContent || 'null'); }
  catch(e){ initial = null; }

  if (initial && initial.days) {
    render(initial);
  } else {
    const qs = new URLSearchParams(location.search);
    fetch(`/dividends/calendar.json?${qs}`)
      .then(r => r.json()).then(render).catch(()=>{});
  }
})();