document.addEventListener("DOMContentLoaded", () => {

  // -------------------- モーダル操作 --------------------
  const tabModal = document.getElementById("tab-modal");
  const submenuModal = document.getElementById("submenu-modal");
  const tabForm = document.getElementById("tab-form");
  const submenuForm = document.getElementById("submenu-form");

  const openModal = (modal) => modal.style.display = "block";
  const closeModal = (modal) => modal.style.display = "none";

  // モーダル閉じるボタン
  document.querySelectorAll(".modal .modal-close").forEach(btn => {
    btn.addEventListener("click", () => {
      closeModal(btn.closest(".modal"));
    });
  });

  // モーダル背景クリックで閉じる
  [tabModal, submenuModal].forEach(modal => {
    modal.addEventListener("click", e => {
      if(e.target === modal) closeModal(modal);
    });
  });

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

  // -------------------- タブ編集 --------------------
  document.querySelectorAll(".edit-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      const id = tabCard.dataset.id;
      const name = tabCard.querySelector(".tab-name").innerText;
      const icon = tabCard.querySelector(".tab-icon").innerText;
      const url_name = tabCard.dataset.url || ""; // data-url 属性あれば

      document.getElementById("modal-title").innerText = "タブ編集";
      document.getElementById("tab-id").value = id;
      document.getElementById("tab-name").value = name;
      document.getElementById("tab-icon").value = icon;
      document.getElementById("tab-url").value = url_name;

      openModal(tabModal);
    });
  });

  // -------------------- タブ削除 --------------------
  document.querySelectorAll(".delete-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      if(confirm("タブを削除しますか？")) {
        tabCard.remove();
        // TODO: AjaxでDB削除
      }
    });
  });

  // -------------------- サブメニュー編集 --------------------
  document.querySelectorAll(".edit-sub-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const subItem = btn.closest(".submenu-item");
      const tabCard = subItem.closest(".tab-card");
      const tabId = tabCard.dataset.id;
      const id = subItem.dataset.id;
      const name = subItem.querySelector("span").innerText;
      const url = subItem.dataset.url || "";

      document.getElementById("submenu-modal-title").innerText = "サブメニュー編集";
      document.getElementById("submenu-tab-id").value = tabId;
      document.getElementById("submenu-id").value = id;
      document.getElementById("submenu-name").value = name;
      document.getElementById("submenu-url").value = url;

      openModal(submenuModal);
    });
  });

  // -------------------- サブメニュー削除 --------------------
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
    document.getElementById("modal-title").innerText = "新規タブ追加";
    document.getElementById("tab-id").value = "";
    document.getElementById("tab-name").value = "";
    document.getElementById("tab-icon").value = "";
    document.getElementById("tab-url").value = "";
    openModal(tabModal);
  });

  // -------------------- 新規サブメニュー追加 --------------------
  document.querySelectorAll(".add-submenu-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      const tabId = tabCard.dataset.id;

      document.getElementById("submenu-modal-title").innerText = "サブメニュー追加";
      document.getElementById("submenu-tab-id").value = tabId;
      document.getElementById("submenu-id").value = "";
      document.getElementById("submenu-name").value = "";
      document.getElementById("submenu-url").value = "";

      openModal(submenuModal);
    });
  });

  // -------------------- タブ保存フォーム --------------------
  tabForm.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(tabForm);
    console.log("タブ送信データ:", Object.fromEntries(formData.entries()));
    closeModal(tabModal);
    // TODO: AjaxでDB保存
  });

  // -------------------- サブメニュー保存フォーム --------------------
  submenuForm.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(submenuForm);
    console.log("サブメニュー送信データ:", Object.fromEntries(formData.entries()));
    closeModal(submenuModal);
    // TODO: AjaxでDB保存
  });

});