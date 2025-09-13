// base.js
document.addEventListener("DOMContentLoaded", function () {
  // 1) ローディング（軽量・全ページ同一）
  (function () {
    const style = document.createElement("style");
    style.innerHTML = `
      #loading-overlay{position:fixed;inset:0;background:rgba(10,10,20,.95);
        display:none;opacity:0;transition:opacity .22s ease;z-index:9999;
        display:flex;align-items:center;justify-content:center;flex-direction:column}
      #loading-overlay .loading-text{color:#0ff;font:700 22px/1.2 "Orbitron",system-ui;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff}
      #loading-overlay .loading-bar{width:220px;height:6px;border-radius:4px;margin-top:12px;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff);background-size:200% 100%;
        animation:loadslide 2s linear infinite}
      @keyframes loadslide{0%{background-position:0 0}100%{background-position:200% 0}}
    `;
    document.head.appendChild(style);
    const loading = document.createElement("div");
    loading.id = "loading-overlay";
    loading.innerHTML = `<div class="loading-text">Now Loading…</div><div class="loading-bar"></div>`;
    document.body.appendChild(loading);
    function showLoading(cb){
      loading.style.display="flex";
      requestAnimationFrame(()=>{
        loading.style.opacity="1";
        if(cb) setTimeout(cb,40);
      });
    }
    function hideLoading(){
      loading.style.opacity="0";
      setTimeout(()=>{ loading.style.display="none"; },200);
    }
    window.__showLoading__ = showLoading;
    showLoading();
    window.addEventListener("load", hideLoading, {passive:true});
    window.addEventListener("beforeunload", ()=> showLoading(), {passive:true});
    window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, {passive:true});
  })();

  // 2) 下タブを強制的に“同じ見た目/挙動”に初期化
  (function initBottomTab(){
    try{
      const tabBar   = document.querySelector(".bottom-tab");
      const tabList  = tabBar?.querySelector(".tab-list");
      const tabItems = tabList ? Array.from(tabList.querySelectorAll(".tab-item")) : [];
      if (!tabBar || !tabList || tabItems.length === 0) return;

      // a) 列数を実数に同期（ページ毎の数違いを吸収）
      tabList.style.setProperty("--tab-count", String(tabItems.length));

      // b) 既存の“飾りケアレット”は全削除（重複表示を根絶）
      tabItems.forEach(tab=>{
        tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']")
          .forEach(n => n.remove());
      });

      // c) 共通アクションバー（単一）
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

        // メニュー再生成（ページ差分を殺す）
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
              (window.__showLoading__ || ((cb)=>cb()))(()=> window.location.href = href);
            }, {passive:false});
            actionbar.appendChild(btn);
          });
        }

        // 位置（スマホは左右余白固定 / PCはタブの真上）
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

      // d) サブメニュー有タブに“押せるケアレット”を必ず注入（重複なし）
      tabItems.forEach(tab=>{
        const link    = tab.querySelector(".tab-link");
        const submenu = tab.querySelector(".sub-menu");

        // ボタンの中は 〔アイコン | ラベル | ケアレット〕の3カラムに統一
        // ケアレットは必ず右端の3カラム目に入る → 高さ/位置が全ページで一致
        if (submenu) {
          tab.classList.add("has-sub");
          const caret = document.createElement("button");
          caret.type = "button";
          caret.className = "tab-caret-btn";
          caret.setAttribute("aria-expanded","false");
          caret.setAttribute("aria-controls","tab-actionbar");
          caret.setAttribute("aria-label","サブメニューを開閉");
          caret.textContent = "▾";
          (link || tab).appendChild(caret);

          caret.addEventListener("click", (e)=>{
            e.preventDefault();
            e.stopPropagation();
            if (openFor === tab) closeBar(); else openBarFor(tab);
          }, {passive:false});
        }

        // 本体は通常遷移（どのページでも同じ挙動）
        if (link) {
          link.addEventListener("click", (e)=>{
            const href   = link.getAttribute("href");
            const target = link.getAttribute("target") || "";
            if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
            if (target === "_blank") return;
            e.preventDefault();
            (window.__showLoading__ || ((cb)=>cb()))(()=> window.location.href = href);
          }, {passive:false});
        }
      });

      // e) 外側クリック/ESC/リサイズで閉じる（どのページでも同じ）
      document.addEventListener("click", (e)=>{
        if (!openFor) return;
        if (Date.now() - justOpenedAt < 140) return;
        const inTab = !!e.target.closest(".bottom-tab .tab-item");
        const inBar = !!e.target.closest(".tab-actionbar");
        if (!inTab && !inBar) closeBar();
      }, {passive:true});
      window.addEventListener("keydown", (e)=>{ if (e.key === "Escape" && openFor) closeBar(); }, {passive:true});
      window.addEventListener("resize", ()=> closeBar(), {passive:true});

    }catch(err){
      console.error("[bottom-tab] init failed:", err);
    }
  })();
});