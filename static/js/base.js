document.addEventListener("DOMContentLoaded", function () {
  /* ===== ケアレット行 + アクションバー初期化 ===== */
  const tabBar   = document.querySelector(".bottom-tab");
  const tabList  = tabBar?.querySelector(".tab-list");
  const tabItems = tabList ? Array.from(tabList.querySelectorAll(".tab-item")) : [];
  if (!tabBar || !tabList || tabItems.length === 0) return;

  // 既存の飾りケアレットやインライン caret を除去（見た目の差異をなくす）
  tabItems.forEach(tab=>{
    tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']")
      .forEach(n => n.remove());
  });

  // ケアレット行を作る（タブ数に合わせて 1:1 スロット）
  let caretRow = document.querySelector(".tab-caret-row");
  if (!caretRow){
    caretRow = document.createElement("div");
    caretRow.className = "tab-caret-row";
    document.body.appendChild(caretRow);
  }
  caretRow.innerHTML = ""; // 再構築

  // 共用アクションバー
  let actionbar = document.getElementById("tab-actionbar");
  if (!actionbar){
    actionbar = document.createElement("div");
    actionbar.id = "tab-actionbar";
    actionbar.className = "tab-actionbar";
    actionbar.setAttribute("role","menu");
    document.body.appendChild(actionbar);
  }

  let openFor = null;
  let justOpenedAt = 0;

  function closeBar(){
    if (openFor){
      const btn = caretRow.querySelector(`[data-tab-id="${openFor.dataset.tabId}"]`);
      if (btn) btn.setAttribute("aria-expanded","false");
      openFor.classList.remove("open");
    }
    actionbar.classList.remove("show");
    setTimeout(()=>{
      if (!actionbar.classList.contains("show")){
        actionbar.style.display = "none";
        actionbar.innerHTML = "";
      }
    }, 120);
    openFor = null;
  }

  function openBarFor(tabItem){
    const submenu = tabItem.querySelector(".sub-menu");
    if (!submenu) return;

    if (openFor && openFor !== tabItem) closeBar();

    // メニューを再生成
    actionbar.innerHTML = "";
    const links = submenu.querySelectorAll("a");
    if (links.length === 0){
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      none.setAttribute("role","menuitem");
      actionbar.appendChild(none);
    } else {
      links.forEach(a=>{
        const href   = a.getAttribute("href") || "#";
        const txt    = (a.textContent || "").trim();
        const target = a.getAttribute("target") || "";
        const btn    = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = txt;
        btn.setAttribute("role","menuitem");
        btn.addEventListener("click", (e)=>{
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          if (target === "_blank") return;
          e.preventDefault();
          window.location.href = href;
        }, {passive:false});
        actionbar.appendChild(btn);
      });
    }

    // 対応するケアレットを展開状態に
    const btn = caretRow.querySelector(`[data-tab-id="${tabItem.dataset.tabId}"]`);
    if (btn) btn.setAttribute("aria-expanded","true");

    tabItem.classList.add("open");
    actionbar.style.display = "flex";
    requestAnimationFrame(()=> actionbar.classList.add("show"));
    openFor = tabItem;
    justOpenedAt = Date.now();
  }

  // 各タブに ID を振って、ケアレット行にスロットを作成
  tabItems.forEach((tab, idx)=>{
    tab.dataset.tabId = `t${idx}`;

    const slot = document.createElement("div");
    slot.className = "tab-caret-slot";

    const hasSub = !!tab.querySelector(".sub-menu");
    if (hasSub){
      const cbtn = document.createElement("button");
      cbtn.type = "button";
      cbtn.className = "tab-caret-btn";
      cbtn.setAttribute("aria-expanded","false");
      cbtn.setAttribute("aria-controls","tab-actionbar");
      cbtn.setAttribute("aria-label","サブメニューを開閉");
      cbtn.dataset.tabId = tab.dataset.tabId;
      cbtn.textContent = "▾";
      cbtn.addEventListener("click",(e)=>{
        e.preventDefault();
        e.stopPropagation();
        if (openFor === tab) closeBar(); else openBarFor(tab);
      }, {passive:false});
      slot.appendChild(cbtn);
    } else {
      // サブメニューがないタブはスペーサを置き、行高さを揃える
      const spacer = document.createElement("div");
      spacer.style.height   = "26px";
      spacer.style.minWidth = "28px";
      slot.appendChild(spacer);
    }

    caretRow.appendChild(slot);

    // タブ本体は通常遷移
    const link = tab.querySelector(".tab-link");
    if (link){
      link.addEventListener("click",(e)=>{
        const href   = link.getAttribute("href");
        const target = link.getAttribute("target") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
        if (target === "_blank") return;
        e.preventDefault();
        window.location.href = href;
      }, {passive:false});
    }
  });

  // 外側クリック/ESC/リサイズで閉じる
  document.addEventListener("click", (e)=>{
    if (!openFor) return;
    if (Date.now() - justOpenedAt < 140) return;
    const inTab  = !!e.target.closest(".bottom-tab .tab-item");
    const inBar  = !!e.target.closest(".tab-actionbar");
    const inCaret= !!e.target.closest(".tab-caret-row");
    if (!inTab && !inBar && !inCaret) closeBar();
  }, {passive:true});
  window.addEventListener("keydown", (e)=>{ if (e.key === "Escape" && openFor) closeBar(); }, {passive:true});
  window.addEventListener("resize", ()=> closeBar(), {passive:true});
});