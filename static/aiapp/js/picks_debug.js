// aiapp/static/aiapp/js/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - lightweight-charts で
//    上段: ローソク足 + 5/25/75/100/200MA + VWAP + Entry/TP/SL + 52週/上場来ライン
//    下段: RSI 専用パネル
//    凡例: 終値 / 各MA / VWAP / 52週高安値 / 上場来高安値

(function () {
  const table = document.getElementById("picksTable");
  const filterInput = document.getElementById("filterInput");

  const body = document.body;
  const modal = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");

  const chartWrapper = document.getElementById("detailChartContainer");
  const priceContainer = document.getElementById("detailChartPriceContainer");
  const rsiContainer = document.getElementById("detailChartRsiContainer");
  const chartEmptyLabel = document.getElementById("chartEmptyLabel");

  // 凡例の数値表示用
  const legendCloseVal   = document.getElementById("legendCloseValue");
  const legendMa5Val     = document.getElementById("legendMa5Value");
  const legendMa25Val    = document.getElementById("legendMa25Value");
  const legendMa75Val    = document.getElementById("legendMa75Value");
  const legendMa100Val   = document.getElementById("legendMa100Value");
  const legendMa200Val   = document.getElementById("legendMa200Value");
  const legendVwapVal    = document.getElementById("legendVwapValue");
  const legendHi52wVal   = document.getElementById("legendHi52wValue");
  const legendLo52wVal   = document.getElementById("legendLo52wValue");
  const legendHiAllVal   = document.getElementById("legendHiAllValue");
  const legendLoAllVal   = document.getElementById("legendLoAllValue");

  let priceChart = null;
  let rsiChart = null;
  let resizeHandler = null;

  // 価格表示モード
  //   "int"       : 価格は整数
  //   "decimal1"  : 価格は小数1桁
  let currentPriceMode = "int";

  if (!table || !modal || !chartWrapper || !priceContainer) {
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

  // 価格フォーマット（チャート用）
  function getPriceFormat() {
    if (currentPriceMode === "decimal1") {
      return { type: "price", precision: 1, minMove: 0.1 };
    }
    return { type: "price", precision: 0, minMove: 1 };
  }

  // 凡例用：価格フォーマット
  function formatPriceForLegend(v) {
    if (v === null || v === undefined || isNaN(Number(v))) return "–";
    const n0 = Number(v);
    if (currentPriceMode === "decimal1") {
      const n = Math.round(n0 * 10) / 10;
      if (Number.isInteger(n)) {
        return n.toLocaleString();
      }
      return n.toLocaleString(undefined, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
    } else {
      const n = Math.round(n0);
      return n.toLocaleString();
    }
  }

  // 配列の末尾から有効な数値を探す
  function getLatestNumber(arr) {
    if (!Array.isArray(arr) || arr.length === 0) return null;
    for (let i = arr.length - 1; i >= 0; i--) {
      const v = arr[i];
      if (v === null || v === undefined) continue;
      const n = Number(v);
      if (!isNaN(n)) return n;
    }
    return null;
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
  // 日付文字列 → BusinessDay 変換
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
  // チャート更新
  // --------------------------------------
  function updateChart(
    candles,
    closes,
    entry,
    tp,
    sl,
    ma5,
    ma25,
    ma75,
    ma100,
    ma200,
    vwap,
    rsiValues,
    hi52w,
    lo52w,
    hiAll,
    loAll
  ) {
    if (priceChart) {
      priceChart.remove();
      priceChart = null;
    }
    if (rsiChart) {
      rsiChart.remove();
      rsiChart = null;
    }
    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
    }

    const hasCandles = Array.isArray(candles) && candles.length > 0;
    const hasCloses = Array.isArray(closes) && closes.length > 0;

    if (!hasCandles && !hasCloses) {
      if (chartWrapper) chartWrapper.style.display = "none";
      if (chartEmptyLabel) chartEmptyLabel.style.display = "flex";
      if (rsiContainer) rsiContainer.style.display = "none";
      return;
    } else {
      if (chartWrapper) chartWrapper.style.display = "block";
      if (chartEmptyLabel) chartEmptyLabel.style.display = "none";
    }

    const baseWidth =
      chartWrapper.clientWidth ||
      chartWrapper.getBoundingClientRect().width ||
      priceContainer.clientWidth ||
      320;

    const PRICE_HEIGHT = 230;
    const RSI_HEIGHT = 90;

    const priceFormat = getPriceFormat();

    // 上段: 価格チャート
    priceChart = LW.createChart(priceContainer, {
      width: baseWidth,
      height: PRICE_HEIGHT,
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
          if (currentPriceMode === "decimal1") {
            const x = Math.round(n * 10) / 10;
            if (Number.isInteger(x)) return x.toLocaleString();
            return x.toLocaleString(undefined, {
              minimumFractionDigits: 1,
              maximumFractionDigits: 1,
            });
          }
          return Math.round(n).toLocaleString();
        },
      },
    });

    let baseTimeList = [];

    if (hasCandles) {
      const candleSeries = priceChart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#9ca3af",
        wickDownColor: "#9ca3af",
        priceFormat: priceFormat,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      candleSeries.setData(candles);
      baseTimeList = candles.map((c) => c.time);
    } else if (hasCloses) {
      const line = priceChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
        priceFormat: priceFormat,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      const data = closes.map((v, i) => ({
        time: i + 1,
        value: v,
      }));
      line.setData(data);
      baseTimeList = data.map((d) => d.time);
    }

    // オーバーレイ用ヘルパ
    function addOverlayLine(values, color) {
      if (!Array.isArray(values) || values.length === 0) return null;
      if (!Array.isArray(baseTimeList) || baseTimeList.length === 0) return null;

      const len = Math.min(values.length, baseTimeList.length);
      if (!len) return null;

      const data = [];
      const offsetV = values.length - len;
      const offsetT = baseTimeList.length - len;

      for (let i = 0; i < len; i++) {
        const vRaw = values[offsetV + i];
        const n = Number(vRaw);
        if (isNaN(n)) continue;
        const t = baseTimeList[offsetT + i];
        data.push({ time: t, value: n });
      }

      if (data.length === 0) return null;

      const series = priceChart.addLineSeries({
        color: color,
        lineWidth: 1.5,
        priceFormat: priceFormat,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      series.setData(data);
      return series;
    }

    // MA / VWAP
    addOverlayLine(ma5,   "#22d3ee"); // 5MA
    addOverlayLine(ma25,  "#f97316"); // 25MA
    addOverlayLine(ma75,  "#a855f7"); // 75MA
    addOverlayLine(ma100, "#22c55e"); // 100MA
    addOverlayLine(ma200, "#e5e7eb"); // 200MA
    addOverlayLine(vwap,  "#facc15"); // VWAP

    // Entry / TP / SL（水平線と右ラベルあり）
    function addHLine(value, color) {
      if (value === null || value === undefined) return null;
      const num = Number(value);
      if (isNaN(num)) return null;
      const series = priceChart.addLineSeries({
        color: color,
        lineWidth: 1,
        lineStyle: LW.LineStyle.Dashed,
        priceFormat: priceFormat,
        lastValueVisible: true,
        priceLineVisible: true,
      });
      const data = baseTimeList.map((t) => ({
        time: t,
        value: num,
      }));
      series.setData(data);
      return series;
    }

    addHLine(entry, "#eab308"); // Entry
    addHLine(tp,    "#22c55e"); // TP
    addHLine(sl,    "#ef4444"); // SL

    // 52週 / 上場来 高安値（ラベルなしの補助ライン）
    function addRefLine(value, color) {
      if (value === null || value === undefined) return null;
      const num = Number(value);
      if (isNaN(num)) return null;
      const series = priceChart.addLineSeries({
        color: color,
        lineWidth: 1,
        lineStyle: LW.LineStyle.Dotted,
        priceFormat: priceFormat,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      const data = baseTimeList.map((t) => ({ time: t, value: num }));
      series.setData(data);
      return series;
    }

    addRefLine(hi52w,  "rgba(96,165,250,0.7)");
    addRefLine(lo52w,  "rgba(96,165,250,0.7)");
    addRefLine(hiAll,  "rgba(248,250,252,0.8)");
    addRefLine(loAll,  "rgba(248,250,252,0.8)");

    priceChart.timeScale().fitContent();

    // 下段: RSI
    const hasRsi = Array.isArray(rsiValues) && rsiValues.length > 0;
    if (rsiContainer) {
      rsiContainer.style.display = hasRsi ? "block" : "none";
    }

    if (hasRsi && rsiContainer && baseTimeList.length > 0) {
      rsiChart = LW.createChart(rsiContainer, {
        width: baseWidth,
        height: RSI_HEIGHT,
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
          rightOffset: 2,
          barSpacing: 7,
        },
        crosshair: {
          mode: LW.CrosshairMode.Normal,
        },
        localization: {
          priceFormatter: (v) => {
            const n = Number(v);
            if (isNaN(n)) return "";
            return n.toFixed(1);
          },
        },
      });

      const len = Math.min(rsiValues.length, baseTimeList.length);
      const offsetV = rsiValues.length - len;
      const offsetT = baseTimeList.length - len;

      const rsiData = [];
      for (let i = 0; i < len; i++) {
        const raw = rsiValues[offsetV + i];
        const n = typeof raw === "number" ? raw : Number(raw);
        if (isNaN(n)) continue;
        const t = baseTimeList[offsetT + i];
        rsiData.push({ time: t, value: n });
      }

      const rsiSeries = rsiChart.addLineSeries({
        color: "#facc15",
        lineWidth: 2,
        priceFormat: {
          type: "price",
          precision: 1,
          minMove: 0.1,
        },
        lastValueVisible: true,   // 右側の小さい黄色ラベル
        priceLineVisible: false,
      });
      rsiSeries.setData(rsiData);

      // 30 / 50 / 70 の水平ライン
      function addRsiRef(level, color) {
        const v = Number(level);
        if (!isFinite(v)) return;
        const data = baseTimeList.map((t) => ({ time: t, value: v }));
        const s = rsiChart.addLineSeries({
          color: color,
          lineWidth: 1,
          lineStyle: LW.LineStyle.Dashed,
          priceFormat: {
            type: "price",
            precision: 1,
            minMove: 0.1,
          },
          lastValueVisible: false,
          priceLineVisible: false,
        });
        s.setData(data);
      }
      addRsiRef(30, "rgba(248,250,252,0.45)");
      addRsiRef(50, "rgba(248,250,252,0.45)");
      addRsiRef(70, "rgba(248,250,252,0.45)");

      rsiChart.timeScale().fitContent();

      // 上下 timeScale 連動
      const ts = priceChart.timeScale();
      const rsiTs = rsiChart.timeScale();
      ts.subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return;
        rsiTs.setVisibleLogicalRange(range);
      });
    }

    // リサイズ対応
    resizeHandler = function () {
      const baseW =
        chartWrapper.clientWidth ||
        chartWrapper.getBoundingClientRect().width ||
        priceContainer.clientWidth ||
        320;

      if (priceChart) {
        priceChart.applyOptions({
          width: baseW,
          height: PRICE_HEIGHT,
        });
      }
      if (rsiChart) {
        rsiChart.applyOptions({
          width: baseW,
          height: RSI_HEIGHT,
        });
      }
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
    setText("detailLast", ds.last, "priceAuto");
    setText("detailAtr", ds.atr, "priceAuto");

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

    // 合計行は現状「診断用」なので非表示のままにしておく
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

    // ------------- チャート用データ -------------
    const openStr  = ds.chartOpen || "";
    const highStr  = ds.chartHigh || "";
    const lowStr   = ds.chartLow || "";
    const closeStr = ds.chartClose || "";
    const datesStr = ds.chartDates || "";

    const ma5Str     = ds.chartMa5 || "";
    const ma25Str    = ds.chartMa25 || "";
    const ma75Str    = ds.chartMa75 || "";
    const ma100Str   = ds.chartMa100 || "";
    const ma200Str   = ds.chartMa200 || "";
    const vwapStr    = ds.chartVwap || "";
    const rsiStr     = ds.chartRsi || "";

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

    function parseFloatArray(str) {
      return str
        ? str.split(",").map((s) => {
            const n = Number(s.trim());
            return isNaN(n) ? null : n;
          })
        : [];
    }

    const ma5     = parseFloatArray(ma5Str);
    const ma25    = parseFloatArray(ma25Str);
    const ma75    = parseFloatArray(ma75Str);
    const ma100   = parseFloatArray(ma100Str);
    const ma200   = parseFloatArray(ma200Str);
    const vwap    = parseFloatArray(vwapStr);
    const rsiList = parseFloatArray(rsiStr);

    const hi52w  = toNumberOrNull(ds.hi52w);
    const lo52w  = toNumberOrNull(ds.lo52w);
    const hiAll  = toNumberOrNull(ds.hiAll);
    const loAll  = toNumberOrNull(ds.loAll);

    // 凡例の数値更新（終値 / MA / VWAP / 高安値）
    const latestClose  = getLatestNumber(closes);
    const latestMa5    = getLatestNumber(ma5);
    const latestMa25   = getLatestNumber(ma25);
    const latestMa75   = getLatestNumber(ma75);
    const latestMa100  = getLatestNumber(ma100);
    const latestMa200  = getLatestNumber(ma200);
    const latestVwap   = getLatestNumber(vwap);

    if (legendCloseVal)  legendCloseVal.textContent  = formatPriceForLegend(latestClose);
    if (legendMa5Val)    legendMa5Val.textContent    = formatPriceForLegend(latestMa5);
    if (legendMa25Val)   legendMa25Val.textContent   = formatPriceForLegend(latestMa25);
    if (legendMa75Val)   legendMa75Val.textContent   = formatPriceForLegend(latestMa75);
    if (legendMa100Val)  legendMa100Val.textContent  = formatPriceForLegend(latestMa100);
    if (legendMa200Val)  legendMa200Val.textContent  = formatPriceForLegend(latestMa200);
    if (legendVwapVal)   legendVwapVal.textContent   = formatPriceForLegend(latestVwap);
    if (legendHi52wVal)  legendHi52wVal.textContent  = formatPriceForLegend(hi52w);
    if (legendLo52wVal)  legendLo52wVal.textContent  = formatPriceForLegend(lo52w);
    if (legendHiAllVal)  legendHiAllVal.textContent  = formatPriceForLegend(hiAll);
    if (legendLoAllVal)  legendLoAllVal.textContent  = formatPriceForLegend(loAll);

    // ローソク足データ生成
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
    const tp    = toNumberOrNull(ds.tp);
    const sl    = toNumberOrNull(ds.sl);

    updateChart(
      candles,
      closes,
      entry,
      tp,
      sl,
      ma5,
      ma25,
      ma75,
      ma100,
      ma200,
      vwap,
      rsiList,
      hi52w,
      lo52w,
      hiAll,
      loAll
    );

    modal.classList.add("show");
    body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.remove("show");
    body.classList.remove("modal-open");

    if (priceChart) {
      priceChart.remove();
      priceChart = null;
    }
    if (rsiChart) {
      rsiChart.remove();
      rsiChart = null;
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