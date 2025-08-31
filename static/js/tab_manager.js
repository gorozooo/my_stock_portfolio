document.addEventListener("DOMContentLoaded", () => {

  // -------------------- サブメニュー折りたたみ --------------------
  document.querySelectorAll(".toggle-submenu").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      tabCard.classList.toggle("expanded");
    });
  });

  // -------------------- タブドラッグ & ドロップ --------------------
  const tabList = document.getElementById("tab-list");
  Sortable.create(tabList, {
    animation: 150,
    handle: ".tab-header",
    ghostClass: "dragging",
    onEnd: function(evt) {
      const order = Array.from(tabList.children).map(c => c.dataset.id);
      console.log("新しいタブ順序:", order);
      // TODO: Ajaxで順序をDBに保存
    }
  });

  // -------------------- サブメニュードラッグ --------------------
  document.querySelectorAll(".submenu-list").forEach(list => {
    Sortable.create(list, {
      animation: 150,
      handle: ".submenu-item",
      ghostClass: "dragging",
      onEnd: function(evt) {
        const order = Array.from(list.children)
          .filter(c => c.dataset.id)
          .map(c => c.dataset.id);
        console.log("サブメニュー順序更新:", order);
        // TODO: AjaxでDBに保存
      }
    });
  });

  // -------------------- タブ編集・削除 --------------------
  document.querySelectorAll(".edit-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      alert("タブ編集: " + tabCard.dataset.id);
      // TODO: モーダル or インライン編集
    });
  });

  document.querySelectorAll(".delete-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      if(confirm("タブを削除しますか？")) {
        tabCard.remove();
        // TODO: AjaxでDB削除
      }
    });
  });

  // -------------------- サブメニュー編集・削除 --------------------
  document.querySelectorAll(".edit-sub-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const subItem = btn.closest(".submenu-item");
      alert("サブメニュー編集: " + subItem.dataset.id);
    });
  });

  document.querySelectorAll(".delete-sub-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const subItem = btn.closest(".submenu-item");
      if(confirm("サブメニューを削除しますか？")) {
        subItem.remove();
        // TODO: AjaxでDB削除
      }
    });
  });

  // -------------------- 新規タブ追加 --------------------
  document.getElementById("add-tab-fab").addEventListener("click", () => {
    alert("新規タブ追加");
    // TODO: モーダル表示 or Ajax追加
  });

  // -------------------- 新規サブメニュー追加 --------------------
  document.querySelectorAll(".add-submenu-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      alert("サブメニュー追加: " + tabCard.dataset.id);
      // TODO: Ajax追加
    });
  });

});