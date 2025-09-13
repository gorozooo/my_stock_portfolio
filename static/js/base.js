document.addEventListener("DOMContentLoaded", function () {
  // 下タブ初期化
  (function initBottomTab(){
    const tabBar   = document.querySelector(".bottom-tab");
    const tabList  = tabBar?.querySelector(".tab-list");
    const tabItems = tabList ? Array.from(tabList.querySelectorAll(".tab-item")) : [];
    if (!tabBar || !tabList || tabItems.length === 0) return;

    // 既存の飾りケアレットは排除
    tabItems.forEach(tab=>{
      tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']")
        .forEach(n => n.remove());
    });

    // 共通アクションバー（単一）
    let actionbar = document.getElementById("tab-actionbar");
    if (!actionbar) {
      actionbar = document.createElement("div");
      actionbar.id = "tab-actionbar";
      actionbar.className = "tab-actionbar";
      actionbar.setAttribute("role","menu");
      document.body.appendChild(actionbar);
    }

    let openFor = null;
    let justOpenedAt = 0;

    function closeBar() {
      if (openFor) {
        openFor.classList.remove("open");
        const caret = openFor.querySelector(".tab-caret-btn");
        if (caret) caret.setAttribute("aria-expanded", "false");
      }
      actionbar.classList.remove("show");
      setTimeout(() => {
        if (!actionbar.classList.contains("show")) {
          actionbar.style.display = "none";
          actionbar.innerHTML = "";
        }
      }, 120);
      openFor = null;
    }

    function openBarFor(tabItem) {
      const submenu = tabItem.querySelector(".sub-menu");
      if (!submenu) return;
      if (openFor && openFor !== tabItem) closeBar();

      // メニュー再生成
      actionbar.innerHTML = "";
      const links = submenu.querySelectorAll("a");
      if (links.length === 0) {
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

      // 位置（スマホ固定 / PC はタブ真上）
      const rect = tabItem.getBoundingClientRect();
      if (window.matchMedia("(min-width: 768px)").matches) {
        const width = Math.min(560, Math.max(260, rect.width * 1.7));
        const left  = Math.min(Math.max(8, rect.left + rect.width/2 - width/2),
                               window.innerWidth - width - 8);
        actionbar.style.left   = left + "px";
        actionbar.style.right  = "auto";
        actionbar.style.width  = width + "px";
        actionbar.style.bottom = (window.innerHeight - rect.top + 12) + "px";
      } else {
        actionbar.style.left   = "8px";
        actionbar.style.right  = "8px";
        actionbar.style.width  = "auto";
        actionbar.style.bottom = "calc(96px + env(safe-area-inset-bottom,0))";
      }

      tabItem.classList.add("open");
      const caret = tabItem.querySelector(".tab-caret-btn");
      if (caret) caret.setAttribute("aria-expanded","true");

      actionbar.style.display = "flex";
      requestAnimationFrame(()=> actionbar.classList.add("show"));
      openFor = tabItem;
      justOpenedAt = Date.now();
    }

    tabItems.forEach(tab=>{
      const link    = tab.querySelector(".tab-link");
      const submenu = tab.querySelector(".sub-menu");

      // 左側（アイコン+ラベル）をラップして横並びを確定
      if (link){
        const icon  = link.querySelector("i");
        const label = link.querySelector(".label") || link.querySelector("span");
        let leftWrap = link.querySelector(".left");
        if (!leftWrap){
          leftWrap = document.createElement("span");
          leftWrap.className = "left";
          // 既存の子要素を左ラップに移動（ケアレットは後で右端に付ける）
          const toMove = [];
          link.childNodes.forEach(n=>{
            if (n.nodeType === 1){ // Element
              const el = n;
              if (!el.classList.contains("tab-caret-btn")) toMove.push(el);
            }
          });
          toMove.forEach(el=> leftWrap.appendChild(el));
          link.prepend(leftWrap);
        }
      }

      // サブメニューがあるタブには“押せるケアレット”を右端に必ず追加
      if (submenu){
        tab.classList.add("has-sub");

        // 重複を避ける
        tab.querySelectorAll(".tab-caret-btn").forEach(n=> n.remove());

        const caret = document.createElement("button");
        caret.type = "button";
        caret.className = "tab-caret-btn";
        caret.setAttribute("aria-expanded","false");
        caret.setAttribute("aria-controls","tab-actionbar");
        caret.setAttribute("aria-label","サブメニューを開閉");
        caret.textContent = "▾";
        (link || tab).appendChild(caret);  // 右端に配置（flexの最後）

        caret.addEventListener("click", (e)=>{
          e.preventDefault();
          e.stopPropagation();
          if (openFor === tab) closeBar(); else openBarFor(tab);
        }, {passive:false});
      }

      // 本体は通常遷移（サブメニューがあってもケアレット以外は遷移）
      if (link){
        link.addEventListener("click", (e)=>{
          if (e.target.closest(".tab-caret-btn")) return; // ケアレットが押された時は無視
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
      const inTab = !!e.target.closest(".bottom-tab .tab-item");
      const inBar = !!e.target.closest(".tab-actionbar");
      if (!inTab && !inBar) closeBar();
    }, {passive:true});
    window.addEventListener("keydown", (e)=>{ if (e.key === "Escape" && openFor) closeBar(); }, {passive:true});
    window.addEventListener("resize", ()=> closeBar(), {passive:true});
  })();
});