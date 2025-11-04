(function(){
  const items = window.__AI_ITEMS__ || [];
  const modal = document.getElementById('aiModal');
  const closeEls = modal.querySelectorAll('[data-close]');
  const nameEl = document.getElementById('m_name');
  const starsEl = document.getElementById('m_stars');
  const scoreEl = document.getElementById('m_score');
  const sectorEl = document.getElementById('m_sector');
  const td = (x)=> x==='up'?'⤴️':(x==='down'?'⤵️':'➡️');
  const tde = {
    d: document.getElementById('m_trend_d'),
    w: document.getElementById('m_trend_w'),
    m: document.getElementById('m_trend_m'),
  };
  const reasonsEl = document.getElementById('m_reasons');
  const pricesEl = document.getElementById('m_prices');
  const qtyEl = document.getElementById('m_qty');

  let chart, ctx = document.getElementById('m_chart').getContext('2d');

  function openModal(idx){
    const it = items[idx];
    if(!it) return;
    nameEl.textContent = `${it.name} (${it.code})`;
    starsEl.textContent = '⭐️'.repeat(it.stars) + '☆'.repeat(5-it.stars);
    scoreEl.textContent = `${it.score}点`;
    sectorEl.textContent = it.sector;
    tde.d.textContent = `日足 ${td(it.trend.d)}`;
    tde.w.textContent = `週足 ${td(it.trend.w)}`;
    tde.m.textContent = `月足 ${td(it.trend.m)}`;

    reasonsEl.innerHTML = '';
    it.reasons.forEach(r=>{
      const li=document.createElement('li'); li.textContent=r; reasonsEl.appendChild(li);
    });

    pricesEl.textContent = `エントリー ${it.prices.entry}円 / 利確 ${it.prices.tp}円 / 損切 ${it.prices.sl}円`;
    qtyEl.textContent = `数量 ${it.qty.shares}株 / 必要資金 約${it.qty.capital.toLocaleString()}円 / 想定 +${it.qty.pl_plus.toLocaleString()}円 / -${it.qty.pl_minus.toLocaleString()}円 (R=${it.qty.r})`;

    // ダミー価格時系列（見た目用）。後で実データに置換。
    const base = it.prices.entry;
    const series = Array.from({length:50}, (_,i)=> base + Math.round((Math.sin(i/6)*30) + (i-25)));
    const tp = it.prices.tp, sl = it.prices.sl, en = it.prices.entry;

    if(chart){ chart.destroy(); }
    chart = new Chart(ctx, {
      type:'line',
      data:{ labels: series.map((_,i)=>i+1), datasets:[
        { label:'価格', data:series, tension:.3, pointRadius:0 },
        { label:'エントリー', data:series.map(()=>en), borderDash:[6,6], pointRadius:0 },
        { label:'利確', data:series.map(()=>tp), borderDash:[6,6], pointRadius:0 },
        { label:'損切', data:series.map(()=>sl), borderDash:[6,6], pointRadius:0 },
      ]},
      options:{
        responsive:true,
        maintainAspectRatio:false,
        plugins:{legend:{display:true}},
        scales:{x:{display:false}}
      }
    });

    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden','false');
  }

  function closeModal(){
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden','true');
  }

  document.querySelectorAll('.more').forEach(btn=>{
    btn.addEventListener('click', e=>{
      const idx = Number(btn.dataset.open);
      openModal(idx);
    });
  });
  closeEls.forEach(el=> el.addEventListener('click', closeModal));
  modal.querySelector('.backdrop').addEventListener('click', closeModal);

  // 監視：戻るジェスチャ対応（iOSで自然に）
  document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeModal(); });
})();