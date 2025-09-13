// js/loader.js
// ローディング専用。即時表示（pointerdown）+ 遷移完了まで維持。
// 下タブ/サブメニュー用のコードは base.js 側に維持したまま、ここは Loader のみ。

(function () {
  const loader = document.getElementById('loading-screen');
  if (!loader) return;

  // 表示中はカーソルを待機にしてフィードバック強化
  function setWaitCursor(on) {
    document.documentElement.style.cursor = on ? 'wait' : '';
    document.body.style.cursor = on ? 'wait' : '';
  }

  // 強制表示（CSSを確実に上書き）
  function showNow(cb) {
    loader.classList.remove('hidden');
    loader.style.removeProperty('display'); // CSS に任せる（display:flex）
    // reflow でフレーム確保
    // eslint-disable-next-line no-unused-expressions
    loader.offsetHeight;
    loader.style.opacity = '1';
    loader.style.visibility = 'visible';
    setWaitCursor(true);
    if (typeof cb === 'function') cb();
  }

  // 強制非表示（.hidden で即座に display:none へ）
  function hideNow() {
    loader.classList.add('hidden');
    setWaitCursor(false);
  }

  // 外向けAPI（base.js等から呼べる）
  window.PageLoader = {
    show: showNow,
    hide: hideNow
  };

  // 1) 初期：必ず表示（以前の挙動）
  //   HTML/CSS で既に表示状態なので、ここでは何もしない。
  //   ※ もし別JSで早期に隠されていたら、下の 'load' で再度隠すのでOK。

  // 2) ページ完全ロードでフェードアウト
  window.addEventListener('load', () => {
    // フェードの残像を抑えるため少しだけ遅らせる
    setTimeout(hideNow, 220);
  }, { passive: true });

  // 3) Safari等の離脱時にも確実に表示
  window.addEventListener('beforeunload', () => {
    showNow();
  }, { passive: true });

  // 4) bfcache 復帰（戻る/進む）はローダー不要
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) hideNow();
  }, { passive: true });

  // 5) 押下の瞬間に表示（リンク/フォーム）
  function isModClick(e){ return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0; }
  function isValidHref(href){ return href && !href.startsWith('#') && !href.startsWith('javascript:'); }

  // pointerdown/touchstart で“最速”表示（視覚ストレス軽減）
  const downHandler = (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (a) {
      const href = a.getAttribute('href');
      if (isValidHref(href) && a.target !== '_blank' && !a.hasAttribute('download') && !isModClick(e) && a.dataset.noLoader !== 'true') {
        showNow();
        return;
      }
    }
    const submitBtn = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');
    if (submitBtn) {
      const form = submitBtn.form || submitBtn.closest('form');
      if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
        showNow();
      }
    }
  };
  document.addEventListener('pointerdown', downHandler, { capture: true, passive: true });
  document.addEventListener('touchstart', downHandler, { capture: true, passive: true });

  // click では遷移を自前実行してローダーを維持
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;

    const href = a.getAttribute('href');
    if (!isValidHref(href)) return;
    if (a.target === '_blank' || a.hasAttribute('download') || a.dataset.noLoader === 'true') return;
    if (isModClick(e)) return;

    e.preventDefault();
    showNow(() => { window.location.href = href; });
  }, { capture: true });

  // form submit でも出しっぱなしにして遷移を待つ
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.target === '_blank' || form.dataset.noLoader === 'true') return;
    showNow();
  }, { capture: true });

  // 念のため、ローダーテキストの data-text を同期
  const textEl = loader.querySelector('.loading-text');
  if (textEl && !textEl.getAttribute('data-text')) {
    textEl.setAttribute('data-text', textEl.textContent.trim());
  }
})();