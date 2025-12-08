(function(){
  const input   = document.getElementById("filterInput");
  const table   = document.getElementById("picksTable");
  const modal   = document.getElementById("pickModal");
  const closeBtn= document.getElementById("modalCloseBtn");
  const body    = document.body;

  const chartCanvas = document.getElementById("picksChart");
  const chartEmpty  = document.getElementById("picksChartEmpty");

  // ---------------- フィルタ ----------------
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

  if (!modal || !table) return;

  // ---------------- ユーティリティ ----------------
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

  // ---------------- チャート描画 ----------------
  function drawChart(ds){
    if (!chartCanvas || !chartEmpty) return;

    const raw = ds.chartCloses || "";
    if (!raw){
      chartCanvas.style.display = "none";
      chartEmpty.style.display = "flex";
      return;
    }

    const closes = raw.split(",")
      .map(s => Number(s.trim()))
      .filter(v => Number.isFinite(v));

    if (closes.length < 2){
      chartCanvas.style.display = "none";
      chartEmpty.style.display = "flex";
      return;
    }

    const entry = Number(ds.entry || "NaN");
    const tp    = Number(ds.tp || "NaN");
    const sl    = Number(ds.sl || "NaN");

    const ys = closes.slice();
    [entry, tp, sl].forEach(v => {
      if (Number.isFinite(v)) ys.push(v);
    });

    let minY = Math.min.apply(null, ys);
    let maxY = Math.max.apply(null, ys);
    if (!Number.isFinite(minY) || !Number.isFinite(maxY)){
      minY = closes[0];
      maxY = closes[0];
    }
    if (minY === maxY){
      minY -= 1;
      maxY += 1;
    }
    const pad = (maxY - minY) * 0.05 || 1;
    const yMin = minY - pad;
    const yMax = maxY + pad;

    const ctx = chartCanvas.getContext("2d");
    const rect = chartCanvas.getBoundingClientRect();
    const width = rect.width || 600;
    const height = rect.height || 180;
    chartCanvas.width = width * window.devicePixelRatio;
    chartCanvas.height = height * window.devicePixelRatio;
    ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);

    // 左右の余白を少し広げて、右端に価格ラベル用スペースを確保
    const padX = 24;
    const padY = 12;

    function sx(i){
      if (closes.length === 1) return width / 2;
      const t = i / (closes.length - 1);
      return padX + t * (width - padX * 2);
    }
    function sy(v){
      const t = (v - yMin) / (yMax - yMin);
      return height - padY - t * (height - padY * 2);
    }

    // 背景
    ctx.clearRect(0, 0, width, height);

    // 価格目盛り＋横グリッド
    ctx.save();
    const nticks = 4; // 上・下を含めて4本
    ctx.font = "10px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
    ctx.fillStyle = "rgba(148,163,184,0.95)";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = "rgba(51,65,85,0.7)";
    ctx.lineWidth = 1;
    ctx.setLineDash([2,4]);

    for (let i = 0; i < nticks; i++){
      const t = i / (nticks - 1);
      const val = yMin + (yMax - yMin) * t;
      const y = sy(val);

      // グリッドライン
      ctx.beginPath();
      ctx.moveTo(padX, y);
      ctx.lineTo(width - padX, y);
      ctx.stroke();

      // 価格ラベル（右端ちょい内側）
      const label = Math.round(val).toLocaleString();
      ctx.fillText(label, width - padX - 4, y);
    }
    ctx.restore();

    // 終値ライン
    ctx.save();
    ctx.setLineDash([]);
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.beginPath();
    closes.forEach((v, i) => {
      const x = sx(i);
      const y = sy(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.restore();

    // 最新終値マーカー
    const lastIdx = closes.length - 1;
    const lastX = sx(lastIdx);
    const lastY = sy(closes[lastIdx]);
    ctx.save();
    ctx.fillStyle = "#38bdf8";
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(15,23,42,0.9)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    // Entry / TP / SL の水平ライン
    function hLine(val, color, dash){
      if (!Number.isFinite(val)) return;
      const y = sy(val);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.setLineDash(dash);
      ctx.globalAlpha = 0.95;
      ctx.beginPath();
      ctx.moveTo(padX, y);
      ctx.lineTo(width - padX, y);
      ctx.stroke();
      ctx.restore();
    }

    hLine(entry, "#e5e7eb", [3,4]);  // Entry
    hLine(tp,    "#22c55e", [5,5]);  // TP
    hLine(sl,    "#ef4444", [5,3]);  // SL

    chartCanvas.style.display = "block";
    chartEmpty.style.display = "none";
  }

  // ---------------- モーダル表示 ----------------
  function openModal(row){
    const ds = row.dataset || {};

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
    drawChart(ds);

    modal.classList.add("open");
    body.classList.add("modal-open");
  }

  function closeModal(){
    modal.classList.remove("open");
    body.classList.remove("modal-open");
  }

  // 行クリック
  table.querySelectorAll("tbody tr").forEach(function(row){
    row.addEventListener("click", function(){
      if (!this.dataset.code) return;
      openModal(this);
    });
  });

  // モーダル外クリックで閉じる
  modal.addEventListener("click", function(e){
    if (e.target === modal){
      closeModal();
    }
  });
  if (closeBtn){
    closeBtn.addEventListener("click", closeModal);
  }
})();