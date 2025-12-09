// aiapp/static/aiapp/js/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - lightweight-charts で本物ローソク足 + Entry/TP/SL

(function () {
  const table = document.getElementById("picksTable");
  const filterInput = document.getElementById("filterInput");

  const body = document.body;
  const modal = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");

  const chartContainer = document.getElementById("detailChartContainer");
  const chartEmptyLabel = document.getElementById("chartEmptyLabel");

  let lwChart = null;

  if (!table || !modal || !chartContainer) {
    return;
  }

  if (typeof window.LightweightCharts === "undefined") {
    console.warn("LightweightCharts is not loaded.");
    return;
  }
  const LW = window.LightweightCharts;

  // --------------------------------------
  // 共通フォーマッタ
  // --------------------------------------
  function setText(id, value, fmt) {
    const el = document.getElementById(id);
    if (!el) return;

    if (value === undefined || value === null || value === "" || value === "NaN") {
      el.textContent = "–";
      return;
    }

    let txt = value;

    if (fmt === "int") {
      const n = Number(value);
      txt = isNaN(n) ? "–" : n.toLocaleString();
    } else if (fmt === "price1") {
      // 小数第1位まで表示（Entry / TP / SL 用）
      const n = Number(value);
      if (isNaN(n)) {
        txt = "–";
      } else {
        txt = n.toLocaleString(undefined, {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        });
      }
    } else if (fmt === "yen") {
      const n = Number(value);
      if (isNaN(n)) {
        txt = "–";
      } else {
        // プラスのときも "+" を付けない
        txt = n.toLocaleString();
      }
    }

    el.textContent = txt;
  }

  function toNumberOrNull(v) {
    if (v === undefined || v === null || v === "") return null;
    const n = Number(v);
    return isNaN(n) ? null : n;
  }

  // --------------------------------------
  // フィルタ（コード・銘柄名・業種）
  // --------------------------------------
  (function setupFilter() {
    if (!filterInput || !table) return;

    const rows = Array.from(table.querySelectorAll("tbody tr"));

    filterInput.addEventListener("input", function () {
      const q = this.value.trim().toLowerCase();
      if (!q) {
        rows.forEach((r) => (r.style.display = ""));
        return;
      }

      rows.forEach((r) => {
        const text = r.textContent.toLowerCase();
        r.style.display = text.includes(q) ? "" : "none";
      });
    });
  })();

  // --------------------------------------
  // lightweight-charts 用：チャート更新
  // --------------------------------------
  // candles: [{time, open, high, low, close}, ...]
  // closes: [number, ...] （candlesが無いときのフォールバック）
  function updateChart(candles, closes, entry, tp, sl) {
    if (lwChart) {
      lwChart.remove();
      lwChart = null;
    }

    const hasCandles = Array.isArray(candles) && candles.length > 0;
    const hasCloses = Array.isArray(closes) && closes.length > 0;

    if (!hasCandles && !hasCloses) {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "flex";
      return;
    } else {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "none";
    }

    const rect = chartContainer.getBoundingClientRect();
    const width = rect.width || 600;
    const height = rect.height || 260;

    lwChart = LW.createChart(chartContainer, {
      width: width,
      height: height,
      layout: {
        background: { type: "solid", color: "rgba(15,23,42,0)" },
        textColor: "#e5edff",
      },
      grid: {
        vertLines: { color: "rgba(148,163,184,0.16)" },
        horzLines: { color: "rgba(148,163,184,0.24)" },
      },
      rightPriceScale: {
        visible: true,
        borderVisible: false,
        scaleMargins: {
          top: 0.15,
          bottom: 0.15,
        },
      },
      timeScale: {
        borderVisible: false,
        rightOffset: 1,
        barSpacing: 7,
      },
      crosshair: {
        mode: LW.CrosshairMode.Normal,
      },
      localization: {
        priceFormatter: (price) => {
          const n = Number(price);
          if (isNaN(n)) return "";
          return n.toLocaleString();
        },
      },
    });

    let baseTimeList = [];

    if (hasCandles) {
      const candleSeries = lwChart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#9ca3af",
        wickDownColor: "#9ca3af",
      });
      candleSeries.setData(candles);
      baseTimeList = candles.map((c) => c.time);
    } else if (hasCloses) {
      const line = lwChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
      });
      const data = closes.map((v, i) => ({
        time: i + 1,
        value: v,
      }));
      line.setData(data);
      baseTimeList = data.map((d) => d.time);
    }

    function addHLine(value, color) {
      if (value === null || value === undefined) return null;
      const num = Number(value);
      if (isNaN(num)) return null;
      const series = lwChart.addLineSeries({
        color: color,
        lineWidth: 1,
        lineStyle: LW.LineStyle.Dashed,
      });
      const data = baseTimeList.map((t) => ({
        time: t,
        value: num,
      }));
      series.setData(data);
      return series;
    }

    // Entry=緑, TP=黄色, SL=赤 でハッキリ区別
    addHLine(entry, "#22c55e");
    addHLine(tp, "#eab308");
    addHLine(sl, "#ef4444");

    lwChart.timeScale().fitContent();

    // リサイズ対応
    window.addEventListener(
      "resize",
      function handleResize() {
        if (!lwChart) return;
        const r = chartContainer.getBoundingClientRect();
        lwChart.applyOptions({
          width: r.width || 600,
          height: r.height || 260,
        });
      },
      { passive: true }
    );
  }

  // --------------------------------------
  // モーダル表示
  // --------------------------------------
  function openModal(row) {
    const ds = row.dataset || {};

    // タイトル / メタ
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

    // Entry / TP / SL（小数第1位）
    setText("detailEntry", ds.entry, "price1");
    setText("detailTp", ds.tp, "price1");
    setText("detailSl", ds.sl, "price1");

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

    // 数量・想定利益・想定損失の「合計」行は非表示にする
    ["detailQtyTotal", "detailPlTotal", "detailLossTotal"].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      const rowEl = el.closest(".detail-row");
      if (rowEl) {
        rowEl.style.display = "none";
      }
    });

    // 理由（AI）
    const ulAi = document.getElementById("detailReasonsAi");
    if (ulAi) {
      ulAi.innerHTML = "";
      const reasons = ds.reasons || "";
      if (reasons) {
        reasons.split("||").forEach(function (t) {
          t = (t || "").trim();
          if (!t) return;
          const li = document.createElement("li");
          li.textContent = t;
          ulAi.appendChild(li);
        });
      }
    }

    // 理由（数量0など発注条件）
    const ulSizing = document.getElementById("detailReasonsSizing");
    if (ulSizing) {
      ulSizing.innerHTML = "";
      const sReasons = ds.sizingReasons || "";
      if (sReasons) {
        sReasons.split("||").forEach(function (t) {
          t = (t || "").trim();
          if (!t) return;
          if (t[0] === "・") {
            t = t.slice(1).trim();
          }
          const li = document.createElement("li");
          li.textContent = t;
          ulSizing.appendChild(li);
        });
      }
    }

    // 懸念
    const concernEl = document.getElementById("detailConcern");
    if (concernEl) {
      concernEl.textContent = ds.concern || "";
    }

    // ------------- チャート用データ（OHLC + 日付） -------------
    const openStr = ds.chartOpen || "";
    const highStr = ds.chartHigh || "";
    const lowStr = ds.chartLow || "";
    const closeStr = ds.chartClose || "";
    const datesStr = ds.chartDates || "";

    const opens = openStr
      ? openStr.split(",").map((s) => Number(s.trim())).filter((v) => !isNaN(v))
      : [];
    const highs = highStr
      ? highStr.split(",").map((s) => Number(s.trim())).filter((v) => !isNaN(v))
      : [];
    const lows = lowStr
      ? lowStr.split(",").map((s) => Number(s.trim())).filter((v) => !isNaN(v))
      : [];
    const closes = closeStr
      ? closeStr.split(",").map((s) => Number(s.trim())).filter((v) => !isNaN(v))
      : [];
    const dates = datesStr
      ? datesStr.split(",").map((s) => s.trim()).filter((s) => s.length > 0)
      : [];

    let candles = [];
    const len = Math.min(opens.length, highs.length, lows.length, closes.length);
    for (let i = 0; i < len; i++) {
      const o = opens[i];
      const h = highs[i];
      const l = lows[i];
      const c = closes[i];
      if (
        typeof o === "number" &&
        typeof h === "number" &&
        typeof l === "number" &&
        typeof c === "number"
      ) {
        const t = dates[i] || (i + 1); // 日付があれば "YYYY-MM-DD"、なければインデックス
        candles.push({
          time: t,
          open: o,
          high: h,
          low: l,
          close: c,
        });
      }
    }

    const entry = toNumberOrNull(ds.entry);
    const tp = toNumberOrNull(ds.tp);
    const sl = toNumberOrNull(ds.sl);

    updateChart(candles, closes, entry, tp, sl);

    modal.classList.add("show");
    body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.remove("show");
    body.classList.remove("modal-open");

    if (lwChart) {
      lwChart.remove();
      lwChart = null;
    }
  }

  // 行クリックでモーダル表示
  table.querySelectorAll("tbody tr.pick-row").forEach(function (row) {
    row.addEventListener("click", function () {
      if (!this.dataset.code) return;
      openModal(this);
    });
  });

  // モーダル外クリックで閉じる
  modal.addEventListener("click", function (e) {
    if (e.target === modal) {
      closeModal();
    }
  });

  // 閉じるボタン
  if (closeBtn) {
    closeBtn.addEventListener("click", closeModal);
  }

  // ESC キーで閉じる
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal.classList.contains("show")) {
      closeModal();
    }
  });
})();