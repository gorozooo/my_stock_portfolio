// static/js/base.js
// ★ローダー専用（下タブ/サブメニューには触れません）
// - 初期は表示（HTML/loader.cssのまま）
// - window.load / readyState=complete / visibilitychange / pageshow(bfcache) で確実に閉じる
// - 例外時・JSエラー時でも強制クローズ（!important で畳む）
// - 遷移時は pointerdown/submit/beforeunload で素早く表示

(function () {
  // ====== ユーティリティ ======
  function hardShow(el) {
    if (!el) return;
    // 既存CSSに勝てるように !important で明示
    el.classList.remove('hidden');
    el.style.setProperty('display', 'flex', 'important');
    el.style.setProperty('opacity', '1', 'important');
    el.style.setProperty('visibility', 'visible', 'important');
    el.style.setProperty('pointer-events', 'auto', 'important');
    el.style.setProperty('z-index', '2147483647', 'important'); // 常に最前
    document.documentElement.style.cursor = 'wait';
    document.body.style.cursor = 'wait';
  }

  function hardHide(el) {
    if (!el) return;
    el.classList.add('hidden'); // loader.css のトランジションも生かす
    // さらに確実に畳む
    el.style.setProperty('opacity', '0', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.style.setProperty('pointer-events', 'none', 'important');
    // 遅延で display も落とす（レイアウト軽減）
    setTimeout(() => {
      el.style.setProperty('display', 'none', 'important');
      document.documentElement.style.cursor = '';
      document.body.style.cursor = '';
    }, 350);
  }

  function createOverlayIfMissing() {
    let el = document.getElementById('loading-screen');
    if (el) return el;

    // 無い場合は簡易オーバーレイを自動生成（見た目はシンプルだが同じ挙動）
    const style = document.createElement('style');
    style.textContent = `
      #__loading_overlay__{
        position:fixed; inset:0; z-index:2147483647 !important;
        background:rgba(10,10,20,.95);
        display:flex; align-items:center; justify-content:center; flex-direction:column;
        opacity:1; visibility:visible; pointer-events:auto;
        transition:opacity .35s ease, visibility .35s ease;
      }
      #__loading_overlay__.hidden{
        opacity:0; visibility:hidden; pointer-events:none;
      }
      #__loading_overlay__ .loading-text{
        color:#0ff; font:700 22px/1.2 "Orbitron",system-ui;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff; margin-bottom:12px;
      }
      #__loading_overlay__ .loading-bar{
        width:220px; height:6px; border-radius:4px;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff);
        background-size:200% 100%; animation:__slide 2s linear infinite;
      }
      @keyframes __slide { 0%{background-position:0 0} 100%{background-position:200% 0} }
    `;
    document.head.appendChild(style);

    el = document.createElement('div');
    el.id = '__loading_overlay__';
    el.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar" role="progressbar" aria-hidden="true"></div>
    `;
    document.body.appendChild(el);
    return el;
  }

  function start() {
    // 1) ローダーDOM
    const loader = createOverlayIfMissing(); // #loading-screen 優先、無ければ作る

    // 2) 初期は表示（HTML/loader.css が担当）。JSでも再保証しておく
    hardShow(loader);

    // 3) “絶対に消す”多重セーフ
    const tryHide = () => hardHide(loader);

    // 3-1) ページ完全読込で閉じる
    window.addEventListener('load', () => {
      // 読み終わってすぐ消すと「最後だけチラッ」になる環境があるので少し余韻
      setTimeout(tryHide, 250);
    }, { once: true, passive: true });

    // 3-2) 既にcompleteなら即クローズ（defer/asyncで遅れて実行されたとき）
    if (document.readyState === 'complete') {
      setTimeout(tryHide, 0);
    } else {
      // 3-3) 念のため DOMContentLoaded 後にも監視（環境依存の取りこぼし対策）
      document.addEventListener('DOMContentLoaded', () => {
        // ネットワークが速い環境で load が遅れても 1.2s で畳む
        setTimeout(() => {
          if (document.readyState === 'complete') tryHide();
        }, 1200);
      }, { once: true });
    }

    // 3-4) ページがフォアグラウンドに戻った時、既に読み終わっているなら消す
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && document.readyState === 'complete') tryHide();
    }, { passive: true });

    // 3-5) bfcache 復帰時はローダー不要
    window.addEventListener('pageshow', (e) => {
      if (e.persisted) tryHide();
    }, { passive: true });

    // 3-6) JS エラー/未処理例外でも UI を見せるため強制クローズ
    window.addEventListener('error', () => tryHide());
    window.addEventListener('unhandledrejection', () => tryHide());

    // 3-7) 最終フェイルセーフ：何があっても最大 6 秒で畳む
    setTimeout(tryHide, 6000);

    // 4) 遷移時は素早く見せる（押下の瞬間）
    const isValidLink = (a, e) =>
      a && isValidHref(a.getAttribute('href')) &&
      a.target !== '_blank' && !a.hasAttribute('download') &&
      !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && e.button === 0;

    const pointerDownHandler = (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (isValidLink(a, e)) hardShow(loader);

      const submitBtn = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');
      if (submitBtn) {
        const form = submitBtn.form || submitBtn.closest('form');
        if (form && form.target !== '_blank') hardShow(loader);
      }
    };
    document.addEventListener('pointerdown', pointerDownHandler, { capture: true, passive: true });
    document.addEventListener('touchstart', pointerDownHandler, { capture: true, passive: true });

    // 5) ページ離脱開始で確実に表示（Safari 対策）
    window.addEventListener('beforeunload', () => hardShow(loader), { passive: true });

    // 6) 外部からも使えるように公開（任意）
    window.PageLoader = {
      show: () => hardShow(loader),
      hide: () => hardHide(loader),
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();