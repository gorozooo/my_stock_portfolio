(function(){
  const grid = document.getElementById('calGrid');
  const ySel = document.getElementById('selYear');
  const mSel = document.getElementById('selMonth');
  const bSel = document.getElementById('selBroker');
  const aSel = document.getElementById('selAccount');

  function firstWeekday(y,m){ // 0=Sun
    return new Date(y, m-1, 1).getDay();
  }
  function lastDay(y,m){
    return new Date(y, m, 0).getDate();
  }

  function buildSkeleton(y,m){
    grid.innerHTML = '';
    const pad = firstWeekday(y,m);
    const last = lastDay(y,m);
    for (let i=0;i<pad;i++){
      const d = document.createElement('div');
      d.className = 'cell';
      grid.appendChild(d);
    }
    for (let d=1; d<=last; d++){
      const cell = document.createElement('div');
      cell.className = 'cell';
      cell.dataset.day = d;
      cell.innerHTML = `<div class="d">${d}</div><div class="list"></div>`;
      grid.appendChild(cell);
    }
  }

  function renderPayload(p){
    const y = parseInt(p.year,10), m = parseInt(p.month,10);
    buildSkeleton(y,m);
    (p.days||[]).forEach(bucket=>{
      const cell = grid.querySelector(`[data-day="${bucket.d}"]`);
      if (!cell) return;
      // バッジ（合計 > 0 のときだけ）
      if ((bucket.total||0) > 0){
        const badge = document.createElement('div');
        badge.className = 'badge';
        badge.textContent = `${Math.round(bucket.total).toLocaleString()}円`;
        cell.appendChild(badge);
      }
      // 先頭1件だけ銘柄名メモ（あると “出てる感” が出る）
      if (bucket.items && bucket.items.length){
        const list = cell.querySelector('.list');
        const top = bucket.items[0];
        list.textContent = `${top.name} など`;
      }
    });
  }

  async function fetchAndRender(){
    const y = ySel.value, m = mSel.value;
    const broker  = bSel.value;
    const account = aSel.value;
    const qs = new URLSearchParams({year:y, month:m});
    if (broker) qs.append('broker', broker);
    if (account) qs.append('account', account);

    const r = await fetch(`/dividends/calendar.json?${qs.toString()}`, {credentials:'same-origin'});
    if (!r.ok) return;
    const data = await r.json();
    renderPayload(data);
  }

  // 初期描画（サーバから埋めた JSON があればそれで即表示）
  if (window.__CAL_INIT__){
    renderPayload(window.__CAL_INIT__);
  }else{
    fetchAndRender();
  }

  [ySel,mSel,bSel,aSel].forEach(el => el && el.addEventListener('change', fetchAndRender));
})();