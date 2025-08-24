document.addEventListener("DOMContentLoaded", function() {
  // ----------------------------
  // DOM 要素取得
  // ----------------------------
  const tabList = document.getElementById("tab-list");                // 下タブ一覧
  const submenuContainer = document.getElementById("submenu-list");   // サブメニュー編集用
  const addTabBtn = document.getElementById("add-tab-btn");
  const addSubmenuBtn = document.getElementById("add-submenu-btn");
  const tabNameInput = document.getElementById("tab-name");
  const tabIconInput = document.getElementById("tab-icon");
  const tabUrlInput = document.getElementById("tab-url");
  const tabLinkTypeSelect = document.getElementById("tab-link-type");

  const sidePanel = document.getElementById("side-panel");
  const closePanelBtn = document.getElementById("close-panel-btn");

  let dragSrc = null;
  let dragType = null;
  let scrollInterval = null;

  // ----------------------------
  // 既存タブ取得
  // ----------------------------
  fetch("/api/get_tabs/")
    .then(res => res.json())
    .then(tabs => tabs.forEach(tab => addTabToList(tab)))
    .catch(err => console.error("タブ取得エラー:", err));

  // ----------------------------
  // タブ生成
  // ----------------------------
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
    if (tab.submenus && tab.submenus.length > 0) {
      tab.submenus.forEach(sm => {
        submenuUl.appendChild(createSubmenuLi(sm, tab.id));
      });
    }

    li.querySelector(".edit-btn").addEventListener("click", () => openEditPanel(tab));
    li.querySelector(".delete-btn").addEventListener("click", () => deleteTab(tab.id, li));

    enableDragDrop(li, "tab");
  }

  function createSubmenuLi(sm, parentId) {
    const li = document.createElement("li");
    li.draggable = true;
    li.classList.add("submenu-li");
    li.dataset.parentId = parentId;
    li.dataset.smId = sm.id || "";
    li.innerHTML = `<span>${sm.name} → ${sm.url} [${sm.link_type}]</span>`;
    enableDragDrop(li, "submenu");
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
    return div;
  }

  addSubmenuBtn.addEventListener("click", () => {
    submenuContainer.appendChild(createSubmenuItem());
  });

  // ----------------------------
  // サイドパネル開閉
  // ----------------------------
  function openEditPanel(tab) {
    sidePanel.classList.add("open");
    tabNameInput.value = tab.name;
    tabIconInput.value = tab.icon;
    tabUrlInput.value = tab.url_name;
    tabLinkTypeSelect.value = tab.link_type;

    submenuContainer.innerHTML = "";
    if (tab.submenus && tab.submenus.length > 0) {
      tab.submenus.forEach(sm => {
        submenuContainer.appendChild(createSubmenuItem(sm.name, sm.url, sm.link_type));
      });
    }
    addTabBtn.dataset.editId = tab.id;
  }

  function closeEditPanel() {
    sidePanel.classList.remove("open");
    addTabBtn.dataset.editId = "";
    tabNameInput.value = "";
    tabIconInput.value = "";
    tabUrlInput.value = "";
    tabLinkTypeSelect.value = "view";
    submenuContainer.innerHTML = "";
  }

  closePanelBtn.addEventListener("click", closeEditPanel);

  // ----------------------------
  // タブ削除
  // ----------------------------
  function deleteTab(tabId, li) {
    if (!confirm("本当に削除しますか？")) return;
    fetch(`/api/delete_tab/${tabId}/`, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    }).then(res => { if(res.ok) li.remove(); saveOrder(); });
  }

  // ----------------------------
  // ドラッグ＆ドロップ (PC + タッチ)
  // ----------------------------
  function enableDragDrop(el, type) {
    el.addEventListener("dragstart", e => { dragSrc = e.target; dragType = type; e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; });
    el.addEventListener("drop", e => { e.preventDefault(); handleDrop(e.target.closest("li, .submenu-item")); });

    el.addEventListener("touchstart", e => { dragSrc = el; dragType = type; dragSrc.classList.add("dragging"); });
    el.addEventListener("touchmove", handleTouchMove);
    el.addEventListener("touchend", handleTouchEnd);
  }

  function handleDrop(target) {
    if (!target || dragSrc===target) return;
    if (dragType==="tab") {
      const nodes = Array.from(tabList.children);
      const srcIndex = nodes.indexOf(dragSrc);
      const targetIndex = nodes.indexOf(target);
      if(srcIndex<targetIndex) tabList.insertBefore(dragSrc, target.nextSibling);
      else tabList.insertBefore(dragSrc, target);
    } else if(dragType==="submenu") {
      const parentTabLi = target.closest(".tab-item");
      if(!parentTabLi) return;
      parentTabLi.querySelector(".submenus-container").insertBefore(dragSrc, target.nextSibling);
      dragSrc.dataset.parentId = parentTabLi.dataset.id;
    }
    saveOrder();
  }

  function handleTouchMove(e) {
    const touch = e.touches[0];
    const target = document.elementFromPoint(touch.clientX, touch.clientY)?.closest("li, .submenu-item");
    document.querySelectorAll("li, .submenu-item").forEach(el => el.style.borderTop="");
    if(target && dragSrc!==target) target.style.borderTop="2px dashed #2196f3";

    const margin=80, maxSpeed=15;
    clearInterval(scrollInterval);
    if(touch.clientY<margin) {
      const speed=Math.ceil((margin-touch.clientY)/5);
      scrollInterval=setInterval(()=>window.scrollBy(0,-Math.min(speed,maxSpeed)),20);
    } else if(touch.clientY>window.innerHeight-margin) {
      const speed=Math.ceil((touch.clientY-(window.innerHeight-margin))/5);
      scrollInterval=setInterval(()=>window.scrollBy(0,Math.min(speed,maxSpeed)),20);
    }
  }

  function handleTouchEnd(e) {
    const touch = e.changedTouches[0];
    const target = document.elementFromPoint(touch.clientX, touch.clientY)?.closest("li, .submenu-item");
    if(target && dragSrc!==target) handleDrop(target);
    if(dragSrc) dragSrc.classList.remove("dragging");
    dragSrc=null; dragType=null;
    clearInterval(scrollInterval);
    document.querySelectorAll("li, .submenu-item").forEach(el=>el.style.borderTop="");
  }

  // ----------------------------
  // タブ保存
  // ----------------------------
  function saveTab() {
    const data={
      name: tabNameInput.value.trim(),
      icon: tabIconInput.value.trim(),
      url_name: tabUrlInput.value.trim(),
      link_type: tabLinkTypeSelect.value,
      submenus:[]
    };
    if(!data.name || !data.icon || !data.url_name) return alert("全て入力してください");

    Array.from(submenuContainer.querySelectorAll(".submenu-item")).forEach(item=>{
      const name=item.querySelector(".submenu-name").value.trim();
      const url=item.querySelector(".submenu-url").value.trim();
      const type=item.querySelector(".submenu-link-type").value;
      if(name && url) data.submenus.push({name,url,link_type:type});
    });

    if(addTabBtn.dataset.editId) data.id=addTabBtn.dataset.editId;

    fetch("/api/save_tab/", {
      method:"POST",
      headers:{"Content-Type":"application/json","X-CSRFToken":getCsrfToken()},
      body:JSON.stringify(data)
    }).then(res=>res.json()).then(tab=>{
      if(addTabBtn.dataset.editId){
        const li=tabList.querySelector(`li[data-id='${tab.id}']`);
        if(li) li.remove();
        delete addTabBtn.dataset.editId;
      }
      addTabToList(tab);
      saveOrder();
      closeEditPanel();
    });
  }

  function saveOrder() {
    const orderData=Array.from(tabList.children).map(tabLi=>({
      id: tabLi.dataset.id,
      order:Array.from(tabList.children).indexOf(tabLi),
      submenus:Array.from(tabLi.querySelectorAll(".submenus-container li")).map((smLi,i)=>({
        id: smLi.dataset.smId||null,
        parent_id: smLi.dataset.parentId,
        order:i,
        text: smLi.textContent
      }))
    }));
    fetch("/api/save_order/",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-CSRFToken":getCsrfToken()},
      body:JSON.stringify(orderData)
    });
  }

  function getCsrfToken(){ return document.querySelector('[name=csrfmiddlewaretoken]').value; }

  // タブ保存ボタン
  addTabBtn.addEventListener("click", saveTab);
});
