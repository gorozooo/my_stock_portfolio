(function(){
  const $  = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>[...r.querySelectorAll(s)];
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY = v=>"¥"+Math.round(v).toLocaleString("ja-JP");
  const pct = v => (v>=0?"+":"") + v.toFixed(2) + "%";

  // ===== LIVE clock =====
  function tickLive(){
    const el = $('#liveTs'); if(!el) return;
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    const ss = String(d.getSeconds()).padStart(2,'0');
    el.textContent = `${hh}:${mm}:${ss}`;
  }
  setInterval(tickLive, 1000); tickLive();

  // ===== util: parse CSV series -> number[] =====
  function parseSeries(csv){
    return String(csv||"")
      .split(',')
      .map(s=>parseFloat(s))
      .filter(v=>!Number.isNaN(v));
  }

  // ===== util: slice last N safely =====
  function tail(arr, n){
    if(!Array.isArray(arr) || arr.length===0) return [];
    return arr.slice(Math.max(0, arr.length - n));
  }

  // ===== util: returns from values =====
  function returnsFrom(values){
    const out=[];
    for(let i=1;i<values.length;i++){
      const prev = values[i-1], cur=values[i];
      if(prev>0) out.push((cur/prev - 1)*100);
    }
    return out;
  }

  // ===== util: percentile =====
  function percentile(arr, p){
    if(arr.length===0) return 0;
    const sorted = [...arr].sort((a,b)=>a-b);
    const k = clamp(Math.floor((p/100)*(sorted.length-1)), 0, sorted.length-1);
    return sorted[k];
  }

  // ===== util: stdev (sample) =====
  function stdev(arr){
    if(arr.length<2) return 0;
    const m = arr.reduce((a,b)=>a+b,0)/arr.length;
    const v = arr.reduce((a,b)=>a+(b-m)*(b-m),0)/(arr.length-1);
    return Math.sqrt(v);
  }

  // ===== util: max drawdown (on value series) [%] & dd series =====
  function maxDrawdown(values){
    if(values.length===0) return {ddPct:0, ddSeries:[]};
    let peak = values[0];
    let maxDD = 0;
    const out=[];
    for(let i=0;i<values.length;i++){
      peak = Math.max(peak, values[i]);
      const dd = peak>0 ? (values[i]/peak - 1)*100 : 0;
      out.push(dd);
      maxDD = Math.min(maxDD, dd);
    }
    return { ddPct: maxDD, ddSeries: out };
  }

  // ===== draw compare spark (portfolio vs bench, normalized) =====
  function drawCompareSpark(el, pv, bv){
    if(!el) return;
    const w = el.clientWidth||600, h = el.clientHeight||90, pad=6;
    if(pv.length<2){ el.textContent='データ不足'; return; }

    // 正規化（初期値=100）
    const norm = (arr)=>{
      if(arr.length===0) return [];
      const base = arr[0] || 1;
      return arr.map(v=> base ? (v/base)*100 : 100);
    };
    const pn = norm(pv);
    const bn = bv.length ? norm(bv) : [];

    const min = Math.min(...pn, ...(bn.length?bn:[Infinity]));
    const max = Math.max(...pn, ...(bn.length?bn:[-Infinity]));
    const x = i => pad + (w-pad*2) * (i/(pn.length-1));
    const y = v => max===min ? h/2 : pad + (1 - (v-min)/(max-min))*(h-pad*2);

    const ptsP = pn.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    const ptsB = bn.length ? bn.map((v,i)=>`${x(i)},${y(v)}`).join(' ') : '';

    el.innerHTML = `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
        <defs>
          <linearGradient id="p-g" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="var(--accent)"/><stop offset="100%" stop-color="var(--primary)"/>
          </linearGradient>
        </defs>
        ${bn.length ? `<polyline points="${ptsB}" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="2"/>` : ''}
        <polyline points="${ptsP}" fill="none" stroke="url(#p-g)" stroke-width="3"/>
      </svg>`;
  }

  // ===== draw histogram on canvas =====
  function drawHistogram(canvas, data){
    if(!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0,0,W,H);
    if(!data.length){ ctx.fillStyle='#999'; ctx.fillText('データ不足', 8, 16); return; }

    // ビン数
    const nbin = Math.min(24, Math.max(10, Math.floor(Math.sqrt(data.length))));
    const mn = Math.min(...data), mx = Math.max(...data);
    const bw = (mx-mn) || 1;
    const binW = bw/nbin;
    const bins = new Array(nbin).fill(0);
    data.forEach(v=>{
      const idx = Math.min(nbin-1, Math.max(0, Math.floor((v-mn)/binW)));
      bins[idx]++; 
    });

    const maxC = Math.max(...bins) || 1;
    const pad = 10;
    const innerW = W - pad*2, innerH = H - pad*2;

    // 軸
    ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad, H-pad); ctx.lineTo(W-pad, H-pad);
    ctx.moveTo(pad, pad);   ctx.lineTo(pad, H-pad);
    ctx.stroke();

    // 棒
    const barW = innerW/nbin;
    for(let i=0;i<nbin;i++){
      const h = innerH * (bins[i]/maxC);
      const x = pad + i*barW;
      const y = H - pad - h;
      const grd = ctx.createLinearGradient(0, y, 0, y+h);
      grd.addColorStop(0, 'rgba(0,255,209,.85)');
      grd.addColorStop(1, 'rgba(110,168,255,.85)');
      ctx.fillStyle = grd;
      ctx.fillRect(x+1, y, barW-2, h);
    }
  }

  // ===== draw dd spark (SVG polyline) =====
  function drawDDSpark(el, ddSeries){
    if(!el) return;
    const w = el.clientWidth||520, h = el.clientHeight||160, pad=10;
    if(!ddSeries.length){ el.textContent='—'; return; }

    const min = Math.min(...ddSeries), max = Math.max(...ddSeries);
    const x = i => pad + (w-pad*2) * (i/(ddSeries.length-1));
    const y = v => max===min ? h/2 : pad + (1 - (v-min)/(max-min))*(h-pad*2);
    const pts = ddSeries.map((v,i)=>`${x(i)},${y(v)}`).join(' ');

    el.innerHTML = `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
        <defs>
          <linearGradient id="dd-g" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#ff9aa6"/><stop offset="100%" stop-color="var(--danger)"/>
          </linearGradient>
        </defs>
        <polyline points="${pts}" fill="none" stroke="url(#dd-g)" stroke-width="3"/>
      </svg>`;
  }

  // ===== compute + render risk =====
  function renderRisk(horizon=30){
    const panel = $('#riskPanel');
    const pv = tail(parseSeries(panel?.dataset.portfolio||''), horizon);
    const bv = tail(parseSeries(panel?.dataset.benchmark||''), horizon);

    // 比較スパーク（上段の「今日の損益+ベンチ差」の下）
    drawCompareSpark($('#sparkCompare'),
      parseSeries($('#sparkCompare')?.dataset.portfolio||panel?.dataset.portfolio||''),
      parseSeries($('#sparkCompare')?.dataset.benchmark||panel?.dataset.benchmark||'')
    );

    // リスク計算対象がなければ終了
    if(pv.length<2){
      $('#kpiVaR').textContent = '—';
      $('#kpiMaxDD').textContent = '—';
      $('#kpiVol').textContent = '—';
      drawHistogram($('#histCanvas'), []);
      drawDDSpark($('#ddSpark'), []);
      return;
    }

    // 日次リターン（%）
    const rets = returnsFrom(pv);

    // VaR(95%) = 下位5%点
    const var95 = percentile(rets, 5); // 既に%単位（マイナスが大きい）
    $('#kpiVaR').textContent = pct(var95);
    $('#kpiVaR').classList.toggle('neg', var95<0);
    $('#kpiVaR').classList.toggle('pos', var95>=0);

    // 年率ボラ = stdev(日次%) * √252
    const vol = stdev(rets) * Math.sqrt(252);
    $('#kpiVol').textContent = pct(vol);

    // 最大DD（%）とDD系列
    const {ddPct, ddSeries} = maxDrawdown(pv);
    $('#kpiMaxDD').textContent = pct(ddPct);
    $('#kpiMaxDD').classList.toggle('neg', ddPct<0);
    drawDDSpark($('#ddSpark'), ddSeries);

    // ヒスト
    drawHistogram($('#histCanvas'), rets);

    // 現金/信用 比率
    const cash = parseFloat($('#kpiCashRatio')?.dataset.cash||'0');
    const total = Math.max(1, parseFloat($('#kpiCashRatio')?.dataset.total||'1'));
    $('#kpiCashRatio').textContent = (cash/total*100).toFixed(1) + '%';

    const margin = parseFloat($('#kpiMarginRatio')?.dataset.margin||'0');
    $('#kpiMarginRatio').textContent = (margin/total*100).toFixed(1) + '%';
  }

  // ===== events =====
  function init(){
    // 初回レンダ
    renderRisk(30);

    // 地平切替
    $('#riskHorizonBtn')?.addEventListener('click', ()=>renderRisk(30));
    $('#riskHorizonBtn60')?.addEventListener('click', ()=>renderRisk(60));
    $('#riskHorizonBtn90')?.addEventListener('click', ()=>renderRisk(90));

    // リサイズで再描画
    let t;
    window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t=setTimeout(()=>renderRisk(), 120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
