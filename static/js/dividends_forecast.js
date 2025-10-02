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

  let chart;

  function qNow(){
    const year  = Number($("#fYear")?.value || new Date().getFullYear());
    const basis = $("#segBasis .pill.is-active")?.dataset.v || "pay";      // pay or ex
    const stack = $("#segStack .pill.is-active")?.dataset.v || "none";     // none | broker | account
    return {year, basis, stack};
  }

  // ランダム系じゃなく固定の色セット（見やすさ優先）
  const PALETTE = [
    "rgba(66,133,244,0.75)",
    "rgba(234,67,53,0.75)",
    "rgba(251,188,5,0.75)",
    "rgba(52,168,83,0.75)",
    "rgba(171,71,188,0.75)",
    "rgba(0,172,193,0.75)",
    "rgba(255,112,67,0.75)",
    "rgba(124,179,66,0.75)"
  ];

  // 値ラベル（合計時のみ描画）
  const valueLabelPlugin = {
    id: "valueLabels",
    afterDatasetsDraw(chart, args, opts){
      const stacked = chart.options.scales?.y?.stacked;
      if (stacked) return; // 積み上げ時は読みにくいので非表示
      const {ctx} = chart;
      const meta = chart.getDatasetMeta(0);
      const data = chart.data.datasets[0]?.data || [];
      ctx.save();
      ctx.fillStyle = "#cfd7ff";
      ctx.font = "600 11px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial";
      ctx.textAlign = "center";
      meta.data.forEach((el, i)=>{
        const v = Number(data[i]||0);
        if (v<=0 || !el) return;
        const p = el.tooltipPosition();
        ctx.fillText(compactJPY(v), p.x, p.y - 6);
      });
      ctx.restore();
    }
  };

  function buildDatasets(series){
    // series: [{key,label,data:[12 nums]}]
    if (!Array.isArray(series) || series.length===0) {
      return [{
        label: "合計（税後）",
        data: Array(12).fill(0),
        backgroundColor: PALETTE[0],
        borderRadius: 6,
        barThickness: 18,
        stack: "S",
      }];
    }
    return series.map((s, i)=>({
      label: s.label || s.key || `系列${i+1}`,
      data: (s.data || Array(12).fill(0)).map(n=>Number(n||0)),
      backgroundColor: PALETTE[i % PALETTE.length],
      borderRadius: 6,
      barThickness: 18,
      stack: "S",
    }));
  }

  function render(payload, query){
    const labels = payload?.labels || ["01","02","03","04","05","06","07","08","09","10","11","12"];
    const series = payload?.series || [];
    const isStacked = (series.length > 1); // 2本以上なら積み上げ

    // 合計（凡例/平均用）
    let sum12 = 0;
    if (series.length === 0) {
      sum12 = 0;
    } else if (series.length === 1) {
      sum12 = series[0].data.reduce((a,b)=>a+Number(b||0), 0);
    } else {
      // 積み上げは全系列合計
      sum12 = series.reduce((acc,s)=> acc + s.data.reduce((a,b)=>a+Number(b||0),0), 0);
    }

    // 既存破棄
    chart?.destroy();
    const ctx = $("#fcChart").getContext("2d");

    chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: buildDatasets(series)
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        resizeDelay: 180,
        animation: { duration: 250 },
        plugins: {
          legend: { display: isStacked }, // 合計だけなら凡例オフ
          tooltip: {
            callbacks: {
              label: (c)=>{
                const lab = c.dataset?.label ? `${c.dataset.label}: ` : "";
                return lab + yen(c.parsed.y);
              },
              footer: (items)=>{
                // 月合計（積み上げ時）
                if (!isStacked) return "";
                const s = items.reduce((a,it)=> a + (it.parsed?.y||0), 0);
                return "月合計: " + yen(s);
              }
            }
          }
        },
        scales: {
          x: { ticks:{color:"#cfd7ff"}, grid:{display:false}, stacked: isStacked },
          y: {
            beginAtZero:true,
            ticks:{ color:"#9aa4b2", callback:(v)=> yen(v) },
            grid:{ color:"rgba(255,255,255,.06)" },
            stacked: isStacked
          }
        }
      },
      plugins: [valueLabelPlugin]
    });

    // 表示テキスト
    const basisLabel = (query.basis==="ex" ? "権利確定月" : "支払い月");
    const stackLabel =
      query.stack==="none" ? "合計（税後）" :
      (query.stack==="broker" ? "証券会社別（税後）" : "口座別（税後）");
    $("#fcLegend").textContent = `${basisLabel} / ${stackLabel}`;
    $("#fcAvg").textContent = `月平均：${yen(sum12/12)}`;
  }

  function fetchAndRender(q){
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, {credentials:"same-origin"})
      .then(r=>r.json())
      .then(json=>render(json,q))
      .catch(()=>{/* no-op */});
  }

  // 初期描画（埋め込み or API）
  const init = window.__DIVFC_INIT__;
  const initYear = window.__DIVFC_YEAR__ || new Date().getFullYear();
  if (init && init.series) render(init, {year:initYear, basis:"pay", stack:"none"});
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