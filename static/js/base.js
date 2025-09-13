// base.js — タブとサブメニューを data-tabkey で厳密にひも付け
document.addEventListener("DOMContentLoaded", () => {
  const tabBar = document.querySelector(".bottom-tab");
  if (!tabBar) return;

  // 共有UI
  const caretRow = document.createElement("div");
  caretRow.className = "caret-row";
  document.body.appendChild(caretRow);

  const actionbar = document.createElement("div");
  actionbar.className = "tab-actionbar";
  document.body.appendChild(actionbar);

  // 状態
  let openKey = null;
  let map = new Map(); // key -> {tab, link, submenu, caretBtn}

  // ===== ひも付け再構築（タブが増減/並び替えされてもOK） =====
  function rebuild() {
    // 既存ケアレット行を作り直し
    caretRow.innerHTML = "";
    map.clear();

    const tabs = Array.from(tabBar.querySelectorAll(".tab-item"));
    let seq = 0;

    tabs.forEach(tab => {
      // 安定キー：既に data-tabkey があれば再利用。無ければ採番
      let key = tab.dataset.tabkey;
      if (!key) {
        key = `t${Date.now().toString(36)}_${(seq++).toString(36)}`;
        tab.dataset.tabkey = key;
      }

      // 旧装飾ケアレットは排除
      tab.querySelectorAll(".tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove());

      const link = tab.querySelector(".tab-link");
      const submenu = tab.querySelector(".sub-menu");
      const cell = document.createElement("div");
      cell.className = "caret-cell";

      let caretBtn = null;
      if (submenu) {
        caretBtn = document.createElement("button");
        caretBtn.type = "button";
        caretBtn.className = "caret-btn";
        caretBtn.textContent = "▾";
        caretBtn.setAttribute("aria-expanded", "false");
        caretBtn.dataset.tabkey = key;
        cell.appendChild(caretBtn);
      } else {
        const ph = document.createElement("div");
        ph.className = "caret-placeholder";
        cell.appendChild(ph);
      }

      caretRow.appendChild(cell);
      map.set(key, { tab, link, submenu, caretBtn });
    });

    // ケアレットのリスナー（イベント委譲でもOKだが個別に）
    map.forEach(({ caretBtn }, key) => {
      if (!caretBtn) return;
      caretBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (openKey === key) hideBar();
        else showBar(key);
      };
    });

    // タブ本体＝通常遷移（サブメニューの有無に関係なく）
    map.forEach(({ link }) => {
      if (!link) return;
      link.onclick = (e) => {
        const href = link.getAttribute("href");
        const target = link.getAttribute("target") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
        e.preventDefault();
        window.location.href = href;
      };
    });

    // 開いてたものが消えていたら閉じる
    if (openKey && !map.has(openKey)) hideBar();
  }

  // ===== サブメニュー表示/非表示 =====
  function showBar(key) {
    const rec = map.get(key);
    if (!rec || !rec.submenu) return;

    // ボタン生成
    actionbar.innerHTML = "";
    const links = rec.submenu.querySelectorAll("a");
    if (links.length === 0) {
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      actionbar.appendChild(none);
    } else {
      links.forEach(a => {
        const href = a.getAttribute("href") || "#";
        const label = (a.textContent || "").trim();
        const target = a.getAttribute("target") || "";
        const btn = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = label;
        btn.onclick = (e) => {
          if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
          e.preventDefault();
          window.location.href = href;
        };
        actionbar.appendChild(btn);
      });
    }

    // ケアレット状態更新
    map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute("aria-expanded", "false"); });
    if (rec.caretBtn) rec.caretBtn.setAttribute("aria-expanded", "true");

    // 表示
    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));
    openKey = key;
  }

  function hideBar() {
    actionbar.classList.remove("show");
    map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute("aria-expanded", "false"); });
    setTimeout(() => { if (!actionbar.classList.contains("show")) actionbar.style.display = "none"; }, 160);
    openKey = null;
  }

  // ===== 外側クリック / Esc / リサイズで閉じる =====
  document.addEventListener("click", (e) => {
    if (!openKey) return;
    const inBar  = !!e.target.closest(".tab-actionbar");
    const inRow  = !!e.target.closest(".caret-row");
    const inTabs = !!e.target.closest(".bottom-tab");
    if (!inBar && !inRow && !inTabs) hideBar();
  });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && openKey) hideBar(); });
  window.addEventListener("resize", hideBar);

  // ===== タブの増減に自動追従（ページ差対策） =====
  const mo = new MutationObserver(() => rebuild());
  mo.observe(tabBar, { childList: true, subtree: true });

  // 初期構築
  rebuild();
});