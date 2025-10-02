(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const API = "/dividends/forecast.json";

  // ====== formatters ======
  const yen = (n)=> "¥" + Math.round(Number(n||0)).toLocaleString("ja-JP");
  const compactJPY = (n) => {
    const v = Number(n||0);
    if (v >= 10000) return (Math.round(v/100)/100).toLocaleString("ja-JP") + "万";
    if (v >= 1000)  return (Math.round(v/10)/100).toLocaleString("ja-JP") + "千";
    return "¥" + v.toLocaleString("ja-JP", {maximumFractionDigits:0});
  };

  // ====== query state ======
  function qNow(){
    return {
      year:  Number($("#fYear")?.value || new Date().getFullYear()),
      basis: $("#segBasis .pill.is-active")?.dataset.v || "pay",   // pay | ex
      stack: $("#segStack .pill.is-active")?.dataset.v || "none",  // none | broker | account
    };
  }

  // ====== helpers ======
  const MONTH_LABELS = ["01","02","03","04","05","06","07","08","09","10","11","12"];

  const to12 = (arrLike)=> {
    const acc = Array(12).fill(0);
    (arrLike||[]).forEach(m=>{
      const i = Number(String(m.yyyymm).slice(-2)) - 1;
      if (i>=0 && i<12) acc[i] = Number(m.net||0);
    });
    return acc;
  };

  // パレット（見やすい青系中心）
  const PALETTE = [
    "rgba(66,133,244,0.75)","rgba(100,181,246,0.75)","rgba(77,182,172,0.75)",
    "rgba(129,199,132,0.75)","rgba(255,202,40,0.75)","rgba(239,108,0,0.75)",
    "rgba(171,71,188,0.75)","rgba(236,64,122,0.75)","rgba(255,112,67,0.75)",
  ];
  const colorAt = (i)=> PALETTE[i % PALETTE.length];

  // 値ラベル（合計モード：各棒の上 / 積み上げ：積み上げ合計のみ）
  const valueLabelPlugin = {
    id: "valueLabels",
    afterDatasetsDraw(chart){
      const {ctx, data, scales} = chart;
      const {datasets, labels} = data;
      if (!datasets?.length) return;
      const stacked = chart.options?.scales?.y?.stacked;

      // 各 x の合計を算出
      const totals = labels.map((_,i)=>
        datasets.reduce((sum,ds)=> sum + (Number(ds.data?.[i]||0)), 0)
      );

      ctx.save();
      ctx.fillStyle = "#cfd7ff";
      ctx.font = "600 11px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial";
      ctx.textAlign = "center";

      const meta = chart.getDatasetMeta(0);
      labels.forEach((_, i)=>{
        const v = totals[i] || 0;
        if (v <= 0) return;
        const x = scales.x.getPixelForValue(i);
        const y = scales.y.getPixelForValue(v);
        ctx.fillText(compactJPY(v), x, y - 6);
      });

      // 合計モードのときは個別バーにも（視認性 Up）
      if (!stacked && datasets.length === 1) {
        const dsMeta = chart.getDatasetMeta(0);
        const arr = datasets[0].data || [];
        dsMeta.data.forEach((el, i)=>{
          if (!el) return;
          const v = Number(arr[i]||0); if (v<=0) return;
          const p = el.tooltipPosition();
          ctx.fillText(compactJPY(v), p.x, p.y - 6);
        });
      }
      ctx.restore();
    }
  };

  // ====== Chart state ======
  let chart;

  // Skeleton / Empty
  function showSkeleton(show){
    $("#skeleton").style.display = show ? "block" : "none";
    $("#chartBox").style.visibility = show ? "hidden" : "visible";
  }
  function showEmpty(show, msg="データがありません"){
    $("#empty").textContent = msg;
    $("#empty").style.display = show ? "flex" : "none";
  }

  // ====== render ======
  function render(payload, query){
    showSkeleton(false);

    const labels = MONTH_LABELS.slice();
    const stackedMode = query.stack !== "none";

    let datasets = [];
    let sum12 = 0;

    if (stackedMode && Array.isArray(payload?.stacks) && payload.stacks.length){
      // stacks: [{key, label, months:[{yyyymm, net}]}]
      payload.stacks.forEach((s,i)=>{
        const data12 = to12(s.months||[]);
        datasets.push({
          label: s.label || s.key || "—",
          data: data12,
          backgroundColor: colorAt(i),
          borderRadius: 6,
          barThickness: 18,
          stack: "ALL",
        });
      });
      sum12 = (payload.months?.reduce((a,b)=>a+Number(b.net||0),0)) ?? 0;
    } else {
      // 合計のみ
      const data12 = to12(payload?.months || []);
      datasets = [{
        label: "合計（税後）",
        data: data12,
        backgroundColor: colorAt(0),
        borderRadius: 6,
        barThickness: 18,
      }];
      sum12 = data12.reduce((a,b)=>a+b,0);
    }

    // 全 0 のときは空表示
    const allZero = datasets.every(ds => (ds.data||[]).every(v => Number(v||0)===0));
    if (allZero){
      showEmpty(true);
    } else {
      showEmpty(false);
    }

    chart?.destroy();
    const ctx = $("#fcChart").getContext("2d");
    chart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,   // 親 .chartbox の固定高さに追従
        resizeDelay: 180,
        animation: { duration: 240 },
        plugins: {
          legend: {
            display: stackedMode,
            labels: { color:"#cfd7ff", boxWidth: 12 },
            onClick: (e, item, legend) => {
              const idx = item.datasetIndex;
              const ci = legend.chart;
              const meta = ci.getDatasetMeta(idx);
              meta.hidden = meta.hidden === null ? !ci.data.datasets[idx].hidden : null;
              ci.update();
            }
          },
          tooltip: {
            callbacks: {
              title: (items)=> `月: ${items?.[0]?.label}`,
              label: (c)=> `${c.dataset?.label || "合計"}：${yen(c.parsed.y)}`
            }
          }
        },
        scales: {
          x: { ticks:{color:"#cfd7ff"}, grid:{display:false}, stacked: stackedMode },
          y: {
            beginAtZero:true,
            ticks:{ color:"#9aa4b2", callback:(v)=> yen(v) },
            grid:{ color:"rgba(255,255,255,.06)" },
            stacked: stackedMode
          }
        }
      },
      plugins: [valueLabelPlugin]
    });

    // 下部テキスト
    $("#fcAvg").textContent = `月平均：${yen(sum12/12)}`;
    $("#fcLegend").textContent =
      query.stack==="none" ? "合計（税後）" :
      (query.stack==="broker" ? "証券会社別（税後）" : "口座別（税後）");
  }

  // ====== fetch ======
  function fetchAndRender(q){
    showSkeleton(true);
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, {credentials:"same-origin"})
      .then(r=>r.json())
      .then(json=> render(json, q))
      .catch(()=> { showSkeleton(false); showEmpty(true, "読み込みに失敗しました"); });
  }

  // ====== init ======
  const init = window.__DIVFC_INIT__;
  const initYear = window.__DIVFC_YEAR__ || new Date().getFullYear();
  if (init) { showSkeleton(false); render(init, {year:initYear, basis:"pay", stack:"none"}); }
  else { fetchAndRender(qNow()); }

  // ====== UI wires ======
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