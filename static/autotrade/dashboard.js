//
// [FILE] static/autotrade/dashboard.js
// [PATH] <project_root>/static/autotrade/dashboard.js
//
// このファイルは何？
// - 新ダッシュボードのフロント操作を担当します。
// - 横スワイプのタブ連動
// - DEMO / LIVE モード切替
// - 非常停止ボタン
//

(() => {
  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  const postJson = async (url, payload) => {
    const csrftoken = getCookie("csrftoken");
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken || "",
      },
      body: JSON.stringify(payload || {}),
      credentials: "same-origin",
    });

    const data = await res.json().catch(() => ({}));
    return { res, data };
  };

  // =====================================
  // 横スワイプ + タブ連動
  // =====================================
  const swiper = document.querySelector(".js-dash-swiper");
  const navButtons = Array.from(document.querySelectorAll(".js-dash-nav-btn"));

  const setActiveTab = (index) => {
    navButtons.forEach((btn, i) => {
      btn.classList.toggle("is-active", i === index);
    });
  };

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!swiper) return;
      const page = parseInt(btn.getAttribute("data-page") || "0", 10);
      const target = swiper.querySelector(`.autodash-page[data-page="${page}"]`);
      if (!target) return;

      target.scrollIntoView({
        behavior: "smooth",
        inline: "start",
        block: "nearest",
      });
      setActiveTab(page);
    });
  });

  if (swiper) {
    let ticking = false;

    const updateFromScroll = () => {
      const pages = Array.from(swiper.querySelectorAll(".autodash-page"));
      if (!pages.length) return;

      const left = swiper.scrollLeft;
      const width = swiper.clientWidth || 1;
      const index = Math.round(left / width);
      setActiveTab(Math.max(0, Math.min(index, pages.length - 1)));
    };

    swiper.addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(() => {
        updateFromScroll();
        ticking = false;
      });
    }, { passive: true });

    updateFromScroll();
  }

  // =====================================
  // Execution Mode Button
  // =====================================
  const modeButtons = Array.from(document.querySelectorAll(".js-mode-btn"));
  modeButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const url = btn.getAttribute("data-url");
      const mode = (btn.getAttribute("data-mode") || "").trim().toUpperCase();
      if (!url || !mode) return;

      let ok = false;
      if (mode === "LIVE") {
        ok = confirm(
          "LIVEに切り替えます。\n\n" +
          "ただし現段階では安全装置により、実発注は行いません。\n" +
          "状態確認用の準備モードとして切り替えます。よろしいですか？"
        );
      } else {
        ok = confirm("DEMOに切り替えます。よろしいですか？");
      }
      if (!ok) return;

      modeButtons.forEach((x) => { x.disabled = true; });

      try {
        const { res, data } = await postJson(url, { mode });

        if (!res.ok || !data.ok) {
          alert(data.detail || "モード切替に失敗しました。");
          modeButtons.forEach((x) => { x.disabled = false; });
          return;
        }

        location.reload();
      } catch (e) {
        alert("モード切替に失敗しました（通信）。");
        modeButtons.forEach((x) => { x.disabled = false; });
      }
    });
  });

  // =====================================
  // Emergency Stop Button
  // =====================================
  const btnStop = document.getElementById("btnStop");
  if (btnStop) {
    const url = btnStop.getAttribute("data-url");
    if (url) {
      btnStop.addEventListener("click", async () => {
        const ok = confirm("非常停止します。以後の自動売買を止めます。よろしいですか？");
        if (!ok) return;

        btnStop.disabled = true;
        btnStop.textContent = "停止中…";

        try {
          const { res, data } = await postJson(url, { reason: "manual" });

          if (!res.ok || !data.ok) {
            alert("非常停止に失敗しました。");
            btnStop.disabled = false;
            btnStop.textContent = "非常停止（ワンタップ）";
            return;
          }

          location.reload();
        } catch (e) {
          alert("非常停止に失敗しました（通信）。");
          btnStop.disabled = false;
          btnStop.textContent = "非常停止（ワンタップ）";
        }
      });
    }
  }
})();