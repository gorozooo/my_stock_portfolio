(function(){
  const ySel = document.getElementById('selYear');
  const rows = document.getElementById('fcRows');
  const sum  = document.getElementById('fcSum');

  function renderPayload(p){
    rows.innerHTML = '';
    (p.months || []).forEach(m=>{
      const el = document.createElement('div');
      el.className = 'row';
      el.innerHTML = `<div>${m.yyyymm}</div><div>${Math.round(m.net).toLocaleString()}円</div>`;
      rows.appendChild(el);
    });
    sum.textContent = `合計（12ヶ月）：${Math.round(p.sum12||0).toLocaleString()}円`;
  }

  async function fetchAndRender(){
    const y = ySel.value;
    const r = await fetch(`/dividends/forecast.json?year=${encodeURIComponent(y)}`, {credentials:'same-origin'});
    if (!r.ok) return;
    const data = await r.json();
    renderPayload(data);
  }

  if (window.__FORECAST_INIT__){
    renderPayload(window.__FORECAST_INIT__);
  }else{
    fetchAndRender();
  }

  ySel.addEventListener('change', fetchAndRender);
})();