// /static/js/realized_close.js
(function () {
  const submenu = document.getElementById("submenu");
  const mask    = document.querySelector(".btm-mask");

  if (!submenu || !mask) return; // ボトムタブの土台が無い場合は何もしない

  // ---- 便利: CSRF ----
  function getCookie(name){
    const m = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]+)'));
    return m ? decodeURIComponent(m[2]) : "";
  }
  const CSRF = getCookie('csrftoken');

  // ---- 共通: シート表示/非表示（bottom_tab.js と同じクラスで制御） ----
  function showSheet(html){
    submenu.innerHTML = html;
    mask.classList.add("show");
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden", "false");
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
  }
  function hideSheet(){
    mask.classList.remove("show");
    submenu.classList.remove("dragging");
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden", "true");
    submenu.style.transform = "";
    document.documentElement.style.overflow = "";
    document.body.style.overflow = "";
  }
  mask.addEventListener("click", hideSheet);

  // ---- 1) 売却ボタンを拾ってシートHTMLをロード ----
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".js-close-btn");
    if (!btn) return;

    const url = btn.getAttribute("data-close-url");
    if (!url) return;

    try {
      const res = await fetch(url, { credentials: "same-origin" });
      const data = await res.json();
      if (data.ok && data.sheet){
        showSheet(data.sheet);
      }
    } catch (err) {
      console.error(err);
      alert("シートの取得に失敗しました。");
    }
  });

  // ---- 2) シート内の送信をハンドル（フォームは部分テンプレ _close_sheet.html 内）----
  submenu.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.matches(".js-close-submit-form")) return; // フォームにこのクラスを付けておく

    e.preventDefault();
    const action = form.getAttribute("action");
    const fd = new FormData(form);

    try {
      const res = await fetch(action, {
        method: "POST",
        body: fd,
        headers: { "X-CSRFToken": CSRF },
        credentials: "same-origin",
      });
      const data = await res.json();
      if (!data.ok){
        alert(data.error || "保存に失敗しました");
        return;
      }

      // 返ってきた断片で差し替え（ID はテンプレと合わせる）
      const summaryEl = document.getElementById("realizedSummary");
      const tableEl   = document.getElementById("realizedTable");
      if (summaryEl && data.summary) summaryEl.innerHTML = data.summary;
      if (tableEl   && data.table)   tableEl.innerHTML   = data.table;

      // 保有リストも返ってきていれば更新（無ければスキップ）
      if (data.holdings){
        const holdingsEl = document.getElementById("holdingsList");
        if (holdingsEl) holdingsEl.innerHTML = data.holdings;
      }

      hideSheet();

      // ちいさなトースト（bottom_tab.js のトーストがあれば利用）
      const toast = document.getElementById("btmToast");
      if (toast){
        toast.textContent = "売却を登録しました";
        toast.style.opacity = "1";
        toast.style.transform = "translate(-50%,0)";
        setTimeout(()=>{
          toast.style.opacity = "0";
          toast.style.transform = "translate(-50%,24px)";
        }, 1100);
      }
    } catch (err) {
      console.error(err);
      alert("通信に失敗しました。");
    }
  });

  // ---- 3) シート内の「キャンセル」ボタン（data-dismiss="sheet" を付ける）----
  submenu.addEventListener("click", (e) => {
    if (e.target.closest('[data-dismiss="sheet"]')) hideSheet();
  });
})();

// static/js/realized_close.js
// HTMXで差し込まれても確実に動くよう、イベント委譲で実装。

(function () {
  // ±ボタンクリックで cashflow の符号を切替
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('#sheetRoot .sign-btn');
    if (!btn) return;

    e.preventDefault();

    const root = btn.closest('#closeSheet') || document.querySelector('#sheetRoot #closeSheet');
    if (!root) return;

    const input = root.querySelector('input[name="cashflow"]');
    if (!input) return;

    const wantMinus = btn.dataset.sgn === '-';

    let v = (input.value || '')
      .replace(/[＋+]/g, '+')
      .replace(/[−–—]/g, '-')
      .replace(/，/g, ',')
      .replace(',', '.')
      .trim();

    if (v === '' || v === '+' || v === '-') v = '0';
    let num = parseFloat(v);
    if (isNaN(num)) num = 0;

    num = Math.abs(num) * (wantMinus ? -1 : 1);
    input.value = String(num);

    // 画面更新用
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  });

  // 送信前に cashflow を正規化
  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!form || form.id !== 'closeForm') return;

    const cf = form.querySelector('input[name="cashflow"]');
    if (!cf) return;

    cf.value = (cf.value || '')
      .replace(/[＋+]/g, '+')
      .replace(/[−–—]/g, '-')
      .replace(/，/g, ',')
      .replace(',', '.')
      .trim();
  });

  // 念のため：HTMXで差し替え直後にフォーカスが飛ぶ問題を抑える
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'sheetRoot') {
      const cf = document.querySelector('#sheetRoot #closeSheet input[name="cashflow"]');
      if (cf) cf.blur();
    }
  });
})();
