// aiapp/js/picks_debug.js
// AI Picks 診断用：フィルタ + モーダル + ローソク足チャート

(function(){
  // ===============================
  // フィルタ（一覧テーブル）
  // ===============================
  const input = document.getElementById("filterInput");
  const table = document.getElementById("picksTable");

  if (input && table){
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    input.addEventListener("input", function(){
      const q = this.value.trim().toLowerCase();
      if (!q){
        rows.forEach(r => r.style.display = "");
        return;
      }
      rows.forEach(r => {
        const text = r.textContent.toLowerCase();
        r.style.display = text.includes(q) ? "" : "none";
      });
    });
  }

  // ===============================
  // モーダル開閉
  // ===============================
  const body   = document.body;
  const modal  = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");
  if (!modal || !table) return;

  function closeModal(){
    modal.classList.remove("open");
    body.classList.remove("modal-open");
  }

  if (closeBtn){
    closeBtn.addEventListener("click", closeModal);
  }
  modal.addEventListener("click", function(e){
    if (e.target === modal){
      closeModal();
    }
  });

  // ===============================
  // チャート関連
  // ===============================
  const chartCanvas = document.getElementById("pickChart");
  const chartEmpty  = document.getElementById("chartEmpty");
  let pickChart = null;

  function showEmpty(msg){
    if (!chartEmpty || !chartCanvas) return;
    chartEmpty.textContent = msg || "チャート用データが不足しています";
    chartEmpty.style.display = "flex";
    chartCanvas.style.opacity = "0.15";
  }

  function hideEmpty(){
    if (!chartEmpty || !chartCanvas) return;
    chartEmpty.style.display = "none";
    chartCanvas.style.opacity = "1";
  }

  function parseNumber(v){
    if (v === undefined || v === null || v === "" || v === "NaN"){
      return null;
    }
    const n = Number(v);
    return isNaN(n) ? null : n;
  }

  // "1,2,3" または "[1,2,3]" → [1,2,3]
  function parseNumberArray(s){
    if (!s) return [];
    let txt = String(s).trim();
    try{
      if (txt[0] === "[" || txt[0] === "{"){
        const arr = JSON.parse(txt);
        if (!Array.isArray(arr)) return [];
        return arr.map(Number).filter(v => !isNaN(v));
      }
      return txt.split(",")
        .map(t => Number(t.trim()))
        .filter(v => !isNaN(v));
    }catch(e){
      return [];
    }
  }

  // OHLC: JSON 形式 [[o,h,l,c], ...] か "o,h,l,c|o,h,l,c|..." を想定
  function parseOhlcArray(s){
    if (!s) return [];
    let txt = String(s).trim();
    let out = [];

    try{
      if (txt[0] === "["){
        const arr = JSON.parse(txt);
        if (Array.isArray(arr)){
          arr.forEach(row => {
            if (!Array.isArray(row) || row.length < 4) return;
            const o = Number(row[0]);
            const h = Number(row[1]);
            const l = Number(row[2]);
            const c = Number(row[3]);
            if ([o,h,l,c].some(v => isNaN(v))) return;
            out.push({o, h, l, c});
          });
        }
      }else{
        // "o,h,l,c|o,h,l,c"
        txt.split("|").forEach(seg => {
          const parts = seg.split(",");
          if (parts.length < 4) return;
          const o = Number(parts[0]);
          const h = Number(parts[1]);
          const l = Number(parts[2]);
          const c = Number(parts[3]);
          if ([o,h,l,c].some(v => isNaN(v))) return;
          out.push({o, h, l, c});
        });
      }
    }catch(e){
      out = [];
    }
    return out;
  }

  // ローソク足描画用プラグイン
  const candlePlugin = {
    id: "simpleCandles",
    afterDatasetsDraw(chart, args, opts){
      const ohlc = chart.$ohlcData;
      if (!ohlc || !ohlc.length) return;

      const {ctx, scales} = chart;
      const xScale = scales.x;
      const yScale = scales.y;
      if (!xScale || !yScale) return;

      const span = xScale.getPixelForValue(1) - xScale.getPixelForValue(0);
      const candleWidth = Math.max(3, span * 0.45);

      ctx.save();
      ctx.lineWidth = 1.2;

      ohlc.forEach((bar, idx) => {
        const x = xScale.getPixelForValue(idx);
        const yHigh = yScale.getPixelForValue(bar.h);
        const yLow  = yScale.getPixelForValue(bar.l);
        const yOpen = yScale.getPixelForValue(bar.o);
        const yClose = yScale.getPixelForValue(bar.c);

        const isUp = bar.c >= bar.o;
        const stroke = isUp ? "#4ade80" : "#f97373";
        const fill   = isUp ? "rgba(74,222,128,0.9)" : "rgba(248,113,113,0.9)";

        // ヒゲ
        ctx.strokeStyle = stroke;
        ctx.beginPath();
        ctx.moveTo(x, yHigh);
        ctx.lineTo(x, yLow);
        ctx.stroke();

        // 実体
        const top = Math.min(yOpen, yClose);
        const bottom = Math.max(yOpen, yClose);
        const h = Math.max(2, bottom - top);
        ctx.fillStyle = fill;
        ctx.fillRect(x - candleWidth / 2, top, candleWidth, h);
      });

      ctx.restore();
    }
  };

  function renderChartFromRow(row){
    if (!chartCanvas || typeof Chart === "undefined"){
      return;
    }
    const ds = row.dataset || {};

    // --- OHLC or close ---
    let ohlc = parseOhlcArray(ds.chartOhlc || "");
    const closesRaw = parseNumberArray(ds.chartCloses || ds.chart || "");

    if (!ohlc.length && closesRaw.length){
      // OHLC が無ければ終値だけから簡易ローソク（実体ゼロ）を作る
      ohlc = closesRaw.map(v => ({o: v, h: v, l: v, c: v}));
    }

    if (!ohlc.length){
      if (pickChart){
        pickChart.destroy();
        pickChart = null;
      }
      showEmpty("チャート用データが不足しています");
      return;
    }

    hideEmpty();

    const n = ohlc.length;
    const labels = Array.from({length: n}, (_, i) => String(i + 1));
    const closes = ohlc.map(b => b.c);

    // Entry / TP / SL
    const entryVal = parseNumber(ds.entry);
    const tpVal    = parseNumber(ds.tp);
    const slVal    = parseNumber(ds.sl);

    // y の min/max （OHLC + 水平線も含めて）
    let values = [];
    ohlc.forEach(b => {
      values.push(b.h, b.l);
    });
    if (entryVal !== null) values.push(entryVal);
    if (tpVal !== null) values.push(tpVal);
    if (slVal !== null) values.push(slVal);

    let yMin = Math.min.apply(null, values);
    let yMax = Math.max.apply(null, values);
    if (!isFinite(yMin) || !isFinite(yMax)){
      yMin = closes[0];
      yMax = closes[0];
    }
    if (yMin === yMax){
      yMin -= 1;
      yMax += 1;
    }
    const pad = (yMax - yMin) * 0.08;
    yMin -= pad;
    yMax += pad;

    // 既存チャート破棄
    if (pickChart){
      pickChart.destroy();
      pickChart = null;
    }

    const ctx = chartCanvas.getContext("2d");

    const datasets = [];

    // 終値ライン（細い青）
    datasets.push({
      label: "終値",
      data: closes,
      borderColor: "rgba(56,189,248,1)",   // シアン寄り
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.2,
    });

    // Entry / TP / SL の水平線
    const mkHorizontal = (value, label, color, dash) => {
      if (value === null) return null;
      return {
        label: label,
        data: Array(n).fill(value),
        borderColor: color,
        borderWidth: 1,
        pointRadius: 0,
        tension: 0,
        borderDash: dash || [],
      };
    };

    const dsEntry = mkHorizontal(entryVal, "Entry", "#22c55e", [4, 3]);
    const dsTp    = mkHorizontal(tpVal, "TP",    "#22c55e", []);
    const dsSl    = mkHorizontal(slVal, "SL",    "#ef4444", []);

    [dsEntry, dsTp, dsSl].forEach(d => { if (d) datasets.push(d); });

    pickChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: {
            display: true,
            labels: {
              usePointStyle: true,
              boxWidth: 10,
              padding: 12,
              color: "#9ca3af",
              font: {
                size: 10,
              },
            },
          },
          tooltip: {
            enabled: false,
          },
        },
        scales: {
          x: {
            display: false,
            grid: {
              display: false,
            },
          },
          y: {
            display: true,
            position: "right",
            grid: {
              color: "rgba(30,64,175,0.35)",
            },
            ticks: {
              color: "#9ca3af",
              font: {
                size: 9,
              },
              callback: function(value){
                const n = Number(value);
                if (isNaN(n)) return "";
                return n.toLocaleString();
              },
            },
            min: yMin,
            max: yMax,
          },
        },
      },
      plugins: [candlePlugin],
    });

    // ローソク足データをチャートに渡す
    pickChart.$ohlcData = ohlc;
    pickChart.update();
  }

  // ===============================
  // モーダル open + チャート描画
  // ===============================
  function setText(id, v, fmt){
    const el = document.getElementById(id);
    if (!el) return;
    if (v === undefined || v === null || v === "" || v === "NaN"){
      el.textContent = "–";
      return;
    }
    let txt = v;
    if (fmt === "int"){
      const n = Number(v);
      txt = isNaN(n) ? "–" : n.toLocaleString();
    }else if (fmt === "yen"){
      const n = Number(v);
      if (isNaN(n)){
        txt = "–";
      }else{
        txt = n.toLocaleString();
        if (n > 0) txt = "+" + txt;
      }
    }
    el.textContent = txt;
  }

  function openModal(row){
    const ds = row.dataset || {};

    // タイトル周り
    document.getElementById("modalTitle").textContent =
      (ds.code || "") + " " + (ds.name || "");
    document.getElementById("modalSector").textContent = ds.sector || "";

    document.getElementById("modalScoreBadge").textContent =
      "Score: " + (ds.score || "–");
    document.getElementById("modalStarBadge").textContent =
      "★ " + (ds.stars || "–");

    // 価格・指標
    setText("detailLast", ds.last, "int");
    setText("detailAtr", ds.atr, "int");

    // 数量
    setText("detailQtyRakuten", ds.qtyRakuten, "int");
    setText("detailQtyMatsui", ds.qtyMatsui, "int");
    setText("detailQtySbi", ds.qtySbi, "int");

    // Entry / TP / SL
    setText("detailEntry", ds.entry, "int");
    setText("detailTp", ds.tp, "int");
    setText("detailSl", ds.sl, "int");

    // 必要資金
    setText("detailCashRakuten", ds.cashRakuten, "yen");
    setText("detailCashMatsui", ds.cashMatsui, "yen");
    setText("detailCashSbi", ds.cashSbi, "yen");

    // 想定PL
    setText("detailPlRakuten", ds.plRakuten, "yen");
    setText("detailPlMatsui", ds.plMatsui, "yen");
    setText("detailPlSbi", ds.plSbi, "yen");

    // 想定損失
    setText("detailLossRakuten", ds.lossRakuten, "yen");
    setText("detailLossMatsui", ds.lossMatsui, "yen");
    setText("detailLossSbi", ds.lossSbi, "yen");

    // 合計
    const qtyTotal =
      (Number(ds.qtyRakuten || 0) || 0) +
      (Number(ds.qtyMatsui || 0) || 0) +
      (Number(ds.qtySbi || 0) || 0);
    const plTotal =
      (Number(ds.plRakuten || 0) || 0) +
      (Number(ds.plMatsui || 0) || 0) +
      (Number(ds.plSbi || 0) || 0);
    const lossTotal =
      (Number(ds.lossRakuten || 0) || 0) +
      (Number(ds.lossMatsui || 0) || 0) +
      (Number(ds.lossSbi || 0) || 0);

    setText("detailQtyTotal", qtyTotal, "int");
    setText("detailPlTotal", plTotal, "yen");
    setText("detailLossTotal", lossTotal, "yen");

    // 理由（AI）
    const ulAi = document.getElementById("detailReasonsAi");
    ulAi.innerHTML = "";
    if (ds.reasons){
      ds.reasons.split("||").forEach(function(t){
        t = (t || "").trim();
        if (!t) return;
        const li = document.createElement("li");
        li.textContent = t;
        ulAi.appendChild(li);
      });
    }

    // 理由（数量0など発注条件）
    const ulSizing = document.getElementById("detailReasonsSizing");
    ulSizing.innerHTML = "";
    if (ds.sizingReasons){
      ds.sizingReasons.split("||").forEach(function(t){
        t = (t || "").trim();
        if (!t) return;
        if (t[0] === "・"){
          t = t.slice(1).trim();
        }
        const li = document.createElement("li");
        li.textContent = t;
        ulSizing.appendChild(li);
      });
    }

    document.getElementById("detailConcern").textContent =
      ds.concern || "";

    // チャート描画
    renderChartFromRow(row);

    modal.classList.add("open");
    body.classList.add("modal-open");
  }

  table.querySelectorAll("tbody tr").forEach(function(row){
    row.addEventListener("click", function(){
      if (!this.dataset.code) return;
      openModal(this);
    });
  });

})();