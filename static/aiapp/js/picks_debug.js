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
  let resizeHandler = null;

  // ▼ チャート凡例は非表示（終値を別線で描いていないため）
  const chartLegend = document.querySelector(".chart-legend");
  if (chartLegend) {
    chartLegend.style.display = "none";
  }

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
      // 価格を小数第1位まで表示（Entry / TP / SL 用）
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
        // プラスでも "+" は付けない
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
  // 日付文字列 → BusinessDay 変換 ("YYYY-MM-DD" or "YYYY/MM/DD")
  // --------------------------------------
  function toBusinessDay(dateStr) {
    if (!dateStr) return null;
    const s = dateStr.replace(/\./g, "-").replace(/\//g, "-");
    const parts = s.split("-");
    if (parts.length !== 3) return null;
    const y = Number(parts[0]);
    const m = Number(parts[1]);
    const d = Number(parts[2]);
    if (!y || !m || !d) return null;
    return { year: y, month: m, day: d };
  }

  // --------------------------------------
  // lightweight-charts 用：チャート更新
  // --------------------------------------
  // candles: [{time, open, high, low, close}, ...]
  // closes: [number, ...] （candles が無いときのフォールバック）
  function updateChart(candles, closes, entry, tp, sl) {
    // 既存チャート破棄
    if (lwChart) {
      lwChart.remove();
      lwChart = null;
    }
    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
    }

    const hasCandles = Array.isArray(candles) && candles.length > 0;
    const hasCloses = Array.isArray(closes) && closes.length > 0;

    if (!hasCandles && !hasCloses) {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "flex";
      return;
    } else {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "none";
    }

    // ▼ カードの内側に左右余白を強制的に確保 & はみ出し隠す
    const INNER_PAD = 16; // px（左右とも）
    chartContainer.style.paddingLeft = INNER_PAD + "px";
    chartContainer.style.paddingRight = INNER_PAD + "px";
    chartContainer.style.boxSizing = "border-box";
    chartContainer.style.overflow = "hidden";

    // 内側の幅を取得（スクロール幅じゃなく clientWidth を使う）
    const containerInnerWidth = chartContainer.clientWidth || chartContainer.getBoundingClientRect().width || 0;
    let chartWidth = containerInnerWidth - INNER_PAD * 2;
    if (!isFinite(chartWidth) || chartWidth <= 0) {
      chartWidth = 320; // 最低幅の保険
    }

    const rect = chartContainer.getBoundingClientRect();
    const chartHeight = rect.height || 260;

    lwChart = LW.createChart(chartContainer, {
      width: chartWidth,
      height: chartHeight,
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
        textColor: "#e5edff",
        scaleMargins: {
          top: 0.15,
          bottom: 0.15,
        },
      },
      timeScale: {
        borderVisible: false,
        rightOffset: 2,
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
        priceFormat: {
          type: "price",
          precision: 0,
          minMove: 1,
        },
      });
      candleSeries.setData(candles);
      baseTimeList = candles.map((c) => c.time);
    } else if (hasCloses) {
      const line = lwChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
        priceFormat: {
          type: "price",
          precision: 0,
          minMove: 1,
        },
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
        priceFormat: {
          type: "price",
          precision: 0,
          minMove: 1,
        },
      });
      const data = baseTimeList.map((t) => ({
        time: t,
        value: num,
      }));
      series.setData(data);
      return series;
    }

    // Entry: 青, TP: 黄色, SL: 赤
    addHLine(entry, "#0ea5e9");
    addHLine(tp, "#eab308");
    addHLine(sl, "#ef4444");

    // 全体がカード内に収まるように自動フィット
    lwChart.timeScale().fitContent();

    // リサイズ対応（余白維持）
    resizeHandler = function () {
      if (!lwChart) return;
      const innerWidth = chartContainer.clientWidth || chartContainer.getBoundingClientRect().width || 0;
      let w = innerWidth - INNER_PAD * 2;
      if (!isFinite(w) || w <= 0) w = 320;
      const r = chartContainer.getBoundingClientRect();
      const h = r.height || 260;
      lwChart.applyOptions({
        width: w,
        height: h,
      });
    };
    window.addEventListener("resize", resizeHandler, { passive: true });
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

    // Entry / TP / SL（小数第1位で表示）
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

    // 想定損失（値はそのまま / 文字色は赤クラスを付与）
    setText("detailLossRakuten", ds.lossRakuten, "yen");
    setText("detailLossMatsui", ds.lossMatsui, "yen");
    setText("detailLossSbi", ds.lossSbi, "yen");

    ["detailLossRakuten", "detailLossMatsui", "detailLossSbi"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) {
        el.classList.add("detail-red");
      }
    });

    // 数量・想定利益・想定損失の「合計」行は不要なので非表示
    ["detailQtyTotal", "detailPlTotal", "detailLossTotal"].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      const rowEl = el.closest(".detail-row");
      if (rowEl) {
        rowEl.style.display = "none";
      }
      el.textContent = ""; // 念のため中身も消しておく
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
        const rawDate = dates[i] || null;
        const bd = rawDate ? toBusinessDay(rawDate) : null;
        const t = bd || (i + 1);
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
    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
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