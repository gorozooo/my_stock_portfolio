// aiapp/static/aiapp/picks_debug.js
// AI Picks 診断（picks_debug.html）用 JS
// - フィルタ
// - モーダル開閉
// - Chart.js で簡易ローソクっぽいライン＋価格目盛り

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
  // Chart.js 用：チャート更新
  // --------------------------------------
  function updateChart(closes, entry, tp, sl) {
    if (!chartCanvas) return;
    const ctx = chartCanvas.getContext("2d");
    if (!ctx) return;

    // 既存チャート破棄
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }

    // 空データの場合
    if (!closes || !closes.length) {
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "flex";
      }
      return;
    } else {
      if (chartEmptyLabel) {
        chartEmptyLabel.style.display = "none";
      }
    }

    const labels = closes.map((_, i) => i + 1); // インデックスを簡易X軸に

    // Y軸の min / max を決める
    let ymin = Math.min.apply(null, closes);
    let ymax = Math.max.apply(null, closes);

    const extraLines = [];

    function addLine(name, value, color, dash) {
      if (value === null || value === undefined) return;
      const n = Number(value);
      if (!isNaN(n)) {
        extraLines.push({ name, value: n, color, dash });
        ymin = Math.min(ymin, n);
        ymax = Math.max(ymax, n);
      }
    }

    addLine("Entry", entry, "#22c55e", [4, 4]);
    addLine("TP", tp, "#4ade80", [4, 4]);
    addLine("SL", sl, "#ef4444", [4, 4]);

    // 余白
    const pad = (ymax - ymin) * 0.1 || 10;
    ymin -= pad;
    ymax += pad;

    const datasets = [
      {
        label: "Close",
        data: closes,
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.15)",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
      },
    ];

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

    // チャート用データ
    let closes = [];
    const closesStr = ds.chartCloses || "";
    if (closesStr) {
      closes = closesStr
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((v) => !isNaN(v));
    }

    const entry = toNumberOrNull(ds.entry);
    const tp = toNumberOrNull(ds.tp);
    const sl = toNumberOrNull(ds.sl);

    updateChart(closes, entry, tp, sl);

    modal.classList.add("show");
    body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.remove("show");
    body.classList.remove("modal-open");

    // モーダルを閉じるときにチャートも破棄しておく（次回のちらつき防止）
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