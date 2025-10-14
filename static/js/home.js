document.addEventListener("DOMContentLoaded", () => {
  // ====== ストレステスト等（既存） ======
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, sectors: [], cash_bars: [] };
  try { data = JSON.parse(dataEl?.textContent || "{}"); } catch (e) {}

  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  const totalMV = Number(data.total_mv || 0);
  const beta = 0.9;
  const updateStress = () => {
    if (!slider) return;
    const pct = Number(slider.value);
    const mv = Math.round(totalMV * (1 + beta * pct / 100));
    pctEl && (pctEl.textContent = String(pct));
    mvEl  && (mvEl.textContent  = "¥" + mv.toLocaleString());
  };
  slider && slider.addEventListener("input", updateStress);
  updateStress();

  // キャッシュフロー
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = data.cash_bars.map(x => x.label);
    const values = data.cash_bars.map(x => x.value);
    new Chart(cashCanvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label: "今月", data: values, borderWidth: 1 }] },
      options: { responsive: true, plugins: { legend: { display:false } }, scales: { y: { beginAtZero:true } } }
    });
  }

  // ====== AIアドバイザー: 採用トグル（楽観的更新） ======
  const list = document.getElementById("aiAdviceList");
  if (list) {
    list.addEventListener("click", async (ev) => {
      // ボタン以外（テキストや行）を押しても反応
      const li = ev.target.closest(".ai-item");
      if (!li) return;

      const btn = li.querySelector(".ai-check");
      if (!btn) return;

      // 楽観的にUI先行切替
      const currentlyTaken = li.dataset.taken === "1";
      const nextTaken = !currentlyTaken;
      li.dataset.taken = nextTaken ? "1" : "0";
      btn.setAttribute("aria-pressed", nextTaken ? "true" : "false");
      btn.textContent = nextTaken ? "✅" : "☑️";

      // id>0 のときだけサーバに送る（id=0 はフロント専用でOK）
      const id = Number(li.dataset.id || 0);
      if (id > 0) {
        try {
          const res = await fetch(`/api/advisor/toggle/${id}/`, {
            method: "POST",
            headers: { "X-Requested-With": "fetch" }
          });
          const json = await res.json();
          // サーバが否定したらロールバック
          if (!json.ok || json.taken !== nextTaken) {
            li.dataset.taken = currentlyTaken ? "1" : "0";
            btn.setAttribute("aria-pressed", currentlyTaken ? "true" : "false");
            btn.textContent = currentlyTaken ? "✅" : "☑️";
          }
        } catch (e) {
          // 通信失敗でもUIはそのまま（次回再同期で吸収）
          console.warn("toggle failed", e);
        }
      }
    });
  }

  // ====== ドラフト閲覧（モーダル代わりに簡易ダイアログ） ======
  const showDraft = (title, body) => {
    const txt = `${title}\n\n${body || "（本文なし）"}`;
    alert(txt);
  };
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    showDraft("週次レポート（ドラフト）", window.weekly_draft || "");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    showDraft("次の一手（ドラフト）", window.nextmove_draft || "");
  });
});