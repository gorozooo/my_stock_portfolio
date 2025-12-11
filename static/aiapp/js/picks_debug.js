// aiapp/static/aiapp/js/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - lightweight-charts でローソク足 + 終値 + MA + VWAP + Entry/TP/SL

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

  // ★ 現在開いている銘柄の価格表示モード
  //   "int"       : 価格は整数
  //   "decimal1"  : 価格は小数1桁
  let currentPriceMode = "int";

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
      const n = Number(value);
      if (isNaN(n)) {
        txt = "–";
      } else {
        txt = n.toLocaleString(undefined, {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        });
      }
    } else if (fmt === "priceAuto") {
      const raw = String(value).trim();
      const n0 = Number(raw);
      if (isNaN(n0)) {
        txt = "–";
      } else {
        if (currentPriceMode === "decimal1") {
          const n = Math.round(n0 * 10) / 10;
          if (Number.isInteger(n)) {
            txt = n.toLocaleString();
          } else {
            txt = n.toLocaleString(undefined, {
              minimumFractionDigits: 1,
              maximumFractionDigits: 1,
            });
          }
        } else {
          const n = Math.round(n0);
          txt = n.toLocaleString();
        }
      }
    } else if (fmt === "yen") {
      const n = Number(value);
      if (isNaN(n)) {
        txt = "–";
      } else {
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
  // closes: [number, ...]
  // maShort, maMid, vwap: [number | null, ...]
  function updateChart(candles, closes, entry, tp, sl, maShort, maMid, vwap) {
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

    // コンテナサイズ
    const rect = chartContainer.getBoundingClientRect();
    const chartWidth = rect.width > 0 ? rect.width : 320;
    const chartHeight = rect.height > 0 ? rect.height : 260;

    const pricePrecision = currentPriceMode === "decimal1" ? 1 : 0;
    const minMove = currentPriceMode === "decimal1" ? 0.1 : 1;

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
          return n.toLocaleString(undefined, {
            minimumFractionDigits: pricePrecision,
            maximumFractionDigits: pricePrecision,
          });
        },
      },
    });

    // ベースの時間軸
    let timeList = [];

    // ローソク足
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
          precision: pricePrecision,
          minMove: minMove,
        },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      candleSeries.setData(candles);
      timeList = candles.map((c) => c.time);
    } else if (hasCloses) {
      // ローソクが無い場合のフォールバック（終値だけ）
      timeList = closes.map((_, i) => i + 1);
    }

    // 終値ライン（「終値」の凡例用）
    if (hasCloses && timeList.length > 0) {
      const closeSeries = lwChart.addLineSeries({
        color: "#e5edff",
        lineWidth: 1.5,
        priceFormat: {
          type: "price",
          precision: pricePrecision,
          minMove: minMove,
        },
      });
      const dataClose = [];
      const len = Math.min(closes.length, timeList.length);
      for (let i = 0; i < len; i++) {
        const v = Number(closes[i]);
        if (!isFinite(v)) continue;
        dataClose.push({ time: timeList[i], value: v });
      }
      if (dataClose.length > 0) {
        closeSeries.setData(dataClose);
      }
    }

    // オーバーレイ用ヘルパ
    function addOverlayLine(values, color, width) {
      if (!Array.isArray(values) || values.length === 0) return null;
      if (!Array.isArray(timeList) || timeList.length === 0) return null;

      const len = Math.min(values.length, timeList.length);
      const data = [];

      for (let i = 0; i < len; i++) {
        const raw = values[i];
        if (raw === null || raw === undefined || raw === "" || raw === "NaN") continue;
        const n = Number(raw);
        if (!isFinite(n)) continue;
        data.push({
          time: timeList[i],
          value: n,
        });
      }

      if (data.length === 0) return null;

      const series = lwChart.addLineSeries({
        color: color,
        lineWidth: width,
        priceFormat: {
          type: "price",
          precision: pricePrecision,
          minMove: minMove,
        },
      });
      series.setData(data);
      return series;
    }

    // MA / VWAP オーバーレイ
    addOverlayLine(maShort, "#38bdf8", 1.5); // 短期MA
    addOverlayLine(maMid, "#6366f1", 1.5);   // 中期MA
    addOverlayLine(vwap, "#f97316", 1.5);    // VWAP

    // 水平線（Entry, TP, SL）
    function addHLine(value, color) {
      const n = Number(value);
      if (!isFinite(n) || !Array.isArray(timeList) || timeList.length === 0) return null;
      const series = lwChart.addLineSeries({
        color: color,
        lineWidth: 1,
        lineStyle: LW.LineStyle.Dashed,
        priceFormat: {
          type: "price",
          precision: pricePrecision,
          minMove: minMove,
        },
      });
      const data = timeList.map((t) => ({ time: t, value: n }));
      series.setData(data);
      return series;
    }

    // Entry: 黄色, TP: 緑, SL: 赤
    addHLine(entry, "#eab308");
    addHLine(tp, "#22c55e");
    addHLine(sl, "#ef4444");

    lwChart.timeScale().fitContent();

    // リサイズ対応
    resizeHandler = function () {
      if (!lwChart) return;
      const r = chartContainer.getBoundingClientRect();
      const w = r.width > 0 ? r.width : 320;
      const h = r.height > 0 ? r.height : 260;
      lwChart.applyOptions({ width: w, height: h });
    };
    window.addEventListener("resize", resizeHandler, { passive: true });
  }

  // --------------------------------------
  // モーダル表示
  // --------------------------------------
  function openModal(row) {
    const ds = row.dataset || {};

    // 現在値から整数/小数判定
    (function decidePriceMode() {
      const raw = (ds.last || "").toString().trim();
      let mode = "int";
      if (raw) {
        const dot = raw.indexOf(".");
        if (dot >= 0) {
          const decimals = raw
            .slice(dot + 1)
            .replace(/0+$/, "")
            .length;
          if (decimals >= 1) mode = "decimal1";
        }
      }
      currentPriceMode = mode;
    })();

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

    // Entry / TP / SL
    setText("detailEntry", ds.entry, "priceAuto");
    setText("detailTp", ds.tp, "priceAuto");
    setText("detailSl", ds.sl, "priceAuto");

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

    ["detailLossRakuten", "detailLossMatsui", "detailLossSbi"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) {
        el.classList.add("detail-red");
      }
    });

    // 合計行は非表示
    ["detailQtyTotal", "detailPlTotal", "detailLossTotal"].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      const rowEl = el.closest(".detail-row");
      if (rowEl) {
        rowEl.style.display = "none";
      }
      el.textContent = "";
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

    // 理由（発注条件）
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

    // ------------- チャート用データ（OHLC + 日付 + MA + VWAP） -------------
    const openStr = ds.chartOpen || "";
    const highStr = ds.chartHigh || "";
    const lowStr = ds.chartLow || "";
    const closeStr = ds.chartClose || "";
    const datesStr = ds.chartDates || "";

    const maShortStr = ds.chartMaShort || "";
    const maMidStr = ds.chartMaMid || "";
    const vwapStr = ds.chartVwap || "";
    // RSI は今のところグラフには載せず（将来別パネルにする想定）

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

    const maShort = maShortStr
      ? maShortStr.split(",").map((s) => {
          const n = Number(s.trim());
          return isNaN(n) ? null : n;
        })
      : [];
    const maMid = maMidStr
      ? maMidStr.split(",").map((s) => {
          const n = Number(s.trim());
          return isNaN(n) ? null : n;
        })
      : [];
    const vwap = vwapStr
      ? vwapStr.split(",").map((s) => {
          const n = Number(s.trim());
          return isNaN(n) ? null : n;
        })
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

    updateChart(candles, closes, entry, tp, sl, maShort, maMid, vwap);

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