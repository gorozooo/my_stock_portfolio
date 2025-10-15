document.addEventListener("DOMContentLoaded", () => {
  // -------- CSRF ----------
  const getCookie = (name) => {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  };
  const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || getCookie('csrftoken') || '';

  // -------- ストレステスト ----------
  (function initStress() {
    const root = document.getElementById("stressRoot");
    if (!root) return;
    const totalMV = Number(root.dataset.totalMv || 0);
    const slider  = document.getElementById("stressSlider");
    const pctEl   = document.getElementById("stressPct");
    const mvEl    = document.getElementById("stressMV");
    const beta    = 0.9;

    const update = () => {
      const pct = Number(slider.value || 0);
      const mv  = Math.round(totalMV * (1 + beta * pct / 100));
      pctEl.textContent = String(pct);
      mvEl.textContent  = "¥" + mv.toLocaleString();
    };
    slider?.addEventListener("input", update);
    update();
  })();

  // -------- キャッシュフロー ----------
  (function initCashflow() {
    const root = document.getElementById("cashRoot");
    const cvs  = document.getElementById("cashflowChart");
    if (!root || !cvs) return;

    const div  = Number(root.dataset.dividend || 0);
    const real = Number(root.dataset.realized || 0);

    if (!window.Chart || (div === 0 && real === 0)) {
      cvs.style.display = "none";
      root.querySelector(".empty-msg").style.display = "block";
      return;
    }
    const ctx = cvs.getContext("2d");
    new Chart(ctx, {
      type: "bar",
      data: { labels: ["配当", "実現益"], datasets: [{ label: "今月", data: [div, real], borderWidth: 1 }] },
      options: { responsive: true, plugins: { legend: { display:false } }, scales: { y: { beginAtZero: true } } }
    });
  })();

  // -------- AIアドバイザー: チェック切替 ----------
  (function initAdvisorToggle() {
    const list = document.getElementById("aiAdviceList");
    if (!list) return;

    list.addEventListener("click", async (ev) => {
      const li  = ev.target.closest(".ai-item");
      if (!li) return;
      const btn = li.querySelector(".ai-check");
      if (!btn) return;

      // UI先行切替
      const wasTaken = li.dataset.taken === "1";
      const willTake = !wasTaken;
      li.dataset.taken = willTake ? "1" : "0";
      btn.setAttribute("aria-pressed", willTake ? "true" : "false");
      btn.textContent = willTake ? "✅" : "☑️";

      // サーバ反映（id==0はフロント専用）
      const id = Number(li.dataset.id || 0);
      if (id > 0) {
        try {
          const res = await fetch(`/api/advisor/toggle/${id}/`, {
            method: "POST",
            headers: { "X-CSRFToken": CSRF, "X-Requested-With": "fetch" },
            credentials: "same-origin"
          });
          const json = await res.json();
          if (!json.ok) throw new Error("toggle failed");
        } catch (e) {
          // 失敗したらロールバック
          li.dataset.taken = wasTaken ? "1" : "0";
          btn.setAttribute("aria-pressed", wasTaken ? "true" : "false");
          btn.textContent = wasTaken ? "✅" : "☑️";
          console.warn(e);
        }
      }
    });
  })();

  // -------- ドラフト（週次・次の一手） ----------
  const showDraft = (title, body) => alert(`${title}\n\n${body || "（本文なし）"}`);
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    showDraft("週次レポート（ドラフト）", window.weekly_draft || "");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    showDraft("次の一手（ドラフト）", window.nextmove_draft || "");
  });
});