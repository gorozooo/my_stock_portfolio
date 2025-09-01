document.addEventListener("DOMContentLoaded", () => {

  const tabModal = document.getElementById("tab-modal");
  const submenuModal = document.getElementById("submenu-modal");
  const tabForm = document.getElementById("tab-form");
  const submenuForm = document.getElementById("submenu-form");
  const tabList = document.getElementById("tab-list");
  const addTabFab = document.getElementById("add-tab-fab");

  const openModal = modal => modal.style.display = "block";
  const closeModal = modal => modal.style.display = "none";

  // モーダル閉じる
  document.querySelectorAll(".modal .modal-close").forEach(btn => {
    btn.addEventListener("click", () => closeModal(btn.closest(".modal")));
  });
  [tabModal, submenuModal].forEach(modal => {
    modal.addEventListener("click", e => { if(e.target === modal) closeModal(modal); });
  });

  function attachToggle(btn) {
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      tabCard.classList.toggle("expanded");
    });
  }

  // -------------------- タブ・サブメニュー作成 --------------------
  function createTabCardHTML(tab) {
    const div = document.createElement("div");
    div.className = "tab-card";
    div.dataset.id = tab.id;
    div.dataset.url = tab.url_name || "";
    div.innerHTML = `
      <div class="tab-header">
        <div class="tab-main">
          <span class="tab-icon">${tab.icon || "📑"}</span>
          <span class="tab-name">${tab.name || "（未設定）"}</span>
        </div>
        <div class="tab-actions">
          <button class="edit-tab-btn" title="編集">✏️</button>
          <button class="delete-tab-btn" title="削除">🗑️</button>
          <button class="toggle-submenu" title="サブメニュー切替">▼</button>
        </div>
      </div>
      <div class="submenu-list"></div>
      <button class="add-submenu-btn">＋ サブメニュー追加</button>
    `;
    attachTabEvents(div);
    return div;
  }

  function attachTabEvents(tabCard) {
    tabCard.querySelector(".edit-tab-btn").addEventListener("click", () => openTabModal(tabCard));
    tabCard.querySelector(".delete-tab-btn").addEventListener("click", () => { 
      if(confirm("タブを削除しますか？")) { 
        submitTabDelete(tabCard.dataset.id, tabCard);
      } 
    });
    attachToggle(tabCard.querySelector(".toggle-submenu"));
    tabCard.querySelector(".add-submenu-btn").addEventListener("click", () => openSubmenuModal(null, tabCard));
  }

  function createSubmenuHTML(sub) {
    const div = document.createElement("div");
    div.className = "submenu-item";
    div.dataset.id = sub.id;
    div.dataset.url = sub.url || "";
    div.innerHTML = `<span>${sub.name || "（未設定）"}</span>
      <div class="submenu-actions">
        <button class="edit-sub-btn" title="編集">✏️</button>
        <button class="delete-sub-btn" title="削除">🗑️</button>
      </div>`;
    attachSubmenuEvents(div);
    return div;
  }

  function attachSubmenuEvents(subItem) {
    subItem.querySelector(".edit-sub-btn").addEventListener("click", () => openSubmenuModal(subItem, subItem.closest(".tab-card")));
    subItem.querySelector(".delete-sub-btn").addEventListener("click", () => { 
      if(confirm("サブメニューを削除しますか？")) { 
        submitSubmenuDelete(subItem.dataset.id, subItem);
      } 
    });
  }

  function openTabModal(tabCard) {
    document.getElementById("modal-title").innerText = tabCard.dataset.id ? "タブ編集" : "新規タブ追加";
    document.getElementById("tab-id").value = tabCard.dataset.id || "";
    document.getElementById("tab-name").value = tabCard.querySelector(".tab-name")?.innerText || "";
    document.getElementById("tab-icon").value = tabCard.querySelector(".tab-icon")?.innerText || "📑";
    document.getElementById("tab-url").value = tabCard.dataset.url || "";
    tabModal.currentTabCard = tabCard;
    openModal(tabModal);
  }

  function openSubmenuModal(subItem, tabCard) {
    document.getElementById("submenu-modal-title").innerText = subItem ? "サブメニュー編集" : "サブメニュー追加";
    document.getElementById("submenu-tab-id").value = tabCard.dataset.id;
    document.getElementById("submenu-id").value = subItem?.dataset.id || "";
    document.getElementById("submenu-name").value = subItem?.querySelector("span")?.innerText || "";
    document.getElementById("submenu-url").value = subItem?.dataset.url || "";
    submenuModal.currentSubItem = subItem;
    submenuModal.currentTabCard = tabCard;
    openModal(submenuModal);
  }

  // -------------------- 新規タブ作成（保存前はDOMに追加しない） --------------------
  addTabFab.addEventListener("click", () => {
    tabModal.currentTabCard = null; // 新規作成モード
    document.getElementById("modal-title").innerText = "新規タブ追加";
    document.getElementById("tab-id").value = "";
    document.getElementById("tab-name").value = "";
    document.getElementById("tab-icon").value = "📑";
    document.getElementById("tab-url").value = "";
    openModal(tabModal);
  });

  // -------------------- タブ保存 (DB保存＋DOM追加) --------------------
  tabForm.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(tabForm);
    const isNew = !tabModal.currentTabCard;

    fetch("/tabs/save/", {
      method: "POST",
      headers: { "X-CSRFToken": getCSRFToken() },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if(data.success){
        if(isNew){
          const newTabCard = createTabCardHTML(data);
          tabList.appendChild(newTabCard);
        } else {
          const tabCard = tabModal.currentTabCard;
          tabCard.dataset.id = data.tab_id;
          tabCard.querySelector(".tab-name").innerText = data.name;
          tabCard.querySelector(".tab-icon").innerText = data.icon || "📑";
          tabCard.dataset.url = data.url_name;
        }
        closeModal(tabModal);
      }
    });
  });

  // -------------------- サブメニュー保存 (DB保存＋DOM追加) --------------------
  submenuForm.addEventListener("submit", e => {
    e.preventDefault();
    const subItem = submenuModal.currentSubItem;
    const tabCard = submenuModal.currentTabCard;
    const formData = new FormData(submenuForm);
    const isNew = !subItem;

    fetch("/submenus/save/", {
      method: "POST",
      headers: { "X-CSRFToken": getCSRFToken() },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if(data.success){
        if(isNew){
          tabCard.querySelector(".submenu-list").appendChild(createSubmenuHTML(data));
        } else {
          subItem.querySelector("span").innerText = data.name;
          subItem.dataset.url = data.url;
        }
        closeModal(submenuModal);
      }
    });
  });

  // -------------------- 削除 --------------------
  function submitTabDelete(tabId, tabCard){
    fetch(`/tabs/delete/${tabId}/`, { method: "POST", headers: {"X-CSRFToken": getCSRFToken()} })
      .then(res => res.json())
      .then(data => { if(data.success) tabCard.remove(); });
  }

  function submitSubmenuDelete(subId, subItem){
    fetch(`/submenus/delete/${subId}/`, { method: "POST", headers: {"X-CSRFToken": getCSRFToken()} })
      .then(res => res.json())
      .then(data => { if(data.success) subItem.remove(); });
  }

  // -------------------- ドラッグ順序更新 (DB保存) --------------------
  Sortable.create(tabList, { animation: 150, handle: ".tab-header", ghostClass: "dragging" });
  document.querySelectorAll(".submenu-list").forEach(list => {
    Sortable.create(list, { animation: 150, handle: ".submenu-item", ghostClass: "dragging" });
  });

  // -------------------- CSRFトークン取得 --------------------
  function getCSRFToken() {
    return document.querySelector('[name=csrfmiddlewaretoken]').value;
  }

});