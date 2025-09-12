// base-bottomtab.js
// 既存ケアレット（↓）がある場合はそれを開閉トグルとして使用。
// 無い場合のみケアレットを自動挿入。重複していたら既存を優先して自動挿入分は削除。

document.addEventListener("DOMContentLoaded", function () {
  /* =========================
     軽量ローディング
  ========================= */
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
  window.addEventListener("load", hideLoading, { passive:true });
  window.addEventListener("beforeunload", ()=> showLoading(), { passive:true });
  window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, { passive:true });

  /* =====================================
     下タブ & サブメニュー（ボタンバー版）
     - 既存ケアレットを優先利用
     - 無ければ自動挿入
     - 既存+自動の重複は自動の方を削除
  ===================================== */
  (function(){
    const tabBar   = document.querySelector('.bottom-tab');
    const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
    if (!tabBar || !tabItems.length) return;

    // 既存のポップオーバー/シートは撤去
    document.querySelectorAll('.tab-backdrop, .bottom-sheet, .popover-menu').forEach(n => n.remove());

    // 共有アクションバー
    const actionbar = document.createElement('div');
    actionbar.className = 'tab-actionbar';
    actionbar.setAttribute('role', 'group');
    actionbar.setAttribute('aria-label', 'クイックアクション');
    document.body.appendChild(actionbar);

    let openFor = null;
    let justOpenedAt = 0;
    const OPEN_IGNORE_MS = 200;

    // ケアレット探索ヘルパ
    function findExistingCaret(tab){
      // よくある命名を幅広くサポート
      const selectors = [
        '.tab-caret',
        '.caret',
        '.caret-icon',
        '[data-caret]',
        '[data-role="caret"]'
      ];
      let carets = [];
      selectors.forEach(sel=>{
        tab.querySelectorAll(sel).forEach(el=> carets.push(el));
      });
      if (carets.length > 1){
        // 先頭を残して残りは削除（見た目崩さないよう display:none ではなく remove）
        carets.slice(1).forEach(el=> el.remove());
      }
      return carets[0] || null;
    }

    // ケアレット生成（必要な場合のみ）
    function injectCaret(tab){
      const link = tab.querySelector('.tab-link');
      const btn  = document.createElement('button');
      btn.type   = 'button';
      btn.className = 'tab-caret';
      btn.setAttribute('aria-label', 'メニューを開閉');
      btn.setAttribute('aria-expanded', 'false');
      btn.dataset.injected = "1";
      btn.textContent = '▾';
      // 既存UIに合わせて、可能なら tab-link の末尾に入れる
      if (link){
        link.appendChild(btn);
      } else {
        tab.appendChild(btn);
      }
      return btn;
    }

    // アクションバー表示
    function openBarFor(tabItem){
      const submenu = tabItem.querySelector('.sub-menu');
      if (!submenu) return;

      if (openFor && openFor !== tabItem) closeBar();

      actionbar.innerHTML = '';
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
        }, { passive:false });
        actionbar.appendChild(btn);
      });

      // 位置
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
      const caret = tabItem.querySelector('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]');
      if (caret) caret.setAttribute('aria-expanded', 'true');

      actionbar.style.display = 'flex';
      requestAnimationFrame(()=> actionbar.classList.add('show'));

      openFor = tabItem;
      justOpenedAt = Date.now();
    }

    function closeBar(){
      if (openFor) {
        openFor.classList.remove('open');
        const caret = openFor.querySelector('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]');
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

    // タブ毎のセットアップ
    tabItems.forEach(tab=>{
      const submenu = tab.querySelector('.sub-menu');

      // サブメニューがないのにケアレットが存在する場合は削除
      if (!submenu){
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(el=> el.remove());
        return;
      }

      // ケアレット確定：既存優先、無ければ挿入
      let caret = findExistingCaret(tab);
      if (!caret) {
        caret = injectCaret(tab);
      } else {
        // 既存が span などの非ボタンなら role / tabindex を付与
        if (caret.tagName !== 'BUTTON') {
          caret.setAttribute('role', 'button');
          caret.setAttribute('tabindex', '0');
        }
        // 既存の中に文字以外（アイコンフォント等）でも OK
      }

      // クリック/Enter/Spaceで開閉
      const handler = (e)=>{
        if (e.type === 'keydown' && !(e.key === 'Enter' || e.key === ' ')) return;
        e.preventDefault();
        e.stopPropagation();
        if (openFor === tab) closeBar(); else openBarFor(tab);
      };
      caret.addEventListener('click', handler, { passive:false });
      caret.addEventListener('keydown', handler, { passive:false });

      // タブ本体のリンクは通常遷移（caret のときは止める）
      const tabLink = tab.querySelector('.tab-link');
      if (tabLink){
        tabLink.addEventListener('click', (e)=>{
          if (e.target.closest('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]')) return;
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')){
            e.preventDefault();
            (window.__showLoading__ || showLoading)(()=> window.location.href = href);
          }
        }, { passive:false });
      }
    });

    // 外側クリック/ESC/リサイズで閉じる（開直後は無視）
    document.addEventListener('click', (e)=>{
      if (!openFor) return;
      if (Date.now() - justOpenedAt < OPEN_IGNORE_MS) return;
      const inTab = !!e.target.closest('.bottom-tab .tab-item');
      const inBar = !!e.target.closest('.tab-actionbar');
      if (!inTab && !inBar) closeBar();
    }, { passive:true });

    window.addEventListener('keydown', (e)=>{
      if (e.key === 'Escape' && openFor) closeBar();
    }, { passive:true });

    window.addEventListener('resize', closeBar, { passive:true });
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