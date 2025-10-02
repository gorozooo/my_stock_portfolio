(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const API = "/dividends/forecast.json";

  // 金額 → 「9.6千 / 1.9万」表記
  const compactJPY = (n) => {
    const v = Number(n||0);
    if (v >= 10000) return (Math.round(v/100)/100).toLocaleString("ja-JP") + "万";
    if (v >= 1000)  return (Math.round(v/10)/100).toLocaleString("ja-JP") + "千";
    return "¥" + v.toLocaleString("ja-JP", {maximumFractionDigits:0});
  };
  const yen = (n)=> "¥" + Math.round(Number(n||0)).toLocaleString("ja-JP");

  // Chart state
  let chart;

  function currentQuery() {
    const year  = Number($("#fYear")?.value || new Date().getFullYear());
    const basis = $("#segBasis .pill.is-active")?.dataset.v || "pay";   // pay / ex
    const stack = $("#segStack .pill.is-active")?.dataset.v || "none";  // none / broker / account
    return { year, basis, stack };
  }

  function buildDatasetMonths(payload){
    // 既存API: { months: [{yyyymm:"YYYY-MM", net: number}], sum12 }
    const months = (payload?.months || []);
    const byIdx = Array(12).fill(0);
    months.forEach(m => {
      const mm = Number(String(m.yyyymm).slice(-2))-1;
      if (mm>=0 && mm<12) byIdx[mm] = Number(m.net||0);
    });
    return byIdx;
  }

  function renderChart(payload, query){
    const labels = ["01","02","03","04","05","06","07","08","09","10","11","12"];

    // 合計（単一データセット）だけ対応（既存API）
    const data12 = buildDatasetMonths(payload);

    const ds = [{
      label: "合計（税後）",
      data: data12,
      borderWidth: 0,
      backgroundColor: "rgba(66,133,244,0.65)",
      hoverBackgroundColor: "rgba(66,133,244,0.8)",
      borderRadius: 6,
      barThickness: 18,
    }];

    // 既存の Chart を破棄
    chart?.destroy();

    const ctx = $("#fcChart").getContext("2d");
    chart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: ds },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => yen(ctx.parsed.y),
            }
          }
        },
        scales: {
          x: {
            ticks: { color: "#cfd7ff" },
            grid: { display:false }
          },
          y: {
            ticks: {
              color: "#9aa4b2",
              callback: (v)=> yen(v)
            },
            grid: { color:"rgba(255,255,255,.06)" },
            beginAtZero: true
          }
        }
      }
    });

    // バーの上にコンパクト表示（データラベル代替：軽量に描画）
    requestAnimationFrame(()=> {
      const meta = chart.getDatasetMeta(0);
      const ctx2 = chart.ctx;
      ctx2.save();
      ctx2.fillStyle = "#cfd7ff";
      ctx2.font = "600 11px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial";
      meta.data.forEach((el, i)=>{
        const v = data12[i] || 0;
        if (v <= 0) return;
        const p = el.tooltipPosition();
        ctx2.textAlign = "center";
        ctx2.fillText(compactJPY(v), p.x, p.y - 6);
      });
      ctx2.restore();
    });

    // 平均
    const sum = (payload?.sum12 || data12.reduce((a,b)=>a+b,0));
    const avg = sum / 12;
    $("#fcAvg").textContent = `月平均：${yen(avg)}`;

    // 疑似レジェンド
    $("#fcLegend").textContent = (query.stack === "none" ? "合計（税後）" :
      query.stack === "broker" ? "証券会社別（税後）" : "口座別（税後）");
  }

  function fetchAndRender(q){
    // 既存のサーバ側は basis / stack を無視してもOK（将来拡張用に送る）
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, { credentials:"same-origin" })
      .then(r=>r.json())
      .then(json => renderChart(json, q))
      .catch(()=>{/* no-op */});
  }

  // 初期
  const init = window.__DIVFC_INIT__;
  const initYear = window.__DIVFC_YEAR__ || new Date().getFullYear();
  if (init) {
    // すぐ描画
    renderChart(init, {year:initYear, basis:"pay", stack:"none"});
  } else {
    fetchAndRender(currentQuery());
  }

  // UIイベント
  $("#fYear")?.addEventListener("change", ()=> fetchAndRender(currentQuery()));
  ["segBasis","segStack"].forEach(id=>{
    const box = $("#"+id);
    box?.addEventListener("click", (e)=>{
      const btn = e.target.closest(".pill");
      if (!btn) return;
      box.querySelectorAll(".pill").forEach(b=>b.classList.remove("is-active"));
      btn.classList.add("is-active");
      fetchAndRender(currentQuery());
    });
  });
})();