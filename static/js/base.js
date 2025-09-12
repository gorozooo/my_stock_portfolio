// base-bottomtab.js  —  既存の“下タブ内ケアレット”を生かして開閉、なければ生成して開閉に使う
document.addEventListener("DOMContentLoaded", function () {
  /* =========================
     軽量ローディング（必要最低限）
  ========================= */
  const style = document.createElement("style");
  style.innerHTML = `
    /* 既存CSSの ::after ケアレットを無効化（実体ボタンに一本化） */
    .bottom-tab .tab-item.has-sub .tab-link::after { content: none !important; }

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
  loading.innerHTML = `<div class="loading-text" aria-live="polite">Now Loading…</div><div class="loading-bar" aria-hidden="true"></div>`;
  document.body.appendChild(loading);

  function showLoading(cb){
    loading.style.display = "flex";
    requestAnimationFrame(()=>{
      loading.style.opacity = "1";
      if (typeof cb === "function") setTimeout(cb, 40);
    });
  }
  function hideLoading(){
    loading.style.opacity = "0";
    setTimeout(()=>{ loading.style.display = "none"; }, 200);
  }
  window.__showLoading__ = showLoading;

  showLoading();
  window.addEventListener("load", hideLoading, { passive: true });
  window.addEventListener("beforeunload", ()=> showLoading(), { passive: true });
  window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, { passive: true });

  /* =====================================
     下タブ & サブメニュー（ボタンバー版）
     - 既存の“ケアレット”（↓）が .tab-link 内にあればそれを利用
     - 無ければケアレットボタンを生成
     - サブメニューの <a> をピル型ボタンに変換して横並び（オーバーレイ無し）
  ===================================== */
  (function(){
    const tabBar   = document.querySelector('.bottom-tab');
    const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
    if (!tabBar || !tabItems.length) return;

    // 既存の別UI要素を除去
    document.querySelectorAll('.tab-backdrop, .bottom-sheet, .popover-menu')
      .forEach(n => n.remove());

    // 共有アクションバー
    const actionbar = document.createElement('div');
    actionbar.className = 'tab-actionbar';
    actionbar.setAttribute('role', 'group');
    actionbar.setAttribute('aria-label', 'クイックアクション');
    document.body.appendChild(actionbar);

    // タブ毎にケアレットを準備
    tabItems.forEach(t => {
      const submenu = t.querySelector('.sub-menu');
      const hasSub  = !!submenu;

      // サブメニューが無いタブに“既存ケアレット”があれば削除（誤表示対策）
      if (!hasSub) {
        const strayCarets = t.querySelectorAll('.tab-caret, .caret, .caret-icon');
        strayCarets.forEach(n => n.remove());
        t.classList.remove('has-sub', 'open');
        return;
      }

      // “サブメニューあり”のフラグ
      t.classList.add('has-sub');

      // 既存ケアレットの探索（できるだけ多くの命名に対応）
      let caret =
        t.querySelector('.tab-link .tab-caret, .tab-link .caret, .tab-link .caret-icon') ||
        t.querySelector('.tab-caret, .caret, .caret-icon');

      // 既存が無ければ生成（.tab-link の末尾に入れる）
      if (!caret) {
        const link = t.querySelector('.tab-link');
        if (link) {
          caret = document.createElement('button');
          caret.type = 'button';
          caret.className = 'tab-caret';
          caret.setAttribute('aria-label', 'メニューを開閉');
          caret.textContent = '▾';
          // 見た目を崩さないように内側右端へ
          caret.style.marginLeft = '6px';
          link.appendChild(caret);
        }
      }

      // ケアレットが存在すれば、ボタンらしい属性を付与
      if (caret) {
        if (caret.tagName !== 'BUTTON') caret.setAttribute('role', 'button');
        caret.setAttribute('tabindex', '0');
        caret.setAttribute('aria-expanded', 'false');
        caret.classList.add('tab-caret'); // クラスを統一
      }
    });

    let openFor = null;
    let justOpenedAt = 0;
    const OPEN_IGNORE_MS = 200;

    function buildButtonsFrom(submenu, container){
      container.innerHTML = '';
      submenu.querySelectorAll('a').forEach(a=>{
        const href = a.getAttribute('href') || '#';
        const txt  = (a.textContent || '').trim();
        const btn  = document.createElement('a');
        btn.className = 'ab-btn';
        btn.href = href;
        btn.textContent = txt;
        btn.addEventListener('click', (e)=>{
          if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
          e.preventDefault();
          (window.__showLoading__ || showLoading)(()=> window.location.href = href);
        }, { passive: false });
        container.appendChild(btn);
      });
    }

    function openBarFor(tabItem){
      const submenu = tabItem.querySelector('.sub-menu');
      if (!submenu) return;
      if (openFor && openFor !== tabItem) closeBar();

      buildButtonsFrom(submenu, actionbar);

      // 位置決め
      const rect = tabItem.getBoundingClientRect();
      const isDesktop = window.matchMedia('(min-width: 768px)').matches;
      if (isDesktop) {
        const width = Math.min(560, Math.max(260, rect.width * 1.7));
        const left  = Math.min(Math.max(8, rect.left + rect.width/2 - width/2), window.innerWidth - width - 8);
        actionbar.style.left   = left + 'px';
        actionbar.style.right  = 'auto';
        actionbar.style.width  = width + 'px';
        actionbar.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
      } else {
        actionbar.style.left   = '8px';
        actionbar.style.right  = '8px';
        actionbar.style.width  = 'auto';
        actionbar.style.bottom = 'calc(86px + env(safe-area-inset-bottom,0) + 8px)';
      }

      tabItem.classList.add('open');
      const caret = tabItem.querySelector('.tab-caret, .caret, .caret-icon');
      if (caret) caret.setAttribute('aria-expanded', 'true');

      actionbar.style.display = 'flex';
      requestAnimationFrame(()=> actionbar.classList.add('show'));

      openFor = tabItem;
      justOpenedAt = Date.now();
    }

    function closeBar(){
      if (openFor) {
        openFor.classList.remove('open');
        const caret = openFor.querySelector('.tab-caret, .caret, .caret-icon');
        if (caret) caret.setAttribute('aria-expanded', 'false');
      }
      actionbar.classList.remove('show');
      setTimeout(()=>{
        if (!actionbar.classList.contains('show')) {
          actionbar.style.display = 'none';
          actionbar.innerHTML = '';
        }
      }, 180);
      openFor = null;
    }

    // クリックの付け替え：ケアレットは開閉、タブ本体は遷移
    tabItems.forEach(tab=>{
      const tabLink = tab.querySelector('.tab-link');
      const submenu = tab.querySelector('.sub-menu');
      const caret   = tab.querySelector('.tab-caret, .caret, .caret-icon');

      if (caret && submenu){
        // 既存ケアレットを“開閉ボタン”として有効化
        ['click','pointerup','keydown'].forEach(ev=>{
          caret.addEventListener(ev, (e)=>{
            if (e.type === 'keydown' && !(e.key === 'Enter' || e.key === ' ')) return;
            e.preventDefault(); e.stopPropagation();
            if (openFor === tab) closeBar(); else openBarFor(tab);
          }, { passive: false });
        });
      }

      if (tabLink){
        tabLink.addEventListener('click', (e)=>{
          // ケアレット領域のクリックは無視（開閉は上のハンドラで処理）
          if (e.target.closest('.tab-caret, .caret, .caret-icon')) return;
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')){
            e.preventDefault();
            (window.__showLoading__ || showLoading)(()=> window.location.href = href);
          }
        }, { passive: false });
      }
    });

    // 外側クリックで閉じる（開直後は無視）
    document.addEventListener('click', (e)=>{
      if (!openFor) return;
      if (Date.now() - justOpenedAt < OPEN_IGNORE_MS) return;
      const inTab = !!e.target.closest('.bottom-tab .tab-item');
      const inBar = !!e.target.closest('.tab-actionbar');
      if (!inTab && !inBar) closeBar();
    }, { passive: true });

    // ESC/リサイズで閉じる
    window.addEventListener('keydown', (e)=>{ if (e.key === 'Escape' && openFor) closeBar(); }, { passive: true });
    window.addEventListener('resize', closeBar, { passive: true });
  })();

  /* =========================
     現在ページ名（任意）
  ========================= */
  const currentURL = location.pathname;
  const currentPageNameEl = document.getElementById("current-page-name");
  if (currentPageNameEl){
    const tabLinks = document.querySelectorAll(".tab-item .tab-link");
    let found = false;
    tabLinks.forEach(tl=>{
      const href = tl.getAttribute("href");
      const nameSpan = tl.querySelector("span");
      if (href && nameSpan && currentURL.startsWith(href)) {
        currentPageNameEl.textContent = nameSpan.textContent;
        found = true;
      }
    });
    if (!found) currentPageNameEl.textContent = currentURL.replace(/^\/|\/$/g,"") || "ホーム";
  }
});