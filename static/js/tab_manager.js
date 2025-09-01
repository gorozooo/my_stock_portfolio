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

  // -------------------- 保存・復元 --------------------
  function saveToStorage() {
    const tabsData = Array.from(tabList.children).map(tabCard => ({
      id: tabCard.dataset.id,
      name: tabCard.querySelector(".tab-name").innerText,
      icon: tabCard.querySelector(".tab-icon").innerText,
      url_name: tabCard.dataset.url,
      submenus: Array.from(tabCard.querySelectorAll(".submenu-item")).map(sub => ({
        id: sub.dataset.id,
        name: sub.querySelector("span").innerText,
        url: sub.dataset.url
      }))
    }));
    localStorage.setItem("my_tabs", JSON.stringify(tabsData));
  }

  function loadFromStorage() {
    const data = JSON.parse(localStorage.getItem("my_tabs") || "[]");
    data.forEach(tab => {
      const tabCard = createTabCardHTML(tab);
      tabList.appendChild(tabCard);
      tab.submenus.forEach(sub => {
        tabCard.querySelector(".submenu-list").appendChild(createSubmenuHTML(sub));
      });
    });
  }

  // -------------------- タブ・サブメニュー作成 --------------------
  function createTabCardHTML(tab) {
    const div = document.createElement("div");
    div.className = "tab-card";
    div.dataset.id = tab.id || Date.now();
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
        tabCard.remove(); 
        saveToStorage();
      } 
    });
    attachToggle(tabCard.querySelector(".toggle-submenu"));
    tabCard.querySelector(".add-submenu-btn").addEventListener("click", () => openSubmenuModal(null, tabCard));
  }

  function createSubmenuHTML(sub) {
    const div = document.createElement("div");
    div.className = "submenu-item";
    div.dataset.id = sub.id || Date.now();
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
        subItem.remove(); 
        saveToStorage();
      } 
    });
  }

  function openTabModal(tabCard) {
    const id = tabCard.dataset.id || "";
    document.getElementById("modal-title").innerText = id ? "タブ編集" : "新規タブ追加";
    document.getElementById("tab-id").value = id;
    document.getElementById("tab-name").value = tabCard.querySelector(".tab-name")?.innerText || "";
    document.getElementById("tab-icon").value = tabCard.querySelector(".tab-icon")?.innerText || "";
    document.getElementById("tab-url").value = tabCard.dataset.url || "";
    tabModal.currentTabCard = tabCard;
    openModal(tabModal);
  }

  function openSubmenuModal(subItem, tabCard) {
    document.getElementById("submenu-modal-title").innerText = subItem ? "サブメニュー編集" : "サブメニュー追加";
    document.getElementById("submenu-tab-id").value = tabCard.dataset.id;
    document.getElementById("submenu-id").value = subItem?.dataset.id || "";
    document.getElementById("submenu-name").value = subItem?.querySelector("span").innerText || "";
    document.getElementById("submenu-url").value = subItem?.dataset.url || "";
    submenuModal.currentSubItem = subItem;
    submenuModal.currentTabCard = tabCard;
    openModal(submenuModal);
  }

  // -------------------- 新規タブ追加 --------------------
  addTabFab.addEventListener("click", () => {
    const newTab = { id: Date.now(), name: "新規タブ", icon: "📑", url_name: "" };
    const tabCard = createTabCardHTML(newTab);
    tabList.appendChild(tabCard); 
    openTabModal(tabCard);
    saveToStorage();
  });

  // -------------------- タブ保存 --------------------
  tabForm.addEventListener("submit", e => {
    e.preventDefault();
    const tabCard = tabModal.currentTabCard;
    tabCard.querySelector(".tab-name").innerText = document.getElementById("tab-name").value;
    tabCard.querySelector(".tab-icon").innerText = document.getElementById("tab-icon").value || "📑";
    tabCard.dataset.url = document.getElementById("tab-url").value;
    closeModal(tabModal);
    saveToStorage();
  });

  // -------------------- サブメニュー保存 --------------------
  submenuForm.addEventListener("submit", e => {
    e.preventDefault();
    const subItem = submenuModal.currentSubItem;
    const tabCard = submenuModal.currentTabCard;
    const name = document.getElementById("submenu-name").value;
    const url = document.getElementById("submenu-url").value;

    if(subItem){
      subItem.querySelector("span").innerText = name;
      subItem.dataset.url = url;
    } else {
      const newSub = { id: Date.now(), name: name, url: url };
      tabCard.querySelector(".submenu-list").appendChild(createSubmenuHTML(newSub));
    }
    closeModal(submenuModal);
    saveToStorage();
  });

  // -------------------- ドラッグ順序更新 --------------------
  Sortable.create(tabList, { animation: 150, handle: ".tab-header", ghostClass: "dragging", onEnd: saveToStorage });
  document.querySelectorAll(".submenu-list").forEach(list => {
    Sortable.create(list, { animation: 150, handle: ".submenu-item", ghostClass: "dragging", onEnd: saveToStorage });
  });

  // -------------------- ページロード時復元 --------------------
  loadFromStorage();

});