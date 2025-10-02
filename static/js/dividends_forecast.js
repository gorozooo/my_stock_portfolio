// static/js/dividends_forecast.js
(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const API = "/dividends/forecast.json";
  let chart;

  const yen0 = n => {
    try { return Math.round(parseFloat(n||0))
      .toLocaleString("ja-JP",{style:"currency",currency:"JPY",maximumFractionDigits:0}); }
    catch(_){ return "¥0"; }
  };

  function randomColor(idx){
    // 見やすい寒色トーンを少しずつ
    const hues = [220, 210, 230, 200, 250, 190, 260, 180];
    const h = hues[idx % hues.length];
    return `hsl(${h} 70% 60% / 0.9)`;
  }

  function buildTotalDataset(payload){
    const labels = (payload.months||[]).map(m => m.yyyymm.slice(5));
    const data   = (payload.months||[]).map(m => m.net || 0);
    return { labels, datasets: [{
      label: "合計（税引後）",
      data,
      type: "bar",
      borderWidth: 0,
      backgroundColor: "rgba(99, 132, 255, 0.8)",
    }]};
  }

  function buildStackDataset(payload){
    const labels = payload.labels?.map(m => m.slice(5)) || [];
    const stacks = payload.stacks || {};
    const keys = Object.keys(stacks);
    const datasets = keys.map((k, i)=>({
      label: k,
      data: stacks[k],
      type: "bar",
      stack: "s1",
      backgroundColor: randomColor(i),
      borderWidth: 0,
    }));
    return { labels, datasets };
  }

  function renderChart(payload){
    const ctx = $("#fcChart");
    const isStack = payload.stack && payload.stack !== "none";

    const cfgData = isStack ? buildStackDataset(payload) : buildTotalDataset(payload);

    const cfg = {
      type: "bar",
      data: cfgData,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: isStack, labels: { color: "#dfe6ff" } },
          tooltip: {
            callbacks: {
              label: (ctx)=>{
                const v = ctx.parsed.y || 0;
                return `${ctx.dataset.label}: ${yen0(v)}`;
              }
            }
          }
        },
        scales: {
          x: { ticks:{ color:"#cfd7ff" }, stacked: isStack, grid:{ color:"rgba(255,255,255,.07)"} },
          y: { ticks:{ color:"#cfd7ff", callback:(v)=>yen0(v) }, stacked: isStack, grid:{ color:"rgba(255,255,255,.07)"} }
        }
      }
    };

    if(chart){ chart.destroy(); }
    chart = new Chart(ctx, cfg);

    $("#fcSum").textContent = `年間合計：${yen0(payload.sum12||0)}`;
  }

  function fetchAndRender(year, stack){
    const p = new URLSearchParams({year, stack});
    fetch(`${API}?${p.toString()}`, {credentials:"same-origin"})
      .then(r=>r.json())
      .then(json=>renderChart(json))
      .catch(()=>{/* no-op */});
  }

  // 初期表示（サーバ埋め込みがあればそれで描画）
  const init = window.__DIVFC_INIT__;
  if(init){ renderChart(init); }

  // 年/スタック切り替え
  $("#fYear")?.addEventListener("change", ()=>{
    const y = $("#fYear").value;
    const s = $("#segStack .active")?.dataset.v || "none";
    fetchAndRender(y, s);
  });
  $("#segStack")?.addEventListener("click", (e)=>{
    const btn = e.target.closest("button[data-v]"); if(!btn) return;
    $$("#segStack button").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");
    const y = $("#fYear").value;
    fetchAndRender(y, btn.dataset.v);
  });
})();