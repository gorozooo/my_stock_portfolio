// CSRF 取得（meta > cookie の順）
function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
  const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

document.addEventListener("DOMContentLoaded", () => {
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, sectors: [], cash_bars: [] };
  try { data = JSON.parse(dataEl.textContent); } catch (e) {}

  // ストレステスト
  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  const totalMV = Number(data.total_mv || 0);
  const beta = 0.9;
  const updateStress = () => {
    if (!slider) return;
    const pct = Number(slider.value);
    const mv = Math.round(totalMV * (1 + beta * pct / 100));
    if (pctEl) pctEl.textContent = String(pct);
    if (mvEl) mvEl.textContent = "¥" + mv.toLocaleString();
  };
  if (slider) slider.addEventListener("input", updateStress);
  updateStress();

  // キャッシュフロー棒グラフ
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = data.cash_bars.map(x => x.label);
    const values = data.cash_bars.map(x => x.value);
    new Chart(cashCanvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label: "今月", data: values, borderWidth: 1 }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  // AIボタン（ダミー）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）。直近の推移・勝率・強弱セクターを要約します。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手レコメンド（AI接続予定）。現金比率・含み益・セクター偏りから提案します。");
  });
});

// === AIアドバイザー: 採用トグル ===
(function () {
  const list = document.getElementById("aiAdviceList");
  if (!list) return;

  list.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".ai-check");
    if (!btn) return;
    const li = btn.closest("li");
    const id = li?.dataset?.id;
    if (!id || id === "0") return; // 0は仮メッセージ

    btn.disabled = true;
    try {
      const res = await fetch(`/api/advisor/toggle/${id}/`, {
        method: "POST",
        headers: {
          "X-Requested-With": "fetch",
          "X-CSRFToken": getCSRFToken()
        }
      });
      if (!res.ok) throw new Error(String(res.status));
      const json = await res.json();
      if (json.ok) {
        // アイコン更新
        btn.textContent = json.taken ? "✅" : "☑️";
        btn.dataset.state = json.taken ? "1" : "0";
      }
    } catch (e) {
      console.warn("toggle failed", e);
      alert("更新に失敗しました。（通信/CSRF）");
    } finally {
      btn.disabled = false;
    }
  });
})();