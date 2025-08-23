document.addEventListener("DOMContentLoaded", function() {
  const tabList = document.getElementById("tab-list");
  const submenuContainer = document.getElementById("submenu-container");
  const addTabBtn = document.getElementById("add-tab-btn");
  const addSubmenuBtn = document.getElementById("add-submenu-btn");
  const tabNameInput = document.getElementById("tab-name");
  const tabIconInput = document.getElementById("tab-icon");
  const tabUrlInput = document.getElementById("tab-url");
  const tabLinkTypeSelect = document.getElementById("tab-link-type");

  let dragSrc = null;
  let dragType = null;

  // --- 既存タブ取得 ---
  fetch("/api/get_tabs/")
    .then(res => res.json())
    .then(tabs => tabs.forEach(tab => addTabToList(tab)));

  // --- タブ作成 ---
  function addTabToList(tab) {
    const li = document.createElement("li");
    li.dataset.id = tab.id;
    li.draggable = true;
    li.classList.add("tab-item");
    li.innerHTML = `
      <div class="tab-header">
        <span>${tab.name} (${tab.icon}) → ${tab.url_name} [${tab.link_type}]</span>
        <button class="edit-btn">編集</button>
        <button class="delete-btn">削除</button>
      </div>
      <ul class="submenus-container"></ul>
    `;
    tabList.appendChild(li);

    const submenuUl = li.querySelector(".submenus-container");
    if (tab.submenus) {
      tab.submenus.forEach(sm => {
        const subLi = createSubmenuLi(sm, tab.id);
        submenuUl.appendChild(subLi);
      });
    }

    li.querySelector(".edit-btn").addEventListener("click", () => editTab(tab, li));
    li.querySelector(".delete-btn").addEventListener("click", () => deleteTab(tab.id, li));

    li.addEventListener("dragstart", e => { dragStart(e, "tab"); });
    li.addEventListener("dragover", dragOver);
    li.addEventListener("drop", drop);
  }

  function createSubmenuLi(sm, parentId) {
    const li = document.createElement("li");
    li.draggable = true;
    li.classList.add("submenu-li");
    li.dataset.parentId = parentId;
    li.dataset.smId = sm.id || ""; // DB上IDある場合
    li.innerHTML = `<span>${sm.name} → ${sm.url} [${sm.link_type}]</span>`;
    li.addEventListener("dragstart", e => { dragStart(e, "submenu"); });
    li.addEventListener("dragover", dragOver);
    li.addEventListener("drop", drop);
    return li;
  }

  function createSubmenuItem(name = "", url = "", type = "view") {
    const div = document.createElement("div");
    div.classList.add("submenu-item");
    div.innerHTML = `
      <input type="text" class="submenu-name" placeholder="サブメニュー名" value="${name}">
      <input type="text" class="submenu-url" placeholder="URLまたはビュー名" value="${url}">
      <select class="submenu-link-type">
        <option value="view" ${type==='view'?'selected':''}>内部ビュー</option>
        <option value="url" ${type==='url'?'selected':''}>外部URL</option>
        <option value="dummy" ${type==='dummy'?'selected':''}>ダミーリンク (#)</option>
      </select>
      <button type="button" class="remove-submenu-btn">削除</button>
    `;
    div.querySelector(".remove-submenu-btn").addEventListener("click", () => div.remove());
    div.draggable = true;
    div.addEventListener("dragstart", e => { dragStart(e, "submenu"); });
    div.addEventListener("dragover", dragOver);
    div.addEventListener("drop", drop);
    return div;
  }

  addSubmenuBtn.addEventListener("click", () => {
    const div = createSubmenuItem();
    submenuContainer.appendChild(div);
  });

  addTabBtn.addEventListener("click", () => saveTab());

  function editTab(tab, li) {
    tabNameInput.value = tab.name;
    tabIconInput.value = tab.icon;
    tabUrlInput.value = tab.url_name;
    tabLinkTypeSelect.value = tab.link_type;

    submenuContainer.innerHTML = "";
    if (tab.submenus) {
      tab.submenus.forEach(sm => {
        const div = createSubmenuItem(sm.name, sm.url, sm.link_type);
        submenuContainer.appendChild(div);
      });
    }
    addTabBtn.dataset.editId = tab.id;
  }

  function deleteTab(tabId, li) {
    if (!confirm("本当に削除しますか？")) return;
    fetch(`/api/delete_tab/${tabId}/`, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    }).then(res => { if (res.ok) li.remove(); saveOrder(); });
  }

  // --- ドラッグ＆ドロップ ---
  function dragStart(e, type) { dragSrc = e.target; dragType = type; e.dataTransfer.effectAllowed = "move"; }
  function dragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }
  function drop(e) {
    e.preventDefault();
    const target = e.target.closest("li, .submenu-item");
    if (!target || dragSrc === target) return;

    if (dragType === "tab") {
      const nodes = Array.from(tabList.children);
      const srcIndex = nodes.indexOf(dragSrc);
      const targetIndex = nodes.indexOf(target);
      if (srcIndex < targetIndex) tabList.insertBefore(dragSrc, target.nextSibling);
      else tabList.insertBefore(dragSrc, target);
    }

    if (dragType === "submenu") {
      // 移動先タブ取得
      const parentTabLi = target.closest(".tab-item");
      if (!parentTabLi) return;

      const ul = parentTabLi.querySelector(".submenus-container");
      ul.insertBefore(dragSrc, target.nextSibling);

      // 親タブID更新
      dragSrc.dataset.parentId = parentTabLi.dataset.id;
    }

    saveOrder();
  }

  function saveTab() {
    const data = {
      name: tabNameInput.value.trim(),
      icon: tabIconInput.value.trim(),
      url_name: tabUrlInput.value.trim(),
      link_type: tabLinkTypeSelect.value,
      submenus: []
    };
    if (!data.name || !data.icon || !data.url_name) return alert("全て入力してください");

    Array.from(submenuContainer.querySelectorAll(".submenu-item")).forEach(item => {
      const name = item.querySelector(".submenu-name").value.trim();
      const url = item.querySelector(".submenu-url").value.trim();
      const type = item.querySelector(".submenu-link-type").value;
      if (name && url) data.submenus.push({ name, url, link_type: type });
    });

    if (addTabBtn.dataset.editId) data.id = addTabBtn.dataset.editId;

    fetch("/api/save_tab/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
      body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(tab => {
      if (addTabBtn.dataset.editId) {
        const li = tabList.querySelector(`li[data-id='${tab.id}']`);
        if (li) li.remove();
        delete addTabBtn.dataset.editId;
      }
      addTabToList(tab);

      tabNameInput.value = "";
      tabIconInput.value = "";
      tabUrlInput.value = "";
      tabLinkTypeSelect.value = "view";
      submenuContainer.innerHTML = "";
      saveOrder();
    });
  }

  // --- 並び順保存 ---
  function saveOrder() {
    const orderData = Array.from(tabList.children).map(tabLi => {
      return {
        id: tabLi.dataset.id,
        order: Array.from(tabList.children).indexOf(tabLi),
        submenus: Array.from(tabLi.querySelectorAll(".submenus-container li")).map((smLi, i) => ({
          id: smLi.dataset.smId || null,
          parent_id: smLi.dataset.parentId,
          order: i,
          text: smLi.textContent
        }))
      };
    });

    fetch("/api/save_order/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
      body: JSON.stringify(orderData)
    });
  }

  function getCsrfToken() { return document.querySelector('[name=csrfmiddlewaretoken]').value; }
});
