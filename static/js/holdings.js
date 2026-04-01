/* [FILE] holdings.js
   [PATH] static/js/holdings.js

   このファイルは何？
   - /holdings/ 専用JS
   - 証券会社タブ / 口座区分タブを再読み込みなしで切替
   - 詳細は details 開閉
   - アクションは左スワイプで表示

   今回の方針
   - 一度に1件だけ詳細を開ける
   - 一度に1件だけスワイプアクションを開ける
   - 左スワイプで開く / 右スワイプで閉じる
   - タブはHTML再取得せず、表示だけ切り替える
*/

(() => {
  const START_SLOP = 8;
  const THRESHOLD = 0.35;

  function closeAllSwipe(exceptShell = null) {
    document.querySelectorAll(".holding-row-shell.is-open").forEach((shell) => {
      if (shell !== exceptShell) {
        closeSwipe(shell);
      }
    });
  }

  function getParts(shell) {
    return {
      actions: shell.querySelector(".row-actions"),
      track: shell.querySelector(".holding-track"),
      details: shell.querySelector(".holding-row"),
      summary: shell.querySelector(".holding-row > summary"),
    };
  }

  function getActionWidth(shell) {
    const { actions } = getParts(shell);
    if (!actions) return 220;
    const rect = actions.getBoundingClientRect();
    return rect.width || 220;
  }

  function openSwipe(shell) {
    const { actions, track } = getParts(shell);
    if (!actions || !track) return;

    const w = getActionWidth(shell);
    closeAllSwipe(shell);

    shell.classList.add("is-open");
    actions.style.transition = "";
    track.style.transition = "";
    actions.style.transform = "translateX(0)";
    track.style.transform = `translateX(-${w}px)`;
  }

  function closeSwipe(shell) {
    const { actions, track } = getParts(shell);
    if (!actions || !track) return;

    shell.classList.remove("is-open");
    actions.style.transition = "";
    track.style.transition = "";
    actions.style.transform = "";
    track.style.transform = "";
  }

  function followSwipe(shell, openedPx) {
    const { actions, track } = getParts(shell);
    if (!actions || !track) return;

    const w = getActionWidth(shell);
    const clamped = Math.max(0, Math.min(w, openedPx));
    const pct = 100 - (clamped / w) * 100;

    actions.style.transition = "none";
    track.style.transition = "none";
    actions.style.transform = `translateX(${pct}%)`;
    track.style.transform = `translateX(-${clamped}px)`;
  }

  function bindDetails() {
    const detailsList = Array.from(document.querySelectorAll(".holding-row"));

    detailsList.forEach((details) => {
      if (details.dataset.detailBound === "1") return;
      details.dataset.detailBound = "1";

      details.addEventListener("toggle", () => {
        if (!details.open) return;
        detailsList.forEach((other) => {
          if (other !== details) other.open = false;
        });
      });
    });
  }

  function bindSwipeShell(shell) {
    if (!shell || shell.dataset.swipeBound === "1") return;
    shell.dataset.swipeBound = "1";

    const { actions, track, summary } = getParts(shell);
    if (!actions || !track || !summary) return;

    actions.addEventListener("click", (e) => e.stopPropagation());
    actions.addEventListener("touchstart", (e) => e.stopPropagation(), { passive: true });

    let startX = 0;
    let startY = 0;
    let dragging = false;
    let horizontal = false;
    let baseOpen = false;
    let openPull = 0;
    let closePush = 0;

    track.addEventListener("touchstart", (e) => {
      if (e.target.closest(".row-actions")) return;
      const t = e.touches[0];
      startX = t.clientX;
      startY = t.clientY;
      dragging = true;
      horizontal = false;
      baseOpen = shell.classList.contains("is-open");
      openPull = 0;
      closePush = 0;

      if (!baseOpen) {
        closeAllSwipe(shell);
      }
    }, { passive: true });

    track.addEventListener("touchmove", (e) => {
      if (!dragging) return;

      const t = e.touches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;

      if (!horizontal) {
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) {
          horizontal = true;
        } else {
          dragging = false;
          return;
        }
      }

      e.preventDefault();

      const w = getActionWidth(shell);

      if (!baseOpen) {
        if (dx < 0) {
          openPull = Math.min(-dx, w);
          followSwipe(shell, openPull);
        }
      } else {
        if (dx > 0) {
          closePush = Math.min(dx, w);
          followSwipe(shell, w - closePush);
        } else {
          followSwipe(shell, w);
        }
      }
    }, { passive: false });

    track.addEventListener("touchend", () => {
      if (!dragging) return;
      dragging = false;

      const w = getActionWidth(shell);

      if (!baseOpen) {
        if (openPull > w * THRESHOLD) {
          openSwipe(shell);
        } else {
          closeSwipe(shell);
        }
      } else {
        if (closePush > w * THRESHOLD) {
          closeSwipe(shell);
        } else {
          openSwipe(shell);
        }
      }
    });

    summary.addEventListener("click", () => {
      if (shell.classList.contains("is-open")) {
        closeSwipe(shell);
      }
    });
  }

  function bindAllSwipe() {
    document.querySelectorAll(".holding-row-shell").forEach(bindSwipeShell);
  }

  function bindOutsideClose() {
    if (document.body.dataset.holdingsOutsideBound === "1") return;
    document.body.dataset.holdingsOutsideBound = "1";

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".holding-row-shell")) {
        closeAllSwipe();
      }
    });
  }

  function updateBucketCounts(panel) {
    if (!panel) return;

    const counts = {
      all: panel.dataset.countAll || "0",
      spec: panel.dataset.countSpec || "0",
      nisa: panel.dataset.countNisa || "0",
      margin: panel.dataset.countMargin || "0",
    };

    document.querySelectorAll("[data-bucket-count]").forEach((el) => {
      const key = el.dataset.bucketCount;
      el.textContent = counts[key] || "0";
    });
  }

  function applyBucketVisibility(activeBucket) {
    const activePanel = document.querySelector(".broker-panel.active");
    if (!activePanel) return;

    let visibleCount = 0;

    activePanel.querySelectorAll("[data-account-section]").forEach((section) => {
      const account = section.dataset.account;
      let show = false;

      if (activeBucket === "all") {
        show = true;
      } else if (activeBucket === "spec" && account === "SPEC") {
        show = true;
      } else if (activeBucket === "nisa" && account === "NISA") {
        show = true;
      } else if (activeBucket === "margin" && account === "MARGIN") {
        show = true;
      }

      section.classList.toggle("hidden", !show);
      if (show) visibleCount += 1;
    });

    const emptyBox = activePanel.querySelector("[data-panel-empty]");
    if (emptyBox) {
      emptyBox.classList.toggle("show", visibleCount === 0);
    }
  }

  function setActiveBroker(activeBroker, activeBucket) {
    document.querySelectorAll("[data-broker-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.broker === activeBroker);
    });

    document.querySelectorAll("[data-broker-panel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.broker === activeBroker);
    });

    const activePanel = document.querySelector(`.broker-panel[data-broker="${activeBroker}"]`);
    updateBucketCounts(activePanel);
    applyBucketVisibility(activeBucket);
  }

  function setActiveBucket(activeBucket) {
    document.querySelectorAll("[data-bucket-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.bucket === activeBucket);
    });
    applyBucketVisibility(activeBucket);
  }

  function updateUrl(activeBroker, activeBucket) {
    const url = new URL(window.location.href);
    url.searchParams.set("broker", activeBroker);
    url.searchParams.set("bucket", activeBucket);
    window.history.replaceState({}, "", url.toString());
  }

  function bindTabs() {
    const root = document.getElementById("holdingsPage");
    if (!root) return;

    let activeBroker = root.dataset.initialBroker || "RAKUTEN";
    let activeBucket = root.dataset.initialBucket || "all";

    setActiveBroker(activeBroker, activeBucket);
    setActiveBucket(activeBucket);
    updateUrl(activeBroker, activeBucket);

    document.querySelectorAll("[data-broker-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeBroker = btn.dataset.broker || "RAKUTEN";
        closeAllSwipe();
        setActiveBroker(activeBroker, activeBucket);
        updateUrl(activeBroker, activeBucket);
      });
    });

    document.querySelectorAll("[data-bucket-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeBucket = btn.dataset.bucket || "all";
        closeAllSwipe();
        setActiveBucket(activeBucket);
        updateUrl(activeBroker, activeBucket);
      });
    });
  }

  function boot() {
    bindDetails();
    bindAllSwipe();
    bindOutsideClose();
    bindTabs();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();