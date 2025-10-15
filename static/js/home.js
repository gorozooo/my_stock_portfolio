document.addEventListener("DOMContentLoaded", () => {
  // ===== ストレステスト =====
  const dataEl = document.getElementById("home-data");
  let data = {};
  try { data = JSON.parse(dataEl?.textContent || "{}"); } catch (e) {}

  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  const totalMV = Number(data.total_mv || 0);
  const beta = 0.9;

  const updateStress = () => {
    const pct = Number(slider.value);
    const mv = Math.round(totalMV * (1 + beta * pct / 100));
    pctEl.textContent = String(pct);
    mvEl.textContent  = "¥" + mv.toLocaleString();
  };
  slider?.addEventListener("input", updateStress);
  updateStress();

  // ===== キャッシュフロー =====
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

  // ===== AIアドバイザー: チェック切替 =====
  const list = document.getElementById("aiAdviceList");
  if (list) {
    list.addEventListener("click", async (ev) => {
      const li = ev.target.closest(".ai-item");
      if (!li) return;

      const btn = li.querySelector(".ai-check");
      const id = Number(li.dataset.id || 0);
      const variant = list.dataset.abVariant || "A";

      const currentlyTaken = li.dataset.taken === "1";
      const nextTaken = !currentlyTaken;
      li.dataset.taken = nextTaken ? "1" : "0";
      btn.textContent = nextTaken ? "✅" : "☑️";

      if (id > 0) {
        try {
          const res = await fetch(`/api/advisor/toggle/${id}/`, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || ""
            },
            body: `ab_variant=${encodeURIComponent(variant)}`
          });
          const json = await res.json();
          if (!json.ok) console.warn("toggle failed");
        } catch (e) {
          console.warn("toggle error", e);
        }
      }
    });
  }

  // ===== ドラフト閲覧（週次・次の一手） =====
  const showDraft = (title, body) => {
    alert(`${title}\n\n${body || "（本文なし）"}`);
  };
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    showDraft("週次レポート（ドラフト）", window.weekly_draft || "");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    showDraft("次の一手（ドラフト）", window.nextmove_draft || "");
  });
});