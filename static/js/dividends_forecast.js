(function(){
  const fmtYen = n => (Math.round(n||0)).toLocaleString() + "円";

  async function load(){
    const res = await fetch('/dividends/forecast.json', {credentials:'same-origin'});
    const json = await res.json();
    render(json.series);
  }

  function render(series){
    // テーブル
    const tbl = document.getElementById('fcTable');
    tbl.innerHTML = series.map(s=>`<tr><td>${s.ym} / ${s.m}月</td><td style="text-align:right">${fmtYen(s.net)}</td></tr>`).join('');

    // 線グラフ（SVG）
    const svg = document.getElementById('fcSvg'); svg.innerHTML='';
    const W=360,H=180,pad=20;
    const max = Math.max(1, ...series.map(s=>s.net));
    const sx=i => pad + i*( (W-pad*2)/(series.length-1) );
    const sy=v => H-pad - (v/max)*(H-pad*2);

    const path = series.map((s,i)=>`${i?'L':'M'}${sx(i)},${sy(s.net)}`).join('');
    const g = document.createElementNS("http://www.w3.org/2000/svg","path");
    g.setAttribute('d', path);
    g.setAttribute('fill','none'); g.setAttribute('stroke','#6ea8ff'); g.setAttribute('stroke-width','2');
    svg.appendChild(g);

    // x labels
    series.forEach((s,i)=>{
      const t=document.createElementNS("http://www.w3.org/2000/svg","text");
      t.setAttribute('x', sx(i)); t.setAttribute('y', H-4); t.setAttribute('font-size','9'); t.setAttribute('text-anchor','middle');
      t.setAttribute('fill','rgba(255,255,255,.8)'); t.textContent = s.m;
      svg.appendChild(t);
    });
  }

  load();
})();