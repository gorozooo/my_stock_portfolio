// aiapp/js/picks_debug.js
// AI Picks 診断：フィルタ + モーダル + ローソク足チャート

document.addEventListener("DOMContentLoaded", function(){

  // ===============================
  // 共通ユーティリティ
  // ===============================
  function parseNumber(v){
    if (v === undefined || v === null || v === "" || v === "NaN") return null;
    var n = Number(v);
    return isNaN(n) ? null : n;
  }

  // "1,2,3" / "[1,2,3]" → [1,2,3]
  function parseNumberArray(s){
    if (!s) return [];
    var txt = String(s).trim();
    try{
      if (txt[0] === "[" || txt[0] === "{"){
        var arr = JSON.parse(txt);
        if (!Array.isArray(arr)) return [];
        return arr.map(function(v){ return Number(v); }).filter(function(v){ return !isNaN(v); });
      }
      return txt.split(",").map(function(t){
        return Number(t.trim());
      }).filter(function(v){ return !isNaN(v); });
    }catch(e){
      return [];
    }
  }

  // OHLC: [[o,h,l,c], ...] か "o,h,l,c|o,h,l,c|..." を想定
  function parseOhlcArray(s){
    if (!s) return [];
    var txt = String(s).trim();
    var out = [];
    try{
      if (txt[0] === "["){
        var arr = JSON.parse(txt);
        if (Array.isArray(arr)){
          arr.forEach(function(row){
            if (!Array.isArray(row) || row.length < 4) return;
            var o = Number(row[0]);
            var h = Number(row[1]);
            var l = Number(row[2]);
            var c = Number(row[3]);
            if ([o,h,l,c].some(function(v){ return isNaN(v); })) return;
            out.push({o:o, h:h, l:l, c:c});
          });
        }
      }else{
        txt.split("|").forEach(function(seg){
          var parts = seg.split(",");
          if (parts.length < 4) return;
          var o = Number(parts[0]);
          var h = Number(parts[1]);
          var l = Number(parts[2]);
          var c = Number(parts[3]);
          if ([o,h,l,c].some(function(v){ return isNaN(v); })) return;
          out.push({o:o, h:h, l:l, c:c});
        });
      }
    }catch(e){
      out = [];
    }
    return out;
  }

  function setText(id, v, fmt){
    var el = document.getElementById(id);
    if (!el) return;
    if (v === undefined || v === null || v === "" || v === "NaN"){
      el.textContent = "–";
      return;
    }
    var txt = v;
    if (fmt === "int"){
      var n = Number(v);
      txt = isNaN(n) ? "–" : n.toLocaleString();
    }else if (fmt === "yen"){
      var n2 = Number(v);
      if (isNaN(n2)){
        txt = "–";
      }else{
        txt = n2.toLocaleString();
        if (n2 > 0) txt = "+" + txt;
      }
    }
    el.textContent = txt;
  }

  // ===============================
  // 一覧フィルタ
  // ===============================
  var input = document.getElementById("filterInput");
  var table = document.getElementById("picksTable");

  if (input && table){
    var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
    input.addEventListener("input", function(){
      var q = this.value.trim().toLowerCase();
      if (!q){
        rows.forEach(function(r){ r.style.display = ""; });
        return;
      }
      rows.forEach(function(r){
        var text = r.textContent.toLowerCase();
        r.style.display = text.indexOf(q) !== -1 ? "" : "none";
      });
    });
  }

  // ===============================
  // モーダル
  // ===============================
  var body   = document.body;
  var modal  = document.getElementById("pickModal");
  var closeBtn = document.getElementById("modalCloseBtn");
  if (!modal || !table){
    return;  // 必須要素がない場合は何もしない
  }

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
  // チャート
  // ===============================
  var chartCanvas = document.getElementById("pickChart");
  var chartEmpty  = document.getElementById("chartEmpty");
  var pickChart = null;

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

  // ローソク足プラグイン
  var candlePlugin = {
    id: "simpleCandles",
    afterDatasetsDraw: function(chart){
      var ohlc = chart.$ohlcData;
      if (!ohlc || !ohlc.length) return;

      var ctx = chart.ctx;
      var scales = chart.scales;
      var xScale = scales.x;
      var yScale = scales.y;
      if (!xScale || !yScale) return;

      var span = xScale.getPixelForValue(1) - xScale.getPixelForValue(0);
      var candleWidth = Math.max(3, span * 0.45);

      ctx.save();
      ctx.lineWidth = 1.2;

      ohlc.forEach(function(bar, idx){
        var x = xScale.getPixelForValue(idx);
        var yHigh  = yScale.getPixelForValue(bar.h);
        var yLow   = yScale.getPixelForValue(bar.l);
        var yOpen  = yScale.getPixelForValue(bar.o);
        var yClose = yScale.getPixelForValue(bar.c);

        var isUp = bar.c >= bar.o;
        var stroke = isUp ? "#4ade80" : "#f97373";
        var fill   = isUp ? "rgba(74,222,128,0.9)" : "rgba(248,113,113,0.9)";

        // ヒゲ
        ctx.strokeStyle = stroke;
        ctx.beginPath();
        ctx.moveTo(x, yHigh);
        ctx.lineTo(x, yLow);
        ctx.stroke();

        // 実体
        var top = Math.min(yOpen, yClose);
        var bottom = Math.max(yOpen, yClose);
        var h = Math.max(2, bottom - top);
        ctx.fillStyle = fill;
        ctx.fillRect(x - candleWidth / 2, top, candleWidth, h);
      });

      ctx.restore();
    }
  };

  function renderChartFromRow(row){
    // ここは何があっても throw しないようにする
    try{
      if (!chartCanvas || typeof Chart === "undefined"){
        return;
      }
      var ds = row.dataset || {};

      // OHLC / 終値
      var ohlc = parseOhlcArray(ds.chartOhlc || "");
      var closesRaw = parseNumberArray(ds.chartCloses || ds.chart || "");

      if (!ohlc.length && closesRaw.length){
        // OHLC 無し → 終値から擬似ローソク
        ohlc = closesRaw.map(function(v){
          return {o:v, h:v, l:v, c:v};
        });
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

      var n = ohlc.length;
      var labels = [];
      var closes = [];
      ohlc.forEach(function(b, i){
        labels.push(String(i + 1));
        closes.push(b.c);
      });

      var entryVal = parseNumber(ds.entry);
      var tpVal    = parseNumber(ds.tp);
      var slVal    = parseNumber(ds.sl);

      // y 範囲
      var values = [];
      ohlc.forEach(function(b){
        values.push(b.h, b.l);
      });
      if (entryVal !== null) values.push(entryVal);
      if (tpVal !== null) values.push(tpVal);
      if (slVal !== null) values.push(slVal);

      var yMin = Math.min.apply(null, values);
      var yMax = Math.max.apply(null, values);
      if (!isFinite(yMin) || !isFinite(yMax)){
        yMin = closes[0];
        yMax = closes[0];
      }
      if (yMin === yMax){
        yMin -= 1;
        yMax += 1;
      }
      var pad = (yMax - yMin) * 0.08;
      yMin -= pad;
      yMax += pad;

      if (pickChart){
        pickChart.destroy();
        pickChart = null;
      }

      var ctx = chartCanvas.getContext("2d");

      var datasets = [];

      // 終値ライン（細い青）
      datasets.push({
        label: "終値",
        data: closes,
        borderColor: "rgba(56,189,248,1)",
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.2
      });

      function mkHorizontal(value, label, color, dash){
        if (value === null) return null;
        return {
          label: label,
          data: Array(n).fill(value),
          borderColor: color,
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
          borderDash: dash || []
        };
      }

      var dsEntry = mkHorizontal(entryVal, "Entry", "#22c55e", [4,3]);
      var dsTp    = mkHorizontal(tpVal,    "TP",    "#22c55e", []);
      var dsSl    = mkHorizontal(slVal,    "SL",    "#ef4444", []);

      [dsEntry, dsTp, dsSl].forEach(function(d){
        if (d) datasets.push(d);
      });

      pickChart = new Chart(ctx, {
        type: "line",
        data: {
          labels: labels,
          datasets: datasets
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
                font: { size: 10 }
              }
            },
            tooltip: {
              enabled: false
            }
          },
          scales: {
            x: {
              display: false,
              grid: { display: false }
            },
            y: {
              display: true,
              position: "right",
              grid: { color: "rgba(30,64,175,0.35)" },
              ticks: {
                color: "#9ca3af",
                font: { size: 9 },
                callback: function(value){
                  var n = Number(value);
                  if (isNaN(n)) return "";
                  return n.toLocaleString();
                }
              },
              min: yMin,
              max: yMax
            }
          }
        },
        plugins: [candlePlugin]
      });

      // ローソク用データを Chart インスタンスにぶら下げる
      pickChart.$ohlcData = ohlc;
      pickChart.update();

    }catch(e){
      // ここでエラーを吸収して、モーダルだけは開くようにする
      console && console.error && console.error("chart error", e);
      showEmpty("チャート用データが不足しています");
    }
  }

  // ===============================
  // 行クリック → モーダル + チャート描画
  // ===============================
  var rowsForModal = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
  rowsForModal.forEach(function(row){
    row.addEventListener("click", function(){
      var ds = this.dataset || {};
      if (!ds.code) return;

      // タイトル等セット
      setText("modalTitle", (ds.code || "") + " " + (ds.name || ""));
      setText("modalSector", ds.sector || "");

      var scoreBadge = document.getElementById("modalScoreBadge");
      if (scoreBadge) scoreBadge.textContent = "Score: " + (ds.score || "–");
      var starBadge = document.getElementById("modalStarBadge");
      if (starBadge) starBadge.textContent = "★ " + (ds.stars || "–");

      // 各種数値
      setText("detailLast", ds.last, "int");
      setText("detailAtr", ds.atr, "int");

      setText("detailQtyRakuten", ds.qtyRakuten, "int");
      setText("detailQtyMatsui", ds.qtyMatsui, "int");
      setText("detailQtySbi", ds.qtySbi, "int");

      setText("detailEntry", ds.entry, "int");
      setText("detailTp", ds.tp, "int");
      setText("detailSl", ds.sl, "int");

      setText("detailCashRakuten", ds.cashRaketen, "yen"); // ← typo だったら後で直す
      setText("detailCashMatsui", ds.cashMatsui, "yen");
      setText("detailCashSbi", ds.cashSbi, "yen");

      setText("detailPlRakuten", ds.plRakuten, "yen");
      setText("detailPlMatsui", ds.plMatsui, "yen");
      setText("detailPlSbi", ds.plSbi, "yen");

      setText("detailLossRakuten", ds.lossRakuten, "yen");
      setText("detailLossMatsui", ds.lossMatsui, "yen");
      setText("detailLossSbi", ds.lossSbi, "yen");

      var qtyTotal =
        (Number(ds.qtyRakuten || 0) || 0) +
        (Number(ds.qtyMatsui || 0) || 0) +
        (Number(ds.qtySbi || 0) || 0);
      var plTotal =
        (Number(ds.plRakuten || 0) || 0) +
        (Number(ds.plMatsui || 0) || 0) +
        (Number(ds.plSbi || 0) || 0);
      var lossTotal =
        (Number(ds.lossRakuten || 0) || 0) +
        (Number(ds.lossMatsui || 0) || 0) +
        (Number(ds.lossSbi || 0) || 0);

      setText("detailQtyTotal", qtyTotal, "int");
      setText("detailPlTotal", plTotal, "yen");
      setText("detailLossTotal", lossTotal, "yen");

      // 理由（AI）
      var ulAi = document.getElementById("detailReasonsAi");
      if (ulAi){
        ulAi.innerHTML = "";
        if (ds.reasons){
          ds.reasons.split("||").forEach(function(t){
            t = (t || "").trim();
            if (!t) return;
            var li = document.createElement("li");
            li.textContent = t;
            ulAi.appendChild(li);
          });
        }
      }

      // 理由（数量0など）
      var ulSizing = document.getElementById("detailReasonsSizing");
      if (ulSizing){
        ulSizing.innerHTML = "";
        if (ds.sizingReasons){
          ds.sizingReasons.split("||").forEach(function(t){
            t = (t || "").trim();
            if (!t) return;
            if (t[0] === "・"){
              t = t.slice(1).trim();
            }
            var li2 = document.createElement("li");
            li2.textContent = t;
            ulSizing.appendChild(li2);
          });
        }
      }

      var concernEl = document.getElementById("detailConcern");
      if (concernEl){
        concernEl.textContent = ds.concern || "";
      }

      // チャート描画（エラーは内部で吸収）
      renderChartFromRow(this);

      modal.classList.add("open");
      body.classList.add("modal-open");
    });
  });

});