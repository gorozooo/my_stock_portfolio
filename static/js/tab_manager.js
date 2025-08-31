document.addEventListener("DOMContentLoaded", () => {

  // ----- サブメニュー折りたたみ -----
  document.querySelectorAll(".toggle-submenu").forEach(btn => {
    btn.addEventListener("click", e => {
      const tabCard = btn.closest(".tab-card");
      tabCard.classList.toggle("expanded");
    });
  });

  // ----- タブドラッグ & ドロップ -----
  const tabList = document.getElementById("tab-list");
  Sortable.create(tabList, {
    animation: 150,
    handle: ".tab-header",
    onEnd: function(evt) {
      console.log("新しいタブ順序:", Array.from(tabList.children).map(c => c.dataset.id));
      // Ajaxで保存可能
    }
  });

  // ----- サブメニュードラッグ -----
  document.querySelectorAll(".submenu-list").forEach(list => {
    Sortable.create(list, {
      animation: 150,
      handle: ".submenu-item",
      onEnd: function(evt) {
        console.log("サブメニュー順序更新:", Array.from(list.children).map(c => c.dataset.id));
        // Ajaxで保存
      }
    });
  });

  // ----- 編集／削除ボタン -----
  document.querySelectorAll(".edit-tab-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      const tabCard = btn.closest(".tab-card");
      alert("タブ編集: " + tabCard.dataset.id);
      // モーダル or inline編集処理
    });
  });

  document.querySelectorAll(".delete-tab-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      const tabCard = btn.closest(".tab-card");
      if(confirm("タブを削除しますか？")) {
        tabCard.remove();
        // AjaxでDB削除
      }
    });
  });

  document.querySelectorAll(".edit-sub-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      const subItem = btn.closest(".submenu-item");
      alert("サブメニュー編集: " + subItem.dataset.id);
    });
  });

  document.querySelectorAll(".delete-sub-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      const subItem = btn.closest(".submenu-item");
      if(confirm("サブメニューを削除しますか？")) {
        subItem.remove();
      }
    });
  });

  // ----- 新規タブ／サブメニュー追加 -----
  document.getElementById("add-tab-fab").addEventListener("click", () => {
    alert("新規タブ追加");
    // モーダル表示 or Ajax追加
  });

  document.querySelectorAll(".add-submenu-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      const tabCard = btn.closest(".tab-card");
      alert("サブメニュー追加: " + tabCard.dataset.id);
      // Ajax追加
    });
  });

});