// aiapp/static/aiapp/js/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - lightweight-charts で本物ローソク足 + Entry/TP/SL + MA/VWAP + RSI

(function () {
  const table = document.getElementById("picksTable");
  const filterInput = document.getElementById("filterInput");

  const body = document.body;
  const modal = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");

  const chartContainer = document.getElementById("detailChartContainer");
  const rsiChartContainer = document.getElementById("detailRsiChartContainer");
  const chartEmptyLabel = document.getElementById("chartEmptyLabel");

  let lwChart = null;
  let lwChartRsi = null;
  let resizeHandler = null;

  // ★ 現在開いている銘柄の価格表示モード
  //   "int"       : 価格は整数（4768 など）
  //   "decimal1"  : 価格は小数1桁（9434 など）
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
      // 価格を小数第1位まで固定表示（使うならそのまま）
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
      // ★ 現在値のフォーマット（currentPriceMode）に合わせて
      //    Entry / TP / SL を「整数」または「小数1桁」で表示
      const raw = String(value).trim();
      const n0 = Number(raw);
      if (isNaN(n0)) {
        txt = "–";
      } else {
        if (currentPriceMode === "decimal1") {
          // 小数銘柄 → 小数1桁に丸めて表示
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
          // 整数銘柄 → 整数に丸めて表示
          const n = Math.round(n0);
          txt = n.toLocaleString();
        }
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

  // 文字列 "1,2,3,None,4" → [1,2,3,null,4]
  function parseSeriesWithNull(str) {
    if (!str) return [];
    return str.split(",").map(function (s) {
      const t = s.trim();
      if (!t || t.toLowerCase() === "none") return null;
      const n = Number(t);
      return isNaN(n) ? null : n;
    });
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
  // maShort / maMid / vwap: [number | null, ...]
  // rsiValues: [number | null, ...] 0〜100
  function updateChart(
    candles,
    closes,
    entry,
    tp,
    sl,
    maShort,
    maMid,
    vwap,
    rsiValues,
    datesForRsi
  ) {
    // 既存チャート破棄
    if (lwChart) {
      lwChart.remove();
      lwChart = null;
    }
    if (lwChartRsi) {
      lwChartRsi.remove();
      lwChartRsi = null;
    }
    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
    }

    const hasCandles = Array.isArray(candles) && candles.length > 0;
    const hasCloses = Array.isArray(closes) && closes.length > 0;

    if (!hasCandles && !hasCloses) {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "flex";
      if (rsiChartContainer) rsiChartContainer.style.display = "none";
      return;
    } else {
      if (chartEmptyLabel) chartEmptyLabel.style.display = "none";
      if (rsiChartContainer) rsiChartContainer.style.display = "";
    }

    // ▼ カードの内側に左右余白を強制的に確保 & はみ出し隠す
    const INNER_PAD = 16; // px（左右とも）
    chartContainer.style.paddingLeft = INNER_PAD + "px";
    chartContainer.style.paddingRight = INNER_PAD + "px";
    chartContainer.style.boxSizing = "border-box";
    chartContainer.style.overflow = "hidden";

    if (rsiChartContainer) {
      rsiChartContainer.style.paddingLeft = INNER_PAD + "px";
      rsiChartContainer.style.paddingRight = INNER_PAD + "px";
      rsiChartContainer.style.boxSizing = "border-box";
      rsiChartContainer.style.overflow = "hidden";
    }

    // 内側の幅を取得（スクロール幅じゃなく clientWidth を使う）
    const containerInnerWidth =
      chartContainer.clientWidth || chartContainer.getBoundingClientRect().width || 0;
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
        lastValueVisible: false, // 現在値ラベル非表示
        priceLineVisible: false, // 現在値の横破線も非表示
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

    function buildLineData(srcArr) {
      if (!Array.isArray(srcArr) || !srcArr.length || !baseTimeList.length) return null;
      const len = Math.min(srcArr.length, baseTimeList.length);
      const out = [];
      for (let i = 0; i < len; i++) {
        const v = srcArr[i];
        if (v === null || v === undefined || !isFinite(Number(v))) {
          out.push({ time: baseTimeList[i], value: null });
        } else {
          out.push({ time: baseTimeList[i], value: Number(v) });
        }
      }
      return out;
    }

    // --- MA・VWAP のライン ---
    const maShortData = buildLineData(maShort);
    const maMidData = buildLineData(maMid);
    const vwapData = buildLineData(vwap);

    if (maShortData) {
      const maShortSeries = lwChart.addLineSeries({
        color: "#f97316", // オレンジ
        lineWidth: 2,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      maShortSeries.setData(maShortData);
    }

    if (maMidData) {
      const maMidSeries = lwChart.addLineSeries({
        color: "#3b82f6", // ブルー
        lineWidth: 2,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      maMidSeries.setData(maMidData);
    }

    if (vwapData) {
      const vwapSeries = lwChart.addLineSeries({
        color: "#a855f7", // パープル
        lineWidth: 2,
        lineStyle: LW.LineStyle.Dotted,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      vwapSeries.setData(vwapData);
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
        lastValueVisible: false,
        priceLineVisible: false,
      });
      const data = baseTimeList.map((t) => ({
        time: t,
        value: num,
      }));
      series.setData(data);
      return series;
    }

    // Entry: 黄色, TP: 緑, SL: 赤
    addHLine(entry, "#eab308");
    addHLine(tp, "#22c55e");
    addHLine(sl, "#ef4444");

    // 全体がカード内に収まるように自動フィット
    lwChart.timeScale().fitContent();

    // --- RSI チャート（下段） ---
    if (rsiChartContainer) {
      const hasRsi = Array.isArray(rsiValues) && rsiValues.length > 0;
      if (!hasRsi) {
        rsiChartContainer.style.display = "none";
      } else {
        rsiChartContainer.style.display = "";

        const rsiRect = rsiChartContainer.getBoundingClientRect();
        const rsiWidth = chartWidth;
        const rsiHeight = rsiRect.height || 80;

        lwChartRsi = LW.createChart(rsiChartContainer, {
          width: rsiWidth,
          height: rsiHeight,
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
            scaleMargins: { top: 0.1, bottom: 0.1 },
          },
          timeScale: {
            borderVisible: false,
            rightOffset: 2,
            barSpacing: 7,
          },
          crosshair: {
            mode: LW.CrosshairMode.Normal,
          },
        });

        const rsiTimes = baseTimeList.length
          ? baseTimeList
          : Array.from({ length: rsiValues.length }, (_, i) => i + 1);

        const lenRsi = Math.min(rsiValues.length, rsiTimes.length);
        const rsiData = [];
        for (let i = 0; i < lenRsi; i++) {
          const v = rsiValues[i];
          const n = v === null || v === undefined ? NaN : Number(v);
          rsiData.push({
            time: rsiTimes[i],
            value: isNaN(n) ? null : n,
          });
        }

        const rsiSeries = lwChartRsi.addLineSeries({
          color: "#22c55e",
          lineWidth: 2,
          priceFormat: {
            type: "price",
            precision: 0,
            minMove: 1,
          },
          lastValueVisible: false,
          priceLineVisible: false,
        });
        rsiSeries.setData(rsiData);

        // 30/70 ライン
        function addRsiHLine(v, color) {
          const series = lwChartRsi.addLineSeries({
            color: color,
            lineWidth: 1,
            lineStyle: LW.LineStyle.Dotted,
            priceFormat: { type: "price", precision: 0, minMove: 1 },
            lastValueVisible: false,
            priceLineVisible: false,
          });
          const data = rsiTimes.map((t) => ({ time: t, value: v }));
          series.setData(data);
        }
        addRsiHLine(30, "rgba(239,68,68,0.5)");
        addRsiHLine(70, "rgba(34,197,94,0.5)");

        lwChartRsi.timeScale().fitContent();
      }
    }

    // リサイズ対応（余白維持）
    resizeHandler = function () {
      if (!lwChart) return;
      const innerWidth =
        chartContainer.clientWidth || chartContainer.getBoundingClientRect().width || 0;
      let w = innerWidth - INNER_PAD * 2;
      if (!isFinite(w) || w <= 0) w = 320;

      const r1 = chartContainer.getBoundingClientRect();
      const h1 = r1.height || 260;
      lwChart.applyOptions({
        width: w,
        height: h1,
      });

      if (lwChartRsi && rsiChartContainer) {
        const r2 = rsiChartContainer.getBoundingClientRect();
        const h2 = r2.height || 80;
        lwChartRsi.applyOptions({
          width: w,
          height: h2,
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

    // ★ まず現在値の文字列から「整数/小数1桁」を判定して currentPriceMode を更新
    (function decidePriceMode() {
      const raw = (ds.last || "").toString().trim();
      let mode = "int";
      if (raw) {
        const dot = raw.indexOf(".");
        if (dot >= 0) {
          // 小数点以下の「0を除いた桁数」を見る
          const decimals = raw
            .slice(dot + 1)
            .replace(/0+$/, "") // 末尾の 0 は無視
            .length;
          if (decimals >= 1) {
            mode = "decimal1";
          }
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

    // Entry / TP / SL → currentPriceMode に合わせて表示
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

    // 想定損失（文字色は赤）
    setText("detailLossRakuten", ds.lossRakuten, "yen");
    setText("detailLossMatsui", ds.lossMatsui, "yen");
    setText("detailLossSbi", ds.lossSbi, "yen");

    ["detailLossRakuten", "detailLossMatsui", "detailLossSbi"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) {
        el.classList.add("detail-red");
      }
    });

    // 数量・想定利益・想定損失の「合計」行は非表示
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

    // ------------- チャート用データ（OHLC + 日付 + MA/VWAP/RSI） -------------
    const openStr = ds.chartOpen || "";
    const highStr = ds.chartHigh || "";
    const lowStr = ds.chartLow || "";
    const closeStr = ds.chartClose || "";
    const datesStr = ds.chartDates || "";

    const maShortStr = ds.chartMaShort || "";
    const maMidStr = ds.chartMaMid || "";
    const vwapStr = ds.chartVwap || "";
    const rsiStr = ds.chartRsi || "";

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

    const maShortArr = parseSeriesWithNull(maShortStr);
    const maMidArr = parseSeriesWithNull(maMidStr);
    const vwapArr = parseSeriesWithNull(vwapStr);
    const rsiArr = parseSeriesWithNull(rsiStr);

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

    updateChart(
      candles,
      closes,
      entry,
      tp,
      sl,
      maShortArr,
      maMidArr,
      vwapArr,
      rsiArr,
      dates
    );

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
    if (lwChartRsi) {
      lwChartRsi.remove();
      lwChartRsi = null;
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