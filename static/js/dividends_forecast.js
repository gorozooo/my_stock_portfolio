(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const API = "/dividends/forecast.json";

  const compactJPY = (n) => {
    const v = Number(n||0);
    if (v >= 10000) return (Math.round(v/100)/100).toLocaleString("ja-JP") + "万";
    if (v >= 1000)  return (Math.round(v/10)/100).toLocaleString("ja-JP") + "千";
    return "¥" + v.toLocaleString("ja-JP", {maximumFractionDigits:0});
  };
  const yen = (n)=> "¥" + Math.round(Number(n||0)).toLocaleString("ja-JP");

  const PAL = [
    "rgba(66,133,244,0.70)","rgba(244,180,0,0.70)","rgba(15,157,88,0.70)",
    "rgba(219,68,55,0.70)","rgba(171,71,188,0.70)","rgba(0,172,193,0.70)",
    "rgba(255,112,67,0.70)","rgba(124,179,66,0.70)"
  ];
  const PAL_HOVER = PAL.map(c=>c.replace("0.70","0.85"));

  let chart;

  function qNow(){
    const year  = Number($("#fYear")?.value || new Date().getFullYear());
    const basis = $("#segBasis .pill.is-active")?.dataset.v || "pay";     // "pay" | "ex"
    const stack = $("#segStack .pill.is-active")?.dataset.v || "none";    // "none" | "broker" | "account"
    return {year, basis, stack};
  }

  function months12(payload){
    const arr = Array(12).fill(0);
    (payload?.months||[]).forEach(m=>{
      const i = Number(String(m.yyyymm).slice(-2)) - 1;
      if (i>=0 && i<12) arr[i] = Number(m.net||0);
    });
    return arr;
  }

  // 値ラベルプラグイン
  const valueLabelPlugin = {
    id: "valueLabels",
    afterDatasetsDraw(chart){
      const {ctx, data} = chart;
      const meta0 = chart.getDatasetMeta(0);
      if (!meta0) return;
      ctx.save();
      ctx.fillStyle = "#cfd7ff";
      ctx.font = "600 11px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial";
      ctx.textAlign = "center";

      // 各x座標ごとに合計値（スタック時は積み上げ後の頂点）を出して表示
      const stacked = chart.options.scales?.y?.stacked;
      const bars = meta0.data || [];
      const nBars = bars.length;
      for (let i=0; i<nBars; i++){
        let val = 0;
        if (stacked) {
          // すべてのdatasetの i 番目の値を合算
          (data.datasets||[]).forEach(ds=>{ val += Number(ds.data?.[i]||0); });
        } else {
          val = Number(data.datasets?.[0]?.data?.[i]||0);
        }
        if (val <= 0) continue;
        const el = bars[i];
        if (!el) continue;
        const p = el.tooltipPosition();
        ctx.fillText(compactJPY(val), p.x, p.y - 6);
      }
      ctx.restore();
    }
  };

  function render(payload, query){
    const labels = ["01","02","03","04","05","06","07","08","09","10","11","12"];

    // datasets 構築
    let datasets = [];
    if (query.stack === "none") {
      datasets = [{
        label: "合計（税後）",
        data: months12(payload),
        backgroundColor: PAL[0],
        hoverBackgroundColor: PAL_HOVER[0],
        borderRadius: 6,
        barThickness: 18,
      }];
    } else {
      const groups = Array.isArray(payload?.groups) ? payload.groups : [];
      groups.forEach((g, idx)=>{
        const color = PAL[idx % PAL.length];
        const colorH = PAL_HOVER[idx % PAL_HOVER.length];
        datasets.push({
          label: String(g.label || g.key || `Group ${idx+1}`),
          data: months12(g),
          backgroundColor: color,
          hoverBackgroundColor: colorH,
          borderRadius: 6,
          barThickness: 18,
          stack: "S1",
        });
      });

      // グループが空でもキャンバスを保つ（0データ1本）
      if (datasets.length === 0){
        datasets.push({
          label: "データなし",
          data: Array(12).fill(0),
          backgroundColor: PAL[0],
          hoverBackgroundColor: PAL_HOVER[0],
          borderRadius: 6,
          barThickness: 18,
          stack: "S1",
        });
      }
    }

    // 合計・平均
    let sum12 = 0;
    if (query.stack === "none") {
      sum12 = payload?.sum12 ?? datasets[0].data.reduce((a,b)=>a+b,0);
    } else {
      const groups = Array.isArray(payload?.groups) ? payload.groups : [];
      sum12 = groups.reduce((s,g)=> s + (Number(g.sum12)||0), 0);
    }

    // 破棄→再生成
    chart?.destroy();
    const ctx = $("#fcChart").getContext("2d");

    chart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,  // 親 .chartbox にだけ従う
        resizeDelay: 200,
        animation: { duration: 250 },
        plugins: {
          legend: { display: true, labels: { color: "#cfd7ff" } },
          tooltip: { callbacks: { label: (c)=> `${c.dataset.label}: ${yen(c.parsed.y)}` } }
        },
        scales: {
          x: { ticks:{color:"#cfd7ff"}, grid:{display:false}, stacked: (query.stack!=="none") },
          y: {
            beginAtZero:true,
            ticks:{ color:"#9aa4b2", callback:(v)=> yen(v) },
            grid:{ color:"rgba(255,255,255,.06)" },
            stacked: (query.stack!=="none")
          }
        }
      },
      plugins: [valueLabelPlugin]
    });

    $("#fcAvg").textContent = `月平均：${yen(sum12/12)}`;
    $("#fcLegend").textContent =
      query.stack==="none" ? "合計（税後）" :
      (query.stack==="broker" ? "証券会社別（税後）" : "口座別（税後）");
  }

  function fetchAndRender(q){
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, {credentials:"same-origin"})
      .then(r=>r.json())
      .then(json=>render(json,q))
      .catch(()=>{/* no-op */});
  }

  // 初期描画
  const init = window.__DIVFC_INIT__;
  const initYear = window.__DIVFC_YEAR__ || new Date().getFullYear();
  if (init) render(init, {year:initYear, basis:"pay", stack:"none"});
  else fetchAndRender(qNow());

  // UI
  $("#fYear")?.addEventListener("change", ()=> fetchAndRender(qNow()));
  ["segBasis","segStack"].forEach(id=>{
    const box = $("#"+id);
    box?.addEventListener("click", (e)=>{
      const btn = e.target.closest(".pill"); if(!btn) return;
      box.querySelectorAll(".pill").forEach(b=>b.classList.remove("is-active"));
      btn.classList.add("is-active");
      fetchAndRender(qNow());
    });
  });
})();