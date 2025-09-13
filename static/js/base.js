// LINE風タブ + ケアレット列 + ピル型サブメニュー
document.addEventListener("DOMContentLoaded", () => {
  const tabBar   = document.querySelector(".bottom-tab");
  const tabItems = Array.from(document.querySelectorAll(".bottom-tab .tab-item"));
  if (!tabBar || tabItems.length === 0) return;

  // 1) 既存の飾りケアレットを除去（ページ差をなくす）
  tabItems.forEach(t =>
    t.querySelectorAll(".tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove())
  );

  // 2) 下タブの直下に「ケアレット列」を自動生成（タブ数と同じ列数）
  const caretRow = document.createElement("div");
  caretRow.className = "caret-row";
  tabItems.forEach(tab => {
    const cell = document.createElement("div");
    cell.className = "caret-cell";

    const hasSub = !!tab.querySelector(".sub-menu");
    if (hasSub) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "caret-btn";
      btn.setAttribute("aria-expanded", "false");
      btn.title = "サブメニュー";
      btn.textContent = "▾";
      cell.appendChild(btn);
    } else {
      const ph = document.createElement("div");
      ph.className = "caret-placeholder";
      cell.appendChild(ph);
    }
    caretRow.appendChild(cell);
  });
  document.body.appendChild(caretRow);

  // 3) サブメニューを描画するアクションバー（共用）
  const actionbar = document.createElement("div");
  actionbar.className = "tab-actionbar";
  document.body.appendChild(actionbar);

  let openIndex = -1;
  const showBar = (idx) => {
    const tab = tabItems[idx];
    const submenu = tab.querySelector(".sub-menu");
    if (!submenu) return;

    // ボタン群を再構築
    actionbar.innerHTML = "";
    const links = submenu.querySelectorAll("a");
    if (links.length === 0) {
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      actionbar.appendChild(none);
    } else {
      links.forEach(a => {
        const href   = a.getAttribute("href") || "#";
        const target = a.getAttribute("target") || "";
        const label  = (a.textContent || "").trim();
        const btn = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = label;
        btn.addEventListener("click", e => {
          // # / javascript: / _blank はそのまま
          if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
          e.preventDefault();
          window.location.href = href;
        }, { passive:false });
        actionbar.appendChild(btn);
      });
    }

    // ケアレットの状態反映（全てリセット→対象のみON）
    caretRow.querySelectorAll(".caret-btn[aria-expanded]").forEach(b => b.setAttribute("aria-expanded", "false"));
    const myCaret = caretRow.querySelectorAll(".caret-btn")[idx];
    if (myCaret) myCaret.setAttribute("aria-expanded", "true");

    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));
    openIndex = idx;
  };

  const hideBar = () => {
    actionbar.classList.remove("show");
    caretRow.querySelectorAll(".caret-btn[aria-expanded]").forEach(b => b.setAttribute("aria-expanded", "false"));
    setTimeout(() => { if (!actionbar.classList.contains("show")) actionbar.style.display = "none"; }, 160);
    openIndex = -1;
  };

  // 4) タブ本体は通常遷移・ケアレットは開閉
  tabItems.forEach((tab, idx) => {
    const link = tab.querySelector(".tab-link");
    const hasSub = !!tab.querySelector(".sub-menu");

    if (link) {
      link.addEventListener("click", (e) => {
        const href = link.getAttribute("href");
        const target = link.getAttribute("target") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
        e.preventDefault();
        window.location.href = href;
      }, { passive:false });
    }

    if (hasSub) {
      const caretBtn = caretRow.querySelectorAll(".caret-btn")[idx];
      caretBtn.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        if (openIndex === idx) hideBar(); else showBar(idx);
      }, { passive:false });
    }
  });

  // 5) 外側タップ・Esc・リサイズで閉じる
  document.addEventListener("click", (e) => {
    if (openIndex === -1) return;
    const inBar  = !!e.target.closest(".tab-actionbar");
    const inRow  = !!e.target.closest(".caret-row");
    const inTabs = !!e.target.closest(".bottom-tab");
    if (!inBar && !inRow && !inTabs) hideBar();
  }, { passive:true });
  window.addEventListener("keydown", e => { if (e.key === "Escape" && openIndex !== -1) hideBar(); }, { passive:true });
  window.addEventListener("resize", hideBar, { passive:true });
});