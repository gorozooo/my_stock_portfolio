// aiapp/static/aiapp/js/picks_debug.js

(function(){
  const input = document.getElementById("filterInput");
  const table = document.getElementById("picksTable");

  // ------- フィルタ -------
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

  const body   = document.body;
  const modal  = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");
  if (!modal || !table) return;

  let detailChart = null;

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

  function drawChartFromDataset(ds){
    const canvas = document.getElementById("detailChart");
    const emptyBox = document.getElementById("detailChartEmpty");

    if (!canvas){
      return;
    }

    let raw = ds.chartCloses || "";
    let closes = [];

    if (raw){
      closes = raw.split(",").map(function(x){
        const n = Number(x.trim());
        return isNaN(n) ? null : n;
      }).filter(function(v){ return v !== null; });
    }

    // エラー種別別にメッセージを変える
    let errorType = null;
    if (!raw){
      errorType = "no-data";
    } else if (!window.Chart){
      errorType = "no-lib";
    } else if (!closes || closes.length < 2){
      errorType = "short-data";
    }

    if (errorType){
      if (detailChart){
        detailChart.destroy();
        detailChart = null;
      }
      if (emptyBox){
        if (errorType === "no-data"){
          emptyBox.textContent = "チャート用データが不足しています";
        }else if (errorType === "no-lib"){
          emptyBox.textContent = "Chart.js の読み込みに失敗したためチャートを表示できません";
        }else{
          emptyBox.textContent = "データ本数が少ないためチャートを表示できません";
        }
        emptyBox.style.display = "flex";
      }
      return;
    }

    // ここまで来たら Chart.js + 十分なデータあり
    if (emptyBox){
      emptyBox.style.display = "none";
    }

    const ctx = canvas.getContext("2d");
    if (detailChart){
      detailChart.destroy();
    }

    const labels = closes.map(function(_, idx){ return idx + 1; });

    detailChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          data: closes,
          borderWidth: 1.5,
          tension: 0.25,
          pointRadius: 0,
          pointHitRadius: 6,
          fill: false
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { display: false },
          y: {
            display: true,
            grid: { display: false }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false }
        }
      }
    });
  }

  function openModal(row){
    const ds = row.dataset || {};

    // タイトル・バッジ
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

    // チャート描画
    drawChartFromDataset(ds);

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

    modal.classList.add("open");
    body.classList.add("modal-open");
  }

  function closeModal(){
    modal.classList.remove("open");
    body.classList.remove("modal-open");
  }

  table.querySelectorAll("tbody tr").forEach(function(row){
    row.addEventListener("click", function(){
      if (!this.dataset.code) return;
      openModal(this);
    });
  });

  modal.addEventListener("click", function(e){
    if (e.target === modal){
      closeModal();
    }
  });

  if (closeBtn){
    closeBtn.addEventListener("click", closeModal);
  }
})();