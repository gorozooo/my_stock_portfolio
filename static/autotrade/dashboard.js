// 
// [FILE] static/autotrade/dashboard.js
// [PATH] <project_root>/static/autotrade/dashboard.js
//
// このファイルは何？
// - ダッシュボードのフロント操作を担当します。
// - 非常停止ボタン（API呼び出し）と、VWAP/BREAKOUT タブ切り替えを行います。

(() => {
  // =========================
  // Tabs (VWAP / BREAKOUT)
  // =========================
  const tabsRoot = document.querySelector(".autotrade-tabs");
  const panesRoot = document.querySelector(".autotrade-tabpanes");
  if (tabsRoot && panesRoot) {
    const tabs = Array.from(tabsRoot.querySelectorAll(".autotrade-tab"));
    const panes = Array.from(panesRoot.querySelectorAll(".autotrade-pane"));
    const initial = (tabsRoot.getAttribute("data-initial") || "VWAP").trim();

    const setActive = (name) => {
      tabs.forEach((t) => {
        const isOn = (t.getAttribute("data-tab") === name);
        t.classList.toggle("is-active", isOn);
      });
      panes.forEach((p) => {
        const isOn = (p.getAttribute("data-pane") === name);
        p.classList.toggle("is-active", isOn);
      });
      try {
        localStorage.setItem("autotrade_dashboard_tab", name);
      } catch (e) {}
    };

    let start = initial;
    try {
      const saved = localStorage.getItem("autotrade_dashboard_tab");
      if (saved) start = saved;
    } catch (e) {}

    // 存在しない値だったらVWAPへ
    if (!["VWAP", "BREAKOUT"].includes(start)) start = "VWAP";
    setActive(start);

    tabs.forEach((t) => {
      t.addEventListener("click", () => {
        const name = t.getAttribute("data-tab");
        if (!name) return;
        setActive(name);
      });
    });
  }

  // =========================
  // Emergency Stop Button
  // =========================
  const btn = document.getElementById("btnStop");
  if (!btn) return;

  const url = btn.getAttribute("data-url");
  if (!url) return; // 停止中などで data-url が無い時

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  btn.addEventListener("click", async () => {
    const ok = confirm("非常停止します。以後の自動売買を止めます。よろしいですか？");
    if (!ok) return;

    btn.disabled = true;
    btn.textContent = "停止中…";

    try {
      const csrftoken = getCookie("csrftoken");

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
        },
        body: JSON.stringify({ reason: "manual" }),
        credentials: "same-origin",
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        alert("非常停止に失敗しました（API）");
        btn.disabled = false;
        btn.textContent = "非常停止（ワンタップ）";
        return;
      }

      // 成功 → 画面更新（表示が「非常停止中」に切り替わる）
      location.reload();
    } catch (e) {
      alert("非常停止に失敗しました（通信）");
      btn.disabled = false;
      btn.textContent = "非常停止（ワンタップ）";
    }
  });
})();