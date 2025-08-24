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

  // --- タブ追加 ---
  addTabBtn.addEventListener("click", saveTab);

  // --- サブメニュー追加 ---
  addSubmenuBtn.addEventListener("click", () => {
    const div = createSubmenuItem();
    submenuContainer.appendChild(div);
  });

  // --- タブ作成・更新 ---
  function addTabToList(tab) {
    const li = document.createElement("li");
    li.dataset.id = tab.id;
    li.classList.add("tab-item");
    li.innerHTML = `
      <span>${tab.name} (${tab.icon}) → ${tab.url_name} [${tab.link_type}]</span>
      <button class="edit-btn">編集</button>
      <button class="delete-btn">削除</button>
      <ul class="submenus-container"></ul>
    `;
    tabList.appendChild(li);
    enableDrag(li, "tab");

    const submenuUl = li.querySelector(".submenus-container");
    if(tab.submenus) {
      tab.submenus.forEach(sm => {
        const smLi = createSubmenuLi(sm, tab.id);
        submenuUl.appendChild(smLi);
      });
    }

    li.querySelector(".edit-btn").addEventListener("click", () => editTab(tab, li));
    li.querySelector(".delete-btn").addEventListener("click", () => deleteTab(tab.id, li));
  }

  function createSubmenuLi(sm, parentId) {
    const li = document.createElement("li");
    li.textContent = `${sm.name} → ${sm.url} [${sm.link_type}]`;
    li.dataset.smId = sm.id || "";
    li.dataset.parentId = parentId;
    enableDrag(li, "submenu");
    return li;
  }

  // --- フォーム上のサブメニュー作成 ---
  function createSubmenuItem(name="", url="", type="view") {
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
    div.querySelector(".remove-submenu-btn").addEventListener("click", ()=>div.remove());
    enableDrag(div, "submenu");
    return div;
  }

  // --- タブ編集 ---
  function editTab(tab, li) {
    tabNameInput.value = tab.name;
    tabIconInput.value = tab.icon;
    tabUrlInput.value = tab.url_name;
    tabLinkTypeSelect.value = tab.link_type;

    submenuContainer.innerHTML = "";
    if(tab.submenus){
      tab.submenus.forEach(sm => {
        const div = createSubmenuItem(sm.name, sm.url, sm.link_type);
        submenuContainer.appendChild(div);
      });
    }
    addTabBtn.dataset.editId = tab.id;
  }

  // --- タブ削除 ---
  function deleteTab(id, li){
    if(!confirm("削除しますか？")) return;
    fetch(`/api/delete_tab/${id}/`, { method:"POST", headers:{"X-CSRFToken":getCsrfToken()} })
      .then(res => { if(res.ok) li.remove(); saveOrder(); });
  }

  // --- ドラッグ＆ドロップ（PC & タッチ対応） ---
  function enableDrag(el, type){
    el.draggable = true;
    el.addEventListener("dragstart", e=>{ dragStart(e,type); });
    el.addEventListener("dragover", dragOver);
    el.addEventListener("drop", drop);

    // タッチ対応
    el.addEventListener("touchstart", e=>{ dragStart(e,type,true); });
    el.addEventListener("touchmove", touchMove);
    el.addEventListener("touchend", touchEnd);
  }

  function dragStart(e,type,isTouch=false){
    dragSrc = e.target.closest("li, .submenu-item");
    dragType = type;
    if(isTouch) dragSrc.classList.add("dragging");
    else e.dataTransfer.effectAllowed="move";
  }

  function dragOver(e){ e.preventDefault(); }

  function drop(e){
    e.preventDefault();
    const target = e.target.closest("li, .submenu-item");
    if(!target || dragSrc===target) return;

    if(dragType==="tab") tabList.insertBefore(dragSrc,target.nextSibling);
    else if(dragType==="submenu"){
      if(target.classList.contains("submenu-item")) {
        submenuContainer.insertBefore(dragSrc,target.nextSibling);
      } else {
        const parentTab = target.closest(".tab-item");
        if(!parentTab) return;
        parentTab.querySelector(".submenus-container").insertBefore(dragSrc,target.nextSibling);
      }
    }
    saveOrder();
  }

  function touchMove(e){
    e.preventDefault();
    const touch = e.touches[0];
    dragSrc.style.position = "absolute";
    dragSrc.style.top = touch.clientY+"px";
    dragSrc.style.left = touch.clientX+"px";
  }

  function touchEnd(e){
    dragSrc.style.position="";
    dragSrc.style.top="";
    dragSrc.style.left="";
    dragSrc.classList.remove("dragging");
  }

  // --- タブ保存 ---
  function saveTab(){
    const data = {
      name: tabNameInput.value.trim(),
      icon: tabIconInput.value.trim(),
      url_name: tabUrlInput.value.trim(),
      link_type: tabLinkTypeSelect.value,
      submenus: []
    };
    if(!data.name || !data.icon || !data.url_name) return alert("全て入力してください");

    Array.from(submenuContainer.querySelectorAll(".submenu-item")).forEach(item=>{
      const name = item.querySelector(".submenu-name").value.trim();
      const url = item.querySelector(".submenu-url").value.trim();
      const type = item.querySelector(".submenu-link-type").value;
      if(name && url) data.submenus.push({name,url,link_type:type});
    });

    if(addTabBtn.dataset.editId) data.id = addTabBtn.dataset.editId;

    fetch("/api/save_tab/",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-CSRFToken":getCsrfToken()},
      body:JSON.stringify(data)
    })
    .then(res=>res.json())
    .then(tab=>{
      if(addTabBtn.dataset.editId){
        const li = tabList.querySelector(`li[data-id='${tab.id}']`);
        if(li) li.remove();
        delete addTabBtn.dataset.editId;
      }
      addTabToList(tab);

      tabNameInput.value="";
      tabIconInput.value="";
      tabUrlInput.value="";
      tabLinkTypeSelect.value="view";
      submenuContainer.innerHTML="";
      saveOrder();
    });
  }

  // --- 並び順保存 ---
  function saveOrder(){
    const orderData = Array.from(tabList.children).map((tabLi,i)=>({
      id: tabLi.dataset.id,
      order: i,
      submenus: Array.from(tabLi.querySelectorAll(".submenus-container li")).map((smLi,j)=>({
        id: smLi.dataset.smId || null,
        parent_id: smLi.dataset.parentId,
        order: j,
        text: smLi.textContent
      }))
    }));

    const formSubmenus = Array.from(submenuContainer.querySelectorAll(".submenu-item")).map((div,i)=>({
      name: div.querySelector(".submenu-name").value.trim(),
      url: div.querySelector(".submenu-url").value.trim(),
      link_type: div.querySelector(".submenu-link-type").value,
      order: i
    }));

    fetch("/api/save_order/",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-CSRFToken":getCsrfToken()},
      body:JSON.stringify({tabs:orderData, formSubmenus:formSubmenus})
    });
  }

  function getCsrfToken(){ return document.querySelector('[name=csrfmiddlewaretoken]').value; }
});
