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

  // ← UIの選択値をAPIクエリに変換
  function qNow(){
    const year   = Number($("#fYear")?.value || new Date().getFullYear());
    const basisV = $("#segBasis .pill.is-active")?.dataset.v || "pay";   // pay | ex(=record)
    const stack  = $("#segStack .pill.is-active")?.dataset.v || "none";  // 画面表示用のみ
    const mode   = (basisV === "ex") ? "record" : "pay";                  // ← バックエンドに合わせる
    return {year, mode, stack};
  }

  // APIペイロード → 12ヶ月配列
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
      const {ctx} = chart;
      const data = chart.data.datasets[0]?.data || [];
      const meta = chart.getDatasetMeta(0);
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

  function render(payload, query){
    const labels = ["01","02","03","04","05","06","07","08","09","10","11","12"];
    const data12 = months12(payload);

    chart?.destroy();
    const ctx = $("#fcChart").getContext("2d");

    chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "合計（税後）",
          data: data12,
          backgroundColor: "rgba(66,133,244,0.65)",
          hoverBackgroundColor: "rgba(66,133,244,0.8)",
          borderRadius: 6,
          barThickness: 18,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,   // 親の固定高さに追従
        resizeDelay: 200,
        animation: { duration: 250 },
        plugins: {
          legend: { display:false },
          tooltip: { callbacks: { label: (c)=> yen(c.parsed.y) } }
        },
        scales: {
          x: { ticks:{color:"#cfd7ff"}, grid:{display:false} },
          y: {
            beginAtZero:true,
            ticks:{ color:"#9aa4b2", callback:(v)=> yen(v) },
            grid:{ color:"rgba(255,255,255,.06)" }
          }
        }
      },
      plugins: [valueLabelPlugin]
    });

    const sum = payload?.sum12 ?? data12.reduce((a,b)=>a+b,0);
    $("#fcAvg").textContent = `月平均：${yen(sum/12)}`;

    const basisLabel = (query.mode === "record") ? "権利確定月" : "支払い月";
    const stackLabel =
      query.stack==="none"    ? "合計（税後）" :
      query.stack==="broker"  ? "証券会社別（税後）" :
                                "口座別（税後）";
    $("#fcLegend").textContent = `${basisLabel}・${stackLabel}`;
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
  if (init) {
    render(init, {year:initYear, mode:"pay", stack:"none"});
  } else {
    fetchAndRender(qNow());
  }

  // UIイベント
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