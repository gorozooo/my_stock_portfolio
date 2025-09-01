document.addEventListener("DOMContentLoaded", () => {
  const tabModal = document.getElementById("tab-modal");
  const submenuModal = document.getElementById("submenu-modal");
  const tabForm = document.getElementById("tab-form");
  const submenuForm = document.getElementById("submenu-form");
  const tabList = document.getElementById("tab-list");
  const addTabFab = document.getElementById("add-tab-fab");
  const apiConfig = document.getElementById("api-config");

  const urls = {
    tabSave: apiConfig.dataset.tabSave,
    tabDelete: apiConfig.dataset.tabDelete,
    tabReorder: apiConfig.dataset.tabReorder,
    submenuSave: apiConfig.dataset.submenuSave,
    submenuDelete: apiConfig.dataset.submenuDelete,
    submenuReorder: apiConfig.dataset.submenuReorder,
  };

  const openModal = modal => modal.style.display = "block";
  const closeModal = modal => modal.style.display = "none";

  // モーダル閉じる
  document.querySelectorAll(".modal .modal-close").forEach(btn => {
    btn.addEventListener("click", () => closeModal(btn.closest(".modal")));
  });
  [tabModal, submenuModal].forEach(modal => {
    modal.addEventListener("click", e => { if(e.target===modal) closeModal(modal); });
  });

  function attachToggle(btn) {
    btn.addEventListener("click", () => btn.closest(".tab-card")?.classList.toggle("expanded"));
  }

  function createTabCardHTML(tab) {
    const div = document.createElement("div");
    div.className = "tab-card";
    div.dataset.id = tab.id || "";
    div.innerHTML = `
      <div class="tab-header">
        <div class="tab-main">
          <span class="tab-icon">${tab.icon||"📑"}</span>
          <span class="tab-name">${tab.name||"（未設定）"}</span>
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
    Sortable.create(div.querySelector(".submenu-list"), {animation:150,handle:".submenu-item",ghostClass:"dragging",onEnd:saveSubmenuOrder});
    return div;
  }

  function createSubmenuHTML(sub) {
    const div = document.createElement("div");
    div.className = "submenu-item";
    div.dataset.id = sub.id || "";
    div.innerHTML = `<span>${sub.name||"（未設定）"}</span><div class="submenu-actions"><button class="edit-sub-btn" title="編集">✏️</button><button class="delete-sub-btn" title="削除">🗑️</button></div>`;
    attachSubmenuEvents(div);
    return div;
  }

  function attachTabEvents(tabCard){
    tabCard.querySelector(".edit-tab-btn")?.addEventListener("click",()=>openTabModal(tabCard));
    tabCard.querySelector(".delete-tab-btn")?.addEventListener("click",()=>{if(confirm("タブを削除しますか？")) submitTabDelete(tabCard.dataset.id,tabCard)});
    attachToggle(tabCard.querySelector(".toggle-submenu"));
    tabCard.querySelector(".add-submenu-btn")?.addEventListener("click",()=>openSubmenuModal(null,tabCard));
    tabCard.querySelectorAll(".submenu-item").forEach(sub=>attachSubmenuEvents(sub));
  }

  function attachSubmenuEvents(subItem){
    subItem.querySelector(".edit-sub-btn")?.addEventListener("click",()=>openSubmenuModal(subItem,subItem.closest(".tab-card")));
    subItem.querySelector(".delete-sub-btn")?.addEventListener("click",()=>{if(confirm("サブメニューを削除しますか？")) submitSubmenuDelete(subItem.dataset.id,subItem)});
  }

  function openTabModal(tabCard){
    document.getElementById("modal-title").innerText = tabCard?"タブ編集":"新規タブ追加";
    document.getElementById("tab-id").value = tabCard?.dataset.id||"";
    document.getElementById("tab-name").value = tabCard?.querySelector(".tab-name")?.textContent||"";
    document.getElementById("tab-icon").value = tabCard?.querySelector(".tab-icon")?.textContent||"📑";
    document.getElementById("tab-url").value = tabCard?.dataset.url||"";
    tabModal.currentTabCard = tabCard||null;
    openModal(tabModal);
  }

  function openSubmenuModal(subItem, tabCard){
    document.getElementById("submenu-modal-title").innerText = subItem?"サブメニュー編集":"サブメニュー追加";
    document.getElementById("submenu-tab-id").value = tabCard?.dataset.id||"";
    document.getElementById("submenu-id").value = subItem?.dataset.id||"";
    document.getElementById("submenu-name").value = subItem?.querySelector("span")?.textContent||"";
    document.getElementById("submenu-url").value = subItem?.dataset.url||"";
    submenuModal.currentSubItem = subItem||null;
    submenuModal.currentTabCard = tabCard;
    openModal(submenuModal);
  }

  addTabFab.addEventListener("click",()=>openTabModal(null));

  tabForm.addEventListener("submit",e=>{
    e.preventDefault();
    const formData=new FormData(tabForm);
    const isNew=!tabModal.currentTabCard;
    fetch(urls.tabSave,{method:"POST",headers:{"X-CSRFToken":getCSRFToken()},body:formData})
    .then(res=>res.json())
    .then(data=>{
      if(data.id){
        if(isNew) tabList.appendChild(createTabCardHTML(data));
        else{
          const tabCard=tabModal.currentTabCard;
          tabCard.dataset.id=data.id;
          tabCard.dataset.url=data.url_name||"";
          tabCard.querySelector(".tab-name").textContent=data.name;
          tabCard.querySelector(".tab-icon").textContent=data.icon||"📑";
        }
        closeModal(tabModal); saveTabOrder();
      } else data.error&&alert("保存できませんでした: "+data.error);
    })
    .catch(err=>alert("通信エラー: "+err));
  });

  submenuForm.addEventListener("submit",e=>{
    e.preventDefault();
    const subItem=submenuModal.currentSubItem;
    const tabCard=submenuModal.currentTabCard;
    const formData=new FormData(submenuForm);
    const isNew=!subItem;
    fetch(urls.submenuSave,{method:"POST",headers:{"X-CSRFToken":getCSRFToken()},body:formData})
    .then(res=>res.json())
    .then(data=>{
      if(data.id){
        if(isNew&&tabCard) tabCard.querySelector(".submenu-list").appendChild(createSubmenuHTML(data));
        else if(subItem) { subItem.querySelector("span").textContent=data.name; subItem.dataset.url=data.url||""; }
        closeModal(submenuModal);
        if(tabCard) saveSubmenuOrder({from:tabCard.querySelector(".submenu-list")});
      } else data.error&&alert("保存できませんでした: "+data.error);
    })
    .catch(err=>alert("通信エラー: "+err));
  });

  function submitTabDelete(tabId,tabCard){
    if(!tabId||!tabCard) return;
    fetch(urls.tabDelete.replace("0",tabId),{method:"POST",headers:{"X-CSRFToken":getCSRFToken()}})
    .then(res=>res.json())
    .then(data=>{if(data.success) tabCard.remove()});
  }

  function submitSubmenuDelete(subId,subItem){
    if(!subId||!subItem) return;
    fetch(urls.submenuDelete.replace("0",subId),{method:"POST",headers:{"X-CSRFToken":getCSRFToken()}})
    .then(res=>res.json())
    .then(data=>{if(data.success) subItem.remove()});
  }

  function saveTabOrder(){
    if(!tabList) return;
    const order=Array.from(tabList.children).map(tab=>tab.dataset.id).filter(id=>id);
    fetch(urls.tabReorder,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":getCSRFToken()},body:JSON.stringify({order})});
  }

  function saveSubmenuOrder(evt){
    const list=evt.from||evt;
    const tabId=list.closest(".tab-card")?.dataset.id;
    if(!tabId) return;
    const order=Array.from(list.children).map(sub=>sub.dataset.id).filter(id=>id);
    fetch(urls.submenuReorder,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":getCSRFToken()},body:JSON.stringify({tab_id:tabId,order})});
  }

  function getCSRFToken(){return document.querySelector('[name=csrfmiddlewaretoken]')?.value||""}

  if(tabList) Sortable.create(tabList,{animation:150,handle:".tab-header",ghostClass:"dragging",onEnd:saveTabOrder});
  tabList.querySelectorAll(".submenu-list").forEach(list=>Sortable.create(list,{animation:150,handle:".submenu-item",ghostClass:"dragging",onEnd:saveSubmenuOrder}));
  tabList.querySelectorAll(".tab-card").forEach(tabCard=>attachTabEvents(tabCard));
});