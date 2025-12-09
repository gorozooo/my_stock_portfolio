// aiapp/static/aiapp/js/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - Chart.js でローソク足＋Entry/TP/SL

(function () {
  const table = document.getElementById("picksTable");
  const filterInput = document.getElementById("filterInput");

  const body = document.body;
  const modal = document.getElementById("pickModal");
  const closeBtn = document.getElementById("modalCloseBtn");

  const chartCanvas = document.getElementById("detailChart");
  const chartEmptyLabel = document.getElementById("chartEmptyLabel");

  let chartInstance = null;

  if (!table || !modal) {
    return;
  }

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
    } else if (fmt === "yen") {
      const n = Number(value);
      if (isNaN(n)) {
        txt = "–";
      } else {
        txt = n.toLocaleString();
        if (n > 0) txt = "+" + txt;
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
  // Chart.js 用：チャート更新（ローソク足＋Entry/TP/SL）
  // --------------------------------------
  // closes: 終値配列（フォールバック用）
  // ohlc: [{open, high, low, close}, ...] があればローソク足で描画
  // labels: X軸用ラベル（日付文字列など）
  function updateChart(closes, ohlc, entry, tp, sl, labels) {
    if (!chartCanvas) return;
    const ctx = chartCanvas.getContext("2d");
    if (!ctx) return;

    // 既存チャート破棄
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }

    const hasOhlc = Array.isArray(ohlc) && ohlc.length > 0;
    const hasCloses = Array.isArray(closes) && closes.length > 0;

    if (!hasOhlc && !hasCloses) {
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "flex";
      }
      return;
    } else {
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "none";
      }
    }

    // ラベル数（X軸）
    let xLabels = [];
    if (Array.isArray(labels) && labels.length > 0) {
      xLabels = labels.slice(-Math.max(ohlc ? ohlc.length : closes.length, 0));
    } else if (hasOhlc) {
      xLabels = ohlc.map((_, i) => i + 1);
    } else if (hasCloses) {
      xLabels = closes.map((_, i) => i + 1);
    }

    // Y軸の min / max を決める
    let ymin = Number.POSITIVE_INFINITY;
    let ymax = Number.NEGATIVE_INFINITY;

    if (hasOhlc) {
      ohlc.forEach((b) => {
        if (b.low < ymin) ymin = b.low;
        if (b.high > ymax) ymax = b.high;
      });
    }
    if (hasCloses) {
      closes.forEach((v) => {
        if (v < ymin) ymin = v;
        if (v > ymax) ymax = v;
      });
    }

    const extraLines = [];

    function addLine(name, value, color, dash) {
      if (value === null || value === undefined) return;
      const n = Number(value);
      if (!isNaN(n)) {
        extraLines.push({ name, value: n, color, dash });
        if (n < ymin) ymin = n;
        if (n > ymax) ymax = n;
      }
    }

    addLine("Entry", entry, "#22c55e", [4, 4]);
    addLine("TP", tp, "#4ade80", [4, 4]);
    addLine("SL", sl, "#ef4444", [4, 4]);

    // 余白
    const pad = (ymax - ymin) * 0.1 || 10;
    ymin -= pad;
    ymax += pad;

    const datasets = [];

    // ---- ローソク足 or 終値ライン ----
    if (hasOhlc) {
      // ヒゲ（高値〜安値）のバー
      datasets.push({
        type: "bar",
        label: "Wick",
        data: ohlc.map((b, idx) => ({
          x: xLabels[idx],
          y: [b.low, b.high],
        })),
        backgroundColor: "rgba(148, 163, 184, 0.35)",
        borderColor: "rgba(148, 163, 184, 0.9)",
        borderWidth: 1,
        borderSkipped: false,
        barPercentage: 0.6,
        categoryPercentage: 0.9,
      });

      // 実体（始値〜終値）
      datasets.push({
        type: "bar",
        label: "Candle",
        data: ohlc.map((b, idx) => ({
          x: xLabels[idx],
          y: [b.open, b.close],
        })),
        backgroundColor: function (context) {
          const raw = context.raw;
          let open = null;
          let close = null;
          if (raw && Array.isArray(raw.y)) {
            open = raw.y[0];
            close = raw.y[1];
          }
          if (close !== null && open !== null && close >= open) {
            // 上昇（陽線）
            return "rgba(34, 197, 94, 0.9)";
          }
          // 下落（陰線）
          return "rgba(239, 68, 68, 0.9)";
        },
        borderColor: function (context) {
          const raw = context.raw;
          let open = null;
          let close = null;
          if (raw && Array.isArray(raw.y)) {
            open = raw.y[0];
            close = raw.y[1];
          }
          if (close !== null && open !== null && close >= open) {
            return "rgba(34, 197, 94, 1)";
          }
          return "rgba(239, 68, 68, 1)";
        },
        borderWidth: 1,
        borderSkipped: false,
        barPercentage: 0.4,
        categoryPercentage: 0.9,
      });

      // 終値ライン
      if (hasCloses) {
        datasets.push({
          type: "line",
          label: "Close",
          data: closes.map((v, idx) => ({ x: xLabels[idx], y: v })),
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.15)",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.25,
        });
      }
    } else if (hasCloses) {
      // OHLC が無い場合は従来どおり折れ線
      datasets.push({
        type: "line",
        label: "Close",
        data: closes.map((v, idx) => ({ x: xLabels[idx], y: v })),
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.15)",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
      });
    }

    // ---- Entry / TP / SL の水平ライン ----
    extraLines.forEach((ln) => {
      datasets.push({
        type: "line",
        label: ln.name,
        data: xLabels.map((x) => ({ x: x, y: ln.value })),
        borderColor: ln.color,
        borderWidth: 1,
        pointRadius: 0,
        borderDash: ln.dash || [],
        fill: false,
      });
    });

    chartInstance = new Chart(ctx, {
      type: "bar", // ベースは bar（中で line と混在）
      data: {
        labels: xLabels,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            // ローソクのバーはツールチップ無し、線だけ表示
            filter: function (context) {
              return context.dataset.type === "line";
            },
            callbacks: {
              label: function (context) {
                const v = context.parsed && context.parsed.y;
                if (v == null || isNaN(v)) return "";
                const name = context.dataset.label || "";
                return `${name}: ${v.toLocaleString()} 円`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "category",
            display: true,
            offset: false, // ここで端のローソクが枠内に収まるようにする
            ticks: {
              color: "#9ca3af",
              maxTicksLimit: 6,
              autoSkip: true,
              maxRotation: 0,
              minRotation: 0,
            },
            grid: {
              color: "rgba(148, 163, 184, 0.25)",
            },
          },
          y: {
            min: ymin,
            max: ymax,
            ticks: {
              color: "#9ca3af",
              callback: function (value) {
                const n = Number(value);
                if (isNaN(n)) return value;
                return n.toLocaleString();
              },
            },
            grid: {
              color: "rgba(148, 163, 184, 0.25)",
            },
          },
        },
      },
    });
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
    let closes = [];
    const closesStr = ds.chartCloses || "";
    if (closesStr) {
      closes = closesStr
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((v) => !isNaN(v));
    }

    let ohlcBars = [];
    const openStr = ds.chartOpen || "";
    const highStr = ds.chartHigh || "";
    const lowStr = ds.chartLow || "";
    const datesStr = ds.chartDates || "";

    if (openStr && highStr && lowStr && closesStr) {
      const os = openStr.split(",");
      const hs = highStr.split(",");
      const ls = lowStr.split(",");
      const cs = closesStr.split(",");
      const len = Math.min(os.length, hs.length, ls.length, cs.length);
      for (let i = 0; i < len; i++) {
        const o = Number(os[i].trim());
        const h = Number(hs[i].trim());
        const l = Number(ls[i].trim());
        const c = Number(cs[i].trim());
        if (!isNaN(o) && !isNaN(h) && !isNaN(l) && !isNaN(c)) {
          ohlcBars.push({
            open: o,
            high: h,
            low: l,
            close: c,
          });
        }
      }
    }

    let labels = [];
    if (datesStr) {
      labels = datesStr.split("||").map((s) => s.trim()).filter((s) => s);
    }

    const entry = toNumberOrNull(ds.entry);
    const tp = toNumberOrNull(ds.tp);
    const sl = toNumberOrNull(ds.sl);

    updateChart(closes, ohlcBars, entry, tp, sl, labels);

    modal.classList.add("show");
    body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.remove("show");
    body.classList.remove("modal-open");

    // モーダルを閉じるときにチャートも破棄
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
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