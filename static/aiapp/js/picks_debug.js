// aiapp/static/aiapp/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - Chart.js でローソク足（OHLC）＋価格目盛り
//
// 前提：
//  - 行 <tr class="pick-row"> に data-chart-ohlc / data-chart-closes などが埋まっている
//    * data-chart-ohlc: "o,h,l,c|o,h,l,c|..." 形式
//    * data-chart-closes: "1000,1010,..."（フォールバック用）

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
  // OHLC 文字列 → 配列 {o,h,l,c}
  // --------------------------------------
  // 例: "1000,1010,995,1005|1005,1020,1000,1018|..."
  function parseOhlc(raw) {
    if (!raw) return [];
    const out = [];
    raw.split("|").forEach((seg) => {
      const p = seg.split(",");
      if (p.length < 4) return;
      const o = Number(p[0]);
      const h = Number(p[1]);
      const l = Number(p[2]);
      const c = Number(p[3]);
      if ([o, h, l, c].some((v) => isNaN(v))) return;
      out.push({ o, h, l, c });
    });
    return out;
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
  // Chart.js 用：ローソク描画プラグイン
  // --------------------------------------
  const ohlcDrawerPlugin = {
    id: "ohlcDrawerPlugin",
    afterDatasetsDraw(chart, args, pluginOptions) {
      const ohlc = chart.$ohlc;
      if (!ohlc || !ohlc.length) return;

      const { ctx, scales } = chart;
      const y = scales.y;
      if (!y) return;

      const meta = chart.getDatasetMeta(0); // 最初のデータセット（closeライン）の座標を利用
      if (!meta || !meta.data || !meta.data.length) return;

      ctx.save();
      ctx.lineWidth = 1;

      ohlc.forEach((bar, idx) => {
        const elem = meta.data[idx];
        if (!elem) return;

        const xPos = elem.x;
        const yHigh = y.getPixelForValue(bar.h);
        const yLow = y.getPixelForValue(bar.l);
        const yOpen = y.getPixelForValue(bar.o);
        const yClose = y.getPixelForValue(bar.c);

        const isUp = bar.c >= bar.o;
        const stroke = isUp ? "#22c55e" : "#ef4444";
        const fill = isUp ? "rgba(34,197,94,0.7)" : "rgba(239,68,68,0.7)";

        // 1本あたりの幅（隣り合う点の距離から推定）
        let candleWidth = 6;
        if (meta.data[idx + 1]) {
          candleWidth = Math.max(
            3,
            Math.min(18, (meta.data[idx + 1].x - elem.x) * 0.7)
          );
        }

        ctx.strokeStyle = stroke;
        ctx.fillStyle = fill;

        // ヒゲ
        ctx.beginPath();
        ctx.moveTo(xPos, yHigh);
        ctx.lineTo(xPos, yLow);
        ctx.stroke();

        // 実体
        const bodyTop = isUp ? yClose : yOpen;
        const bodyBottom = isUp ? yOpen : yClose;
        const bodyHeight = Math.max(1, bodyBottom - bodyTop);

        const xLeft = xPos - candleWidth / 2;
        ctx.fillRect(xLeft, bodyTop, candleWidth, bodyHeight);
        ctx.strokeRect(xLeft, bodyTop, candleWidth, bodyHeight);
      });

      ctx.restore();
    },
  };

  // --------------------------------------
  // Chart.js 用：チャート更新
  // --------------------------------------
  function updateChart(ohlc, closes, entry, tp, sl) {
    if (!chartCanvas) return;
    const ctx = chartCanvas.getContext("2d");
    if (!ctx) return;

    // 既存チャート破棄
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }

    // データ正規化：
    //  - OHLC があればローソク足メイン
    //  - 無ければ closes だけで折れ線
    const hasOhlc = ohlc && ohlc.length;
    let closesArr = Array.isArray(closes) ? closes.slice() : [];

    if (!hasOhlc && !closesArr.length) {
      // データ無し
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "flex";
      }
      return;
    } else {
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "none";
      }
    }

    if (hasOhlc && !closesArr.length) {
      closesArr = ohlc.map((b) => b.c);
    }

    const labels = closesArr.map((_, i) => i + 1);

    // Y軸の min / max を決める
    let ymin = Number.POSITIVE_INFINITY;
    let ymax = Number.NEGATIVE_INFINITY;

    function expand(v) {
      if (v === null || v === undefined) return;
      const n = Number(v);
      if (isNaN(n)) return;
      if (n < ymin) ymin = n;
      if (n > ymax) ymax = n;
    }

    if (hasOhlc) {
      ohlc.forEach((b) => {
        expand(b.h);
        expand(b.l);
      });
    } else {
      closesArr.forEach(expand);
    }

    expand(entry);
    expand(tp);
    expand(sl);

    if (!isFinite(ymin) || !isFinite(ymax)) {
      ymin = 0;
      ymax = 1;
    }
    const pad = (ymax - ymin) * 0.1 || 10;
    ymin -= pad;
    ymax += pad;

    // 追加ライン（Entry/TP/SL）
    const extraLines = [];
    function addLine(name, value, color, dash) {
      if (value === null || value === undefined) return;
      const n = Number(value);
      if (!isNaN(n)) {
        extraLines.push({ name, value: n, color, dash });
        expand(n);
      }
    }

    addLine("Entry", entry, "#22c55e", [4, 4]);
    addLine("TP", tp, "#4ade80", [4, 4]);
    addLine("SL", sl, "#ef4444", [4, 4]);

    // データセット：
    //  - 1本目: Close ライン（ツールチップ用、線は細め）
    const datasets = [
      {
        label: "Close",
        data: closesArr,
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.15)",
        borderWidth: 1,
        pointRadius: 0,
        tension: 0.15,
        fill: false,
      },
    ];

    // Entry / TP / SL の水平線
    extraLines.forEach((ln) => {
      datasets.push({
        label: ln.name,
        data: labels.map(() => ln.value),
        borderColor: ln.color,
        borderWidth: 1,
        pointRadius: 0,
        borderDash: ln.dash || [],
        fill: false,
      });
    });

    chartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
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
            callbacks: {
              label: function (context) {
                const v = context.parsed.y;
                if (v == null || isNaN(v)) return "";
                const name = context.dataset.label || "";
                return `${name}: ${v.toLocaleString()} 円`;
              },
            },
          },
        },
        scales: {
          x: {
            display: false,
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
      plugins: [ohlcDrawerPlugin],
    });

    // プラグインが参照する OHLC データを chart に紐付け
    chartInstance.$ohlc = hasOhlc ? ohlc : null;
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

    // チャート用データ
    let closes = [];
    const closesStr = ds.chartCloses || "";
    if (closesStr) {
      closes = closesStr
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((v) => !isNaN(v));
    }

    let ohlc = [];
    const ohlcStr = ds.chartOhlc || ds.chartOhlcRaw || "";
    if (ohlcStr) {
      ohlc = parseOhlc(ohlcStr);
    }

    const entry = toNumberOrNull(ds.entry);
    const tp = toNumberOrNull(ds.tp);
    const sl = toNumberOrNull(ds.sl);

    updateChart(ohlc, closes, entry, tp, sl);

    modal.classList.add("show");
    body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.remove("show");
    body.classList.remove("modal-open");

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