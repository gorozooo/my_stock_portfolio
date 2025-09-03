// js/loader.js

(function(){
  const loader = document.getElementById('loading-screen');
  if (!loader) return;

  const textEl = loader.querySelector('.loading-text');
  // 念のため、data-text と表示文字を同期（翻訳・置換時の保険）
  if (textEl && !textEl.getAttribute('data-text')) {
    textEl.setAttribute('data-text', textEl.textContent.trim());
  }

  // 外向けAPI（base.jsなど他のJSから呼べるように）
  window.PageLoader = {
    show() {
      loader.classList.remove('hidden');
      // 即表示を保証
      loader.style.opacity = '1';
      loader.style.visibility = 'visible';
    },
    hide() {
      loader.classList.add('hidden');
    }
  };

  // 初期状態：表示（CSSがheadで読まれていれば最初からネオンON）
  // ※ ここでは明示的に show しない。HTMLの初期DOMで表示されている想定

  // ページ読み込み完了→少し待ってフェードアウト
  window.addEventListener('load', () => {
    // 「最後に一瞬だけ見える」を防ぐため短めに
    setTimeout(() => PageLoader.hide(), 300);
  });

  // Safariのリロード/離脱時にも確実に表示
  window.addEventListener('beforeunload', () => {
    PageLoader.show();
  });

  // bfcache復帰時（戻る/進む）はローダー不要
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) PageLoader.hide();
  });
})();