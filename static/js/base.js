// static/js/base.js
// Loader only（下タブ/サブメニューは触りません）
// ・初回は“必ず表示”→ 完全読込でフェードアウト
// ・リンク/フォーム操作の“押した瞬間”に即表示
// ・CSS競合があっても !important で強制反映
// ・bfcache復帰やエラー時、フェイルセーフも網羅

(function () {
  // ---- utilities ----
  function forceShow(el) {
    if (!el) return;
    el.classList.remove('hidden'); // loader.css の hidden を解除
    el.style.setProperty('display', 'flex', 'important');
    el.style.setProperty('opacity', '1', 'important');
    el.style.setProperty('visibility', 'visible', 'important');
    el.style.setProperty('pointer-events', 'auto', 'important');
    el.style.setProperty('z-index', '2147483647', 'important');
    document.documentElement.style.cursor = 'wait';
    document.body.style.cursor = 'wait';
  }
  function forceHide(el) {
    if (!el) return;
    el.classList.add('hidden'); // CSS のトランジションを活かす
    el.style.setProperty('opacity', '0', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.style.setProperty('pointer-events', 'none', 'important');
    setTimeout(() => {
      el.style.setProperty('display', 'none', 'important');
      document.documentElement.style.cursor = '';
      document.body.style.cursor = '';
    }, 350);
  }
  function ensureLoaderHost() {
    let host = document.getElementById('loading-screen');
    if (host) return host;

    // 無い場合は簡易オーバーレイを作る（見た目はシンプルだが挙動は同じ）
    const style = document.createElement('style');
    style.textContent = `
      #__loading_overlay__{
        position:fixed; inset:0; z-index:2147483647!important;
        background:rgba(10,10,20,.95);
        display:flex; align-items:center; justify-content:center; flex-direction:column;
        opacity:1; visibility:visible; pointer-events:auto;
        transition:opacity .35s ease, visibility .35s ease;
      }
      #__loading_overlay__.hidden{ opacity:0; visibility:hidden; pointer-events:none; }
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

    host = document.createElement('div');
    host.id = '__loading_overlay__';
    host.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar" role="progressbar" aria-hidden="true"></div>
    `;
    document.body.appendChild(host);
    return host;
  }
  function isValidNavAnchor(a, e) {
    if (!a) return false;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
    if (a.target === '_blank' || a.hasAttribute('download')) return false;
    if (a.dataset.noLoader === 'true') return false;
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)) return false;
    return true;
  }

  function start() {
    const loader = ensureLoaderHost();

    // 1) 初回“必ず表示”
    //    - HTML/CSS 側で何かに上書きされていてもここで可視化
    forceShow(loader);

    // 2) 完全読込でフェードアウト（“最後だけチラ見え”防止のため少し余韻）
    const closeAfterLoad = () => setTimeout(() => forceHide(loader), 250);
    if (document.readyState === 'complete') {
      // 既に読み終わっているケース（defer/asyncなど）
      closeAfterLoad();
    } else {
      window.addEventListener('load', closeAfterLoad, { once: true, passive: true });
    }

    // 3) フェイルセーフ群
    //    3-1) DOMContentLoaded 後も 1.2s 経過で readyState=complete なら念押しクローズ
    document.addEventListener('DOMContentLoaded', () => {
      setTimeout(() => {
        if (document.readyState === 'complete') forceHide(loader);
      }, 1200);
    }, { once: true });
    //    3-2) 可視化復帰時に complete ならクローズ
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && document.readyState === 'complete') forceHide(loader);
    }, { passive: true });
    //    3-3) bfcache 復帰でクローズ
    window.addEventListener('pageshow', (e) => { if (e.persisted) forceHide(loader); }, { passive: true });
    //    3-4) エラーでも UI を塞がない
    window.addEventListener('error', () => forceHide(loader));
    window.addEventListener('unhandledrejection', () => forceHide(loader));
    //    3-5) 最終保険：最大 6 秒で畳む
    setTimeout(() => forceHide(loader), 6000);

    // 4) “押した瞬間”表示（ナビ前に確実表示）
    const onPointerDown = (e) => {
      const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if (isValidNavAnchor(a, e)) { forceShow(loader); return; }

      const submit = e.target && e.target.closest
        ? e.target.closest('button[type="submit"], input[type="submit"]') : null;
      if (submit) {
        const form = submit.form || submit.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          forceShow(loader);
        }
      }
    };
    document.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true });
    document.addEventListener('touchstart', onPointerDown, { capture: true, passive: true });

    // 5) 離脱開始時も確実に表示（Safari 対策）
    window.addEventListener('beforeunload', () => forceShow(loader), { passive: true });

    // 6) 外部からも使える API（任意）
    window.PageLoader = { show: () => forceShow(loader), hide: () => forceHide(loader) };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();