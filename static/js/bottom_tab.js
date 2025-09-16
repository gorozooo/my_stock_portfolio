document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");

  // -------- ページ別メニュー定義 --------
  // label: 表示, action: 送出するアクション名, danger: 破壊操作を強調, href: 直接遷移も可
  const MENUS = {
    home: [
      { section: "クイック" },
      { label: "＋ 保有を追加", action: "add_holding" },
      { label: "実現損益を記録", action: "add_realized" },
      { label: "設定を開く", href: "/settings/trade" },
    ],
    holdings: [
      { section: "保有" },
      { label: "＋ 追加", action: "add_holding" },
      { label: "CSVエクスポート", action: "export_csv" },
      { label: "並び替え/フィルタ", action: "open_filter" },
      { section: "選択中" },
      { label: "売却（クローズ）", action: "close_position" },
      { label: "削除", action: "delete_holding", danger: true },
    ],
    trend: [
      { section: "トレンド" },
      { label: "この銘柄を監視に追加", action: "watch_symbol" },
      { label: "エントリー/ストップ計算", action: "calc_entry_stop" },
      { label: "共有リンクをコピー", action: "share_link" },
      { label: "チャート設定", action: "chart_settings" },
    ],
  };

  // -------- 共通ユーティリティ --------
  const LONG_PRESS_MS = 550;
  let pressTimer;

  function renderMenu(type, anchorBtn) {
    const items = MENUS[type] || [];
    submenu.innerHTML = ""; // reset

    items.forEach((it) => {
      if (it.section) {
        const sec = document.createElement("div");
        sec.className = "section";
        sec.textContent = it.section;
        submenu.appendChild(sec);
        return;
      }
      const btn = document.createElement("button");
      btn.className = "submenu-item" + (it.danger ? " danger" : "");
      btn.textContent = it.label;
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        hideMenu();
        if (it.href) {
          window.location.href = it.href;
          return;
        }
        // 各ページ側で拾えるカスタムイベントを投げる
        window.dispatchEvent(new CustomEvent("bottomtab:action", {
          detail: { menu: type, action: it.action }
        }));
      });
      submenu.appendChild(btn);
    });

    // 押されたボタンの真上にメニューのX位置を寄せる
    if (anchorBtn) {
      const r = anchorBtn.getBoundingClientRect();
      const center = r.left + r.width / 2;
      const vw = window.innerWidth;
      const left = Math.max(12, Math.min(center, vw - 12));
      submenu.style.left = `${left}px`;
      submenu.style.transform = "translateX(-50%)";
    }
  }

  function showMenu(type, anchorBtn) {
    renderMenu(type, anchorBtn);
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden", "false");
    if (navigator.vibrate) navigator.vibrate(8);
  }

  function hideMenu() {
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden", "true");
  }

  // -------- タブの挙動（タップで遷移 / 長押しでメニュー） --------
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const link = btn.dataset.link;
    const menuType = btn.dataset.menu;

    // 通常タップ：遷移
    btn.addEventListener("click", () => {
      if (!submenu.classList.contains("show") && link) {
        window.location.href = link;
      }
    });

    // 長押し（スマホ）
    btn.addEventListener("touchstart", () => {
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => showMenu(menuType, btn), LONG_PRESS_MS);
    }, { passive: true });
    btn.addEventListener("touchend", () => clearTimeout(pressTimer));

    // 右クリック（PC）
    btn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showMenu(menuType, btn);
    });
  });

  // 背景クリックで閉じる
  document.addEventListener("click", (e) => {
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) {
      hideMenu();
    }
  });

  // Escで閉じる
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideMenu();
  });
});