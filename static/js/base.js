// static/js/base.js
// Loader + 下タブ/サブメニュー（まとめ）
//
// 変更点（重要）
// - 初期表示では loader を “表示しない”（即座に隠す）← 永続表示の原因を排除
// - クリック／フォーム送信の「押した瞬間」にだけ表示
// - ページ到達/復帰/エラー時は自動的に隠すフェイルセーフ
// - 下タブ/サブメニューの表示制御は従来のまま（遷移だけ loader 経由）
//
// 期待される挙動
// 1) 初回ロード時：ローディングは出さない（最後にチラッ…もしない）
// 2) 任意のリンクや送信を押した瞬間にローディングが出る → 遷移完了で消える
// 3) 下タブ/サブメニュー：今まで通り開閉・遷移（ナビは loader 経由）
// ---------------------------------------------------------------------------

(function () {
  // ===== util =====
  function hardHide(el) {
    if (!el) return;
    el.classList.add('hidden'); // loader.css の非表示クラス
    el.style.setProperty('opacity', '0', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.style.setProperty('pointer-events', 'none', 'important');
    el.style.setProperty('display', 'none', 'important');
    document.documentElement.style.cursor = '';
    document.body.style.cursor = '';
  }
  function hardShow(el) {
    if (!el) return;
    el.classList.remove('hidden');
    el.style.setProperty('display', 'flex', 'important');
    // reflow 確保
    // eslint-disable-next-line no-unused-expressions
    el.offsetHeight;
    el.style.setProperty('opacity', '1', 'important');
    el.style.setProperty('visibility', 'visible', 'important');
    el.style.setProperty('pointer-events', 'auto', 'important');
    el.style.setProperty('z-index', '2147483647', 'important');
    document.documentElement.style.cursor = 'wait';
    document.body.style.cursor = 'wait';
  }
  function getLoaderHost() {
    return document.getElementById('loading-screen');
  }

  function isValidAnchor(a, e) {
    if (!a) return false;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
    if (a.target === '_blank' || a.hasAttribute('download')) return false;
    if (a.dataset.noLoader === 'true') return false;
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)) return false;
    return true;
  }

  // ===== Loader =====
  function initLoader() {
    const loader = getLoaderHost();
    if (!loader) return;

    // 0) 初期は “必ず隠す” ーー 以前の「最初から表示」が永続化の原因だったため
    //    （テンプレが display:flex でも即座に打ち消す）
    hardHide(loader);

    // 1) 押した瞬間にだけ表示
    const onPointerDown = (e) => {
      const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if (isValidAnchor(a, e)) {
        hardShow(loader);
        return;
      }
      const submit = e.target && e.target.closest
        ? e.target.closest('button[type="submit"], input[type="submit"]')
        : null;
      if (submit) {
        const form = submit.form || submit.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          hardShow(loader);
        }
      }
    };
    document.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true });
    document.addEventListener('touchstart', onPointerDown, { capture: true, passive: true });

    // 2) 離脱開始時にも表示（Safari 対策）
    window.addEventListener('beforeunload', () => hardShow(loader), { passive: true });

    // 3) 到着/復帰/エラー時は確実に閉じる
    const safeHide = () => hardHide(loader);
    if (document.readyState === 'complete') {
      // 既に読み切っている場合も即閉じる
      safeHide();
    } else {
      window.addEventListener('load', () => setTimeout(safeHide, 50), { once: true, passive: true });
    }
    window.addEventListener('pageshow', (e) => { if (e.persisted) safeHide(); }, { passive: true });
    window.addEventListener('error', safeHide);
    window.addEventListener('unhandledrejection', safeHide);

    // 4) 外向け API
    window.PageLoader = {
      show: () => hardShow(loader),
      hide: () => hardHide(loader),
    };
  }

  // ===== 下タブ / サブメニュー =====
  // ※ ロジックは従来のまま。表示制御のみで、遷移時に PageLoader.show() を経由。
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    // ケアレット列（タブ直下）
    let caretRow = document.querySelector('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

    // サブメニューのアクションバー
    let actionbar = document.querySelector('.tab-actionbar');
    if (!actionbar) {
      actionbar = document.createElement('div');
      actionbar.className = 'tab-actionbar';
      document.body.appendChild(actionbar);
    }

    let openKey = null;
    const map = new Map();

    function rebuild() {
      caretRow.innerHTML = '';
      map.clear();

      const tabs = Array.from(tabBar.querySelectorAll('.tab-item'));
      let seq = 0;

      tabs.forEach((tab) => {
        let key = tab.dataset.tabkey;
        if (!key) {
          key = `t${Date.now().toString(36)}_${(seq++).toString(36)}`;
          tab.dataset.tabkey = key;
        }

        // 既存の装飾的ケアレットは除去（押せるボタンは別で作る前提）
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(n => n.remove());

        const link = tab.querySelector('.tab-link');
        const submenu = tab.querySelector('.sub-menu');

        const cell = document.createElement('div');
        cell.className = 'caret-cell';

        let caretBtn = null;
        if (submenu) {
          caretBtn = document.createElement('button');
          caretBtn.type = 'button';
          caretBtn.className = 'caret-btn';
          caretBtn.textContent = '▾';
          caretBtn.setAttribute('aria-expanded', 'false');
          caretBtn.dataset.tabkey = key;
          cell.appendChild(caretBtn);
        } else {
          const ph = document.createElement('div');
          ph.className = 'caret-placeholder';
          cell.appendChild(ph);
        }
        caretRow.appendChild(cell);

        map.set(key, { tab, link, submenu, caretBtn });
      });

      // ケアレット開閉
      map.forEach(({ caretBtn }, key) => {
        if (!caretBtn) return;
        caretBtn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (openKey === key) hideBar();
          else showBar(key);
        };
      });

      // タブ本体のクリック → ローダー経由で遷移（下タブの通常動作は保持）
      map.forEach(({ link }) => {
        if (!link) return;
        link.addEventListener('click', (e) => {
          const href = link.getAttribute('href');
          const target = link.getAttribute('target') || '';
          if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
          e.preventDefault();
          window.PageLoader?.show();
          window.location.href = href;
        });
      });

      if (openKey && !map.has(openKey)) hideBar();
    }

    function showBar(key) {
      const rec = map.get(key);
      if (!rec || !rec.submenu) return;

      actionbar.innerHTML = '';
      const links = rec.submenu.querySelectorAll('a');

      if (!links.length) {
        const none = document.createElement('span');
        none.className = 'ab-btn';
        none.textContent = 'メニューなし';
        actionbar.appendChild(none);
      } else {
        links.forEach((a) => {
          const href = a.getAttribute('href') || '#';
          const label = (a.textContent || '').trim();
          const target = a.getAttribute('target') || '';
          const btn = document.createElement('a');
          btn.className = 'ab-btn';
          btn.href = href;
          btn.textContent = label;
          btn.addEventListener('click', (e) => {
            if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
            e.preventDefault();
            window.PageLoader?.show();
            window.location.href = href;
          });
          actionbar.appendChild(btn);
        });
      }

      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      if (rec.caretBtn) rec.caretBtn.setAttribute('aria-expanded', 'true');

      actionbar.style.display = 'flex';
      requestAnimationFrame(() => actionbar.classList.add('show'));
      openKey = key;
    }

    function hideBar() {
      actionbar.classList.remove('show');
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      setTimeout(() => {
        if (!actionbar.classList.contains('show')) actionbar.style.display = 'none';
      }, 160);
      openKey = null;
    }

    // 外側クリック/ESC/リサイズで閉じる
    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar  = !!e.target.closest('.tab-actionbar');
      const inRow  = !!e.target.closest('.caret-row');
      const inTabs = !!e.target.closest('.bottom-tab');
      if (!inBar && !inRow && !inTabs) hideBar();
    }, { passive: true });

    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    new MutationObserver(() => rebuild()).observe(tabBar, { childList: true, subtree: true });
    rebuild();
  }

  // ===== boot =====
  function start() {
    initLoader(); // ← 初回は隠す／クリック時にだけ表示
    initTabs();   // ← 下タブ/サブメニューも含める
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();