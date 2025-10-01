(function(){
  const grid = document.getElementById('calGrid');
  const ySel = document.getElementById('selYear');
  const mSel = document.getElementById('selMonth');
  const bSel = document.getElementById('selBroker');
  const aSel = document.getElementById('selAccount');

  // modal
  const mask  = document.getElementById('modalMask');
  const modal = document.getElementById('modal');
  const mTitle= document.getElementById('mTitle');
  const mTotal= document.getElementById('mTotal');
  const mList = document.getElementById('mList');
  const mClose= document.getElementById('mClose');

  function openModal(){ mask.style.display='block'; modal.style.display='block'; }
  function closeModal(){ mask.style.display='none'; modal.style.display='none'; }
  mask.addEventListener('click', closeModal);
  mClose.addEventListener('click', closeModal);

  function firstWeekday(y,m){ return new Date(y, m-1, 1).getDay(); } // 0=Sun
  function lastDay(y,m){ return new Date(y, m, 0).getDate(); }

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
      cell.innerHTML = `<div class="d">${d}</div><div class="label"></div>`;
      grid.appendChild(cell);
    }
  }

  function renderPayload(p){
    const y = parseInt(p.year,10), m = parseInt(p.month,10);
    buildSkeleton(y,m);
    (p.days||[]).forEach(bucket=>{
      const cell = grid.querySelector(`[data-day="${bucket.d}"]`);
      if (!cell) return;

      // ラベル（先頭銘柄を1行省略）
      if (bucket.items && bucket.items.length){
        const label = cell.querySelector('.label');
        label.textContent = bucket.items[0].name + (bucket.items.length>1 ? ' など' : '');
      }

      // 合計があればバッジを出す
      const total = Math.round(bucket.total || 0);
      if (total > 0){
        cell.classList.add('has-data');
        const badge = document.createElement('div');
        badge.className = 'badge';
        badge.textContent = `${total.toLocaleString()}円`;
        cell.appendChild(badge);

        // クリックでモーダル
        const openDetail = ()=>{
          mTitle.textContent = `${y}年${m}月${bucket.d}日`;
          mTotal.textContent = `合計：${total.toLocaleString()}円`;
          mList.innerHTML = '';
          (bucket.items||[]).forEach(it=>{
            const row = document.createElement('div');
            row.className = 'row';
            row.innerHTML = `<div>${it.name}（${it.ticker}）</div><div>${Math.round(it.net).toLocaleString()}円</div>`;
            mList.appendChild(row);
          });
          openModal();
        };
        cell.addEventListener('click', openDetail);
        badge.addEventListener('click', (e)=>{ e.stopPropagation(); openDetail(); });
      }
    });
  }

  async function fetchAndRender(){
    const y = ySel.value, m = mSel.value;
    const broker  = bSel.value, account = aSel.value;
    const qs = new URLSearchParams({year:y, month:m});
    if (broker) qs.append('broker', broker);
    if (account) qs.append('account', account);

    const r = await fetch(`/dividends/calendar.json?${qs.toString()}`, {credentials:'same-origin'});
    if (!r.ok) return;
    renderPayload(await r.json());
  }

  if (window.__CAL_INIT__){ renderPayload(window.__CAL_INIT__); }
  else { fetchAndRender(); }

  [ySel,mSel,bSel,aSel].forEach(el => el && el.addEventListener('change', fetchAndRender));
})();