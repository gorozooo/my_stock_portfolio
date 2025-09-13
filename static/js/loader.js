// static/js/loader.js
// ローダー一元管理：PageLoader.show()/hide() 提供 + 自動フック（load / beforeunload / instant show）

(function(){
  const loader = document.getElementById('loading-screen');
  if (!loader) return;

  // 表示文言の data-text を同期（保険）
  const textEl = loader.querySelector('.loading-text');
  if (textEl && !textEl.getAttribute('data-text')) {
    textEl.setAttribute('data-text', textEl.textContent.trim());
  }

  // 外向けAPI
  window.PageLoader = {
    show() {
      loader.classList.remove('hidden'); // ← CSSで opacity/visibility を切り替え
      // 念のため即時確定（他CSS競合を抑える）
      loader.style.opacity = '1';
      loader.style.visibility = 'visible';
    },
    hide() {
      loader.classList.add('hidden');
    }
  };

  // ---- 初期状態：**表示**（前の挙動）
  // HTMLでは #loading-screen に .hidden がついていない想定
  // → すでに見えているので何もしない

  // ---- ページ読み込み完了 → 少し待ってフェードアウト
  window.addEventListener('load', () => {
    setTimeout(() => PageLoader.hide(), 300);
  }, { passive: true });

  // ---- 離脱で必ず表示（Safari含む）
  window.addEventListener('beforeunload', () => {
    PageLoader.show();
  }, { passive: true });

  // ---- bfcache 復帰（戻る/進む）はローダー不要
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) PageLoader.hide();
  }, { passive: true });

  // =========================================
  // ここから「押した瞬間に出す」フック（アンカー/フォーム）
  // =========================================

  const isModClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
  const isValidHref = (href) => href && !href.startsWith('#') && !href.startsWith('javascript:');

  let fallbackTimer = null;
  function armFallback(){
    clearTimeout(fallbackTimer);
    // 3秒以内に beforeunload が来なければ自動で隠す（遷移無しケースの保険）
    fallbackTimer = setTimeout(() => PageLoader.hide(), 3000);
  }
  function clearFallback(){ clearTimeout(fallbackTimer); fallbackTimer = null; }
  window.addEventListener('beforeunload', clearFallback, { passive: true });

  // pointerdown / touchstart で最速表示
  const downHandler = (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    const submitBtn = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');

    if (a) {
      const href   = a.getAttribute('href') || '';
      const target = a.getAttribute('target') || '';
      const dl     = a.hasAttribute('download');
      if (isValidHref(href) && target !== '_blank' && !dl && a.dataset.noLoader !== 'true' && !isModClick(e)) {
        PageLoader.show();
        armFallback();
      }
    } else if (submitBtn) {
      const form = submitBtn.form || submitBtn.closest('form');
      if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
        PageLoader.show();
        armFallback();
      }
    }
  };
  document.addEventListener('pointerdown', downHandler, { capture: true, passive: true });
  document.addEventListener('touchstart', downHandler, { capture: true, passive: true });

  // クリック時は自前遷移でローダー維持（他で prevent されてなければ）
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    if (e.defaultPrevented) return;

    const href   = a.getAttribute('href') || '';
    const target = a.getAttribute('target') || '';
    const dl     = a.hasAttribute('download');

    if (!isValidHref(href) || target === '_blank' || dl || a.dataset.noLoader === 'true' || isModClick(e)) return;

    e.preventDefault();
    PageLoader.show();        // 念のため再度 show
    clearFallback();
    setTimeout(() => { window.location.href = href; }, 0);
  }, { capture: true, passive: false });

  // フォーム送信も確実に show
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.target === '_blank' || form.dataset.noLoader === 'true') return;
    PageLoader.show();
    clearFallback();
    // 送信自体はブラウザに任せる
  }, { capture: true });

})();