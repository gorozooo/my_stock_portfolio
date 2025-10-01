(() => {
  const grid = document.getElementById('calGrid');
  if (!grid) return;

  const fmtY = n => '¥' + Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');

  // ---- bottom sheet ----
  const sheet   = document.getElementById('daySheet');
  const shDate  = document.getElementById('sheetDate');
  const shTotal = document.getElementById('sheetTotal');
  const shList  = document.getElementById('sheetList');
  document.getElementById('sheetClose').onclick = () => sheet.classList.remove('open');

  function render(payload){
    const y = payload.year, m = payload.month;
    const firstDow = new Date(y, m - 1, 1).getDay();
    const today = new Date();
    const isToday = (d) => (y===today.getFullYear() && m===(today.getMonth()+1) && d===today.getDate());

    const byDay = {};
    payload.days.forEach(b => { byDay[b.d] = b; });

    let html = '';
    for (let i=0;i<firstDow;i++) html += '<div></div>';

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

    grid.onclick = (e)=>{
      const cell = e.target.closest('.day'); if (!cell) return;
      const d = Number(cell.dataset.d); const bucket = byDay[d];
      if (!bucket || (bucket.total||0)<=0) return;

      grid.querySelectorAll('.day.selected').forEach(x=>x.classList.remove('selected'));
      cell.classList.add('selected');

      shDate.textContent  = `${y}年${m}月${d}日`;
      shTotal.textContent = `合計：${fmtY(bucket.total)}`;

      // 明細：銘柄／金額 + サブ行(証券・口座・株数)
      shList.innerHTML = (bucket.items||[])
        .map(it => `
          <li>
            <div class="row1">
              <span class="nm">${it.ticker} / ${it.name}</span>
              <span class="amt">${fmtY(it.net)}</span>
            </div>
            <div class="row2">
              ${it.broker ? `<span class="chip">${it.broker}</span>` : ``}
              ${it.account ? `<span class="chip">${it.account}</span>` : ``}
              ${it.qty ? `<span class="chip">株数: ${it.qty}</span>` : ``}
            </div>
          </li>`).join('') || `<li><div class="row1"><span class="nm">内訳なし</span><span class="amt">—</span></div></li>`;

      sheet.classList.add('open');
    };
  }

  // 初期 payload 使用 → 無ければ fetch
  let initial = null;
  try { initial = JSON.parse(document.getElementById('payload_json')?.textContent || 'null'); }
  catch(e){ initial = null; }

  if (initial && initial.days) {
    render(initial);
  } else {
    const qs = new URLSearchParams(location.search);
    fetch(`/dividends/calendar.json?${qs}`).then(r=>r.json()).then(render).catch(()=>{});
  }
})();