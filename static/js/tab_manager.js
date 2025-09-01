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
      // Ajaxで順序をDBに保存
      fetch("/save_tab_order/", {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ order: order })
      });
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
        // AjaxでDB保存
        fetch("/save_submenu_order/", {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() },
          body: JSON.stringify({ order: order })
        });
      }
    });
  });

  // -------------------- タブ編集 --------------------
  function openTabModal(tabCard) {
    const id = tabCard.dataset.id;
    const name = tabCard.querySelector(".tab-name").innerText;
    const icon = tabCard.querySelector(".tab-icon").innerText;
    const url_name = tabCard.dataset.url || "";

    document.getElementById("modal-title").innerText = id ? "タブ編集" : "新規タブ追加";
    document.getElementById("tab-id").value = id || "";
    document.getElementById("tab-name").value = name || "";
    document.getElementById("tab-icon").value = icon || "";
    document.getElementById("tab-url").value = url_name || "";

    openModal(tabModal);
  }

  document.querySelectorAll(".edit-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      openTabModal(tabCard);
    });
  });

  document.getElementById("add-tab-fab").addEventListener("click", () => {
    openTabModal({ dataset: {} });
  });

  // -------------------- タブ削除 --------------------
  document.querySelectorAll(".delete-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      if(confirm("タブを削除しますか？")) {
        fetch(`/delete_tab/${tabCard.dataset.id}/`, {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() }
        }).then(res => {
          if(res.ok) tabCard.remove();
        });
      }
    });
  });

  // -------------------- サブメニュー編集 --------------------
  function openSubmenuModal(subItem, tabId) {
    document.getElementById("submenu-modal-title").innerText = subItem ? "サブメニュー編集" : "サブメニュー追加";
    document.getElementById("submenu-tab-id").value = tabId;
    document.getElementById("submenu-id").value = subItem ? subItem.dataset.id : "";
    document.getElementById("submenu-name").value = subItem ? subItem.querySelector("span").innerText : "";
    document.getElementById("submenu-url").value = subItem ? subItem.dataset.url : "";
    openModal(submenuModal);
  }

  document.querySelectorAll(".edit-sub-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const subItem = btn.closest(".submenu-item");
      const tabCard = subItem.closest(".tab-card");
      openSubmenuModal(subItem, tabCard.dataset.id);
    });
  });

  document.querySelectorAll(".add-submenu-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      openSubmenuModal(null, tabCard.dataset.id);
    });
  });

  // -------------------- サブメニュー削除 --------------------
  document.querySelectorAll(".delete-sub-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const subItem = btn.closest(".submenu-item");
      if(confirm("サブメニューを削除しますか？")) {
        fetch(`/delete_submenu/${subItem.dataset.id}/`, {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() }
        }).then(res => {
          if(res.ok) subItem.remove();
        });
      }
    });
  });

  // -------------------- タブ保存フォーム --------------------
  tabForm.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(tabForm);
    const id = formData.get("tab_id");

    fetch(id ? `/update_tab/${id}/` : "/create_tab/", {
      method: "POST",
      headers: { "X-CSRFToken": getCSRFToken() },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if(data.success){
        if(id){
          // 編集の場合
          const tabCard = document.querySelector(`.tab-card[data-id='${id}']`);
          tabCard.querySelector(".tab-name").innerText = data.tab.name;
          tabCard.querySelector(".tab-icon").innerText = data.tab.icon || "📑";
          tabCard.dataset.url = data.tab.url_name || "";
        } else {
          // 新規追加
          tabList.insertAdjacentHTML("beforeend", data.html); // data.htmlはサーバーで生成されたタブカードHTML
        }
      }
      closeModal(tabModal);
    });
  });

  // -------------------- サブメニュー保存フォーム --------------------
  submenuForm.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(submenuForm);
    const id = formData.get("submenu_id");
    const tabId = formData.get("tab_id");

    fetch(id ? `/update_submenu/${id}/` : "/create_submenu/", {
      method: "POST",
      headers: { "X-CSRFToken": getCSRFToken() },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if(data.success){
        const tabCard = document.querySelector(`.tab-card[data-id='${tabId}']`);
        if(id){
          // 編集
          const subItem = tabCard.querySelector(`.submenu-item[data-id='${id}'] span`);
          subItem.innerText = data.submenu.name;
        } else {
          // 新規追加
          const submenuList = tabCard.querySelector(".submenu-list");
          submenuList.insertAdjacentHTML("beforeend", data.html); // data.htmlはサーバーで生成されたサブメニューHTML
        }
      }
      closeModal(submenuModal);
    });
  });

  // -------------------- CSRF取得 --------------------
  function getCSRFToken(){
    return document.querySelector("[name=csrfmiddlewaretoken]").value;
  }

});