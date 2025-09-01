document.addEventListener("DOMContentLoaded", () => {
  // -------------------- DOM要素 --------------------
  const tabModal = document.getElementById("tab-modal");
  const submenuModal = document.getElementById("submenu-modal");
  const tabForm = document.getElementById("tab-form");
  const submenuForm = document.getElementById("submenu-form");
  const tabList = document.getElementById("tab-list");
  const addTabFab = document.getElementById("add-tab-fab");
  const apiConfig = document.getElementById("api-config");

  if (!apiConfig) {
    console.error("api-config element not found. Make sure the template includes the #api-config element with data-* attributes.");
    return;
  }

  // API エンドポイント（テンプレートで data 属性に {% url ... %} を渡す想定）
  const urls = {
    tabSave: apiConfig.dataset.tabSave,
    tabDelete: apiConfig.dataset.tabDelete,   // 末尾に /0/ を入れておく想定
    tabReorder: apiConfig.dataset.tabReorder,
    submenuSave: apiConfig.dataset.submenuSave,
    submenuDelete: apiConfig.dataset.submenuDelete, // 末尾に /0/ を入れておく想定
    submenuReorder: apiConfig.dataset.submenuReorder
  };

  // -------------------- ヘルパー --------------------
  const openModal = modal => { if (modal) modal.style.display = "block"; };
  const closeModal = modal => { if (modal) modal.style.display = "none"; };

  // CSRF（テンプレに {% csrf_token %} を入れている想定）
  function getCSRFToken() {
    const tokenElem = document.querySelector('[name=csrfmiddlewaretoken]');
    return tokenElem ? tokenElem.value : "";
  }

  // safe replace for URLs like "/tabs/delete/0/" -> "/tabs/delete/<id>/"
  function replaceIdInUrl(templateUrl, id) {
    if (!templateUrl) return templateUrl;
    // replace a trailing "/0/" or trailing "0" with the id
    // prefer replacing "/0/" at the end
    if (/\/0\/?$/.test(templateUrl)) {
      return templateUrl.replace(/\/0\/?$/, `/${encodeURIComponent(id)}/`);
    }
    // fallback: replace last occurrence of "0" with id (less ideal)
    const idx = templateUrl.lastIndexOf("0");
    if (idx === -1) return templateUrl;
    return templateUrl.slice(0, idx) + encodeURIComponent(id) + templateUrl.slice(idx + 1);
  }

  // fetch utility with basic error handling and JSON parsing
  async function fetchJson(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) {
      // try to parse JSON error
      let txt = await res.text();
      try { txt = JSON.parse(txt); } catch (e) { /* leave txt as raw text */ }
      throw { status: res.status, body: txt };
    }
    return res.json();
  }

  // -------------------- モーダル閉じる操作 --------------------
  document.querySelectorAll(".modal .modal-close").forEach(btn => {
    btn.addEventListener("click", () => closeModal(btn.closest(".modal")));
  });
  [tabModal, submenuModal].forEach(modal => {
    if (!modal) return;
    modal.addEventListener("click", e => { if (e.target === modal) closeModal(modal); });
  });

  // -------------------- タブの展開/折りたたみ --------------------
  function attachToggle(btn) {
    if (!btn) return;
    btn.addEventListener("click", () => {
      const tabCard = btn.closest(".tab-card");
      if (tabCard) tabCard.classList.toggle("expanded");
    });
  }

  // -------------------- サブメニュー生成 / イベント --------------------
  function createSubmenuHTML(sub) {
    const div = document.createElement("div");
    div.className = "submenu-item";
    div.dataset.id = sub.id || "";
    // 保険としてtextContentを使ってXSS抑止（テンプレ側で出しているデータは信頼できる想定だが）
    const name = document.createElement("span");
    name.textContent = sub.name || "（未設定）";
    const actions = document.createElement("div");
    actions.className = "submenu-actions";
    actions.innerHTML = `<button class="edit-sub-btn" title="編集">✏️</button>
                         <button class="delete-sub-btn" title="削除">🗑️</button>`;
    div.appendChild(name);
    div.appendChild(actions);
    attachSubmenuEvents(div);
    return div;
  }

  function attachSubmenuEvents(subItem) {
    if (!subItem) return;
    const editBtn = subItem.querySelector(".edit-sub-btn");
    const deleteBtn = subItem.querySelector(".delete-sub-btn");

    if (editBtn) editBtn.addEventListener("click", () => openSubmenuModal(subItem, subItem.closest(".tab-card")));
    if (deleteBtn) deleteBtn.addEventListener("click", () => {
      if (!confirm("サブメニューを削除しますか？")) return;
      submitSubmenuDelete(subItem.dataset.id, subItem).catch(err => {
        console.error(err);
        alert("削除に失敗しました");
      });
    });
  }

  // -------------------- タブ生成 / イベント --------------------
  function createTabCardHTML(tab) {
    const div = document.createElement("div");
    div.className = "tab-card";
    div.dataset.id = tab.id || "";
    // build DOM safely
    const header = document.createElement("div");
    header.className = "tab-header";
    header.innerHTML = `
      <div class="tab-main">
        <span class="tab-icon"></span>
        <span class="tab-name"></span>
      </div>
      <div class="tab-actions">
        <button class="edit-tab-btn" title="編集">✏️</button>
        <button class="delete-tab-btn" title="削除">🗑️</button>
        <button class="toggle-submenu" title="サブメニュー切替">▼</button>
      </div>
    `;
    const iconElem = header.querySelector(".tab-icon");
    const nameElem = header.querySelector(".tab-name");
    iconElem.textContent = tab.icon || "📑";
    nameElem.textContent = tab.name || "（未設定）";

    const submenuList = document.createElement("div");
    submenuList.className = "submenu-list";

    const addSubBtn = document.createElement("button");
    addSubBtn.className = "add-submenu-btn";
    addSubBtn.textContent = "＋ サブメニュー追加";

    div.appendChild(header);
    div.appendChild(submenuList);
    div.appendChild(addSubBtn);

    // attach events + Sortable
    attachTabEvents(div);
    Sortable.create(submenuList, {
      animation: 150,
      handle: ".submenu-item",
      ghostClass: "dragging",
      onEnd: saveSubmenuOrder
    });

    // if server returned submenus, populate them
    if (Array.isArray(tab.submenus)) {
      tab.submenus.forEach(sm => submenuList.appendChild(createSubmenuHTML(sm)));
    }

    return div;
  }

  function attachTabEvents(tabCard) {
    if (!tabCard) return;
    const editBtn = tabCard.querySelector(".edit-tab-btn");
    const deleteBtn = tabCard.querySelector(".delete-tab-btn");
    const toggleBtn = tabCard.querySelector(".toggle-submenu");
    const addSubBtn = tabCard.querySelector(".add-submenu-btn");

    if (editBtn) editBtn.addEventListener("click", () => openTabModal(tabCard));
    if (deleteBtn) deleteBtn.addEventListener("click", () => {
      if (!confirm("タブを削除しますか？")) return;
      submitTabDelete(tabCard.dataset.id, tabCard).catch(err => {
        console.error(err);
        alert("削除に失敗しました");
      });
    });
    if (toggleBtn) attachToggle(toggleBtn);
    if (addSubBtn) addSubBtn.addEventListener("click", () => openSubmenuModal(null, tabCard));

    // ensure subitems have handlers
    tabCard.querySelectorAll(".submenu-item").forEach(sub => attachSubmenuEvents(sub));
  }

  // -------------------- モーダル開閉 --------------------
  function openTabModal(tabCard) {
    const title = document.getElementById("modal-title");
    const idInput = document.getElementById("tab-id");
    const nameInput = document.getElementById("tab-name");
    const iconInput = document.getElementById("tab-icon");

    title && (title.textContent = tabCard ? "タブ編集" : "新規タブ追加");
    idInput && (idInput.value = tabCard && tabCard.dataset.id ? tabCard.dataset.id : "");
    nameInput && (nameInput.value = tabCard ? (tabCard.querySelector(".tab-name")?.textContent || "") : "");
    iconInput && (iconInput.value = tabCard ? (tabCard.querySelector(".tab-icon")?.textContent || "📑") : "📑");

    tabModal.currentTabCard = tabCard || null;
    openModal(tabModal);
  }

  function openSubmenuModal(subItem, tabCard) {
    const title = document.getElementById("submenu-modal-title");
    const tabIdInput = document.getElementById("submenu-tab-id");
    const subIdInput = document.getElementById("submenu-id");
    const nameInput = document.getElementById("submenu-name");
    const urlInput = document.getElementById("submenu-url");

    title && (title.textContent = subItem ? "サブメニュー編集" : "サブメニュー追加");
    tabIdInput && (tabIdInput.value = tabCard?.dataset.id || "");
    subIdInput && (subIdInput.value = subItem?.dataset.id || "");
    nameInput && (nameInput.value = subItem ? (subItem.querySelector("span")?.textContent || "") : "");
    urlInput && (urlInput.value = subItem ? (subItem.dataset.url || "") : "");

    submenuModal.currentSubItem = subItem || null;
    submenuModal.currentTabCard = tabCard || null;
    openModal(submenuModal);
  }

  // -------------------- 新規タブボタン --------------------
  if (addTabFab) addTabFab.addEventListener("click", () => openTabModal(null));

  // -------------------- タブ保存 --------------------
  tabForm && tabForm.addEventListener("submit", async e => {
    e.preventDefault();
    const submitBtn = tabForm.querySelector("button[type=submit]");
    if (submitBtn) submitBtn.disabled = true;

    try {
      const formData = new FormData(tabForm);
      // include tab id for edit
      const isNew = !tabModal.currentTabCard;
      const jsonResponse = await fetchJson(urls.tabSave, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: formData
      });

      // server returns { id:..., name:..., icon:..., url_name:..., submenus: [...] }
      if (jsonResponse && jsonResponse.id) {
        if (isNew) {
          const newCard = createTabCardHTML(jsonResponse);
          tabList.appendChild(newCard);
        } else {
          const tabCard = tabModal.currentTabCard;
          if (tabCard) {
            tabCard.dataset.id = jsonResponse.id;
            tabCard.querySelector(".tab-name").textContent = jsonResponse.name || "";
            tabCard.querySelector(".tab-icon").textContent = jsonResponse.icon || "📑";
            // refresh submenu list if server returned them
            if (Array.isArray(jsonResponse.submenus)) {
              const submenuList = tabCard.querySelector(".submenu-list");
              submenuList && (submenuList.innerHTML = "");
              jsonResponse.submenus.forEach(sm => submenuList.appendChild(createSubmenuHTML(sm)));
            }
            attachTabEvents(tabCard); // reattach if needed
          }
        }
        closeModal(tabModal);
        saveTabOrder();
      } else {
        alert("保存に失敗しました：" + (jsonResponse && jsonResponse.error ? jsonResponse.error : "不明なエラー"));
      }
    } catch (err) {
      console.error(err);
      alert("通信エラーが発生しました。コンソールを確認してください。");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  // -------------------- サブメニュー保存 --------------------
  submenuForm && submenuForm.addEventListener("submit", async e => {
    e.preventDefault();
    const submitBtn = submenuForm.querySelector("button[type=submit]");
    if (submitBtn) submitBtn.disabled = true;

    try {
      const formData = new FormData(submenuForm);
      const isNew = !submenuModal.currentSubItem;
      const jsonResponse = await fetchJson(urls.submenuSave, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: formData
      });

      if (jsonResponse && jsonResponse.id) {
        const tabCard = submenuModal.currentTabCard;
        if (isNew && tabCard) {
          const submenuList = tabCard.querySelector(".submenu-list");
          submenuList && submenuList.appendChild(createSubmenuHTML(jsonResponse));
        } else if (submenuModal.currentSubItem) {
          const subItem = submenuModal.currentSubItem;
          subItem.querySelector("span").textContent = jsonResponse.name || "";
          subItem.dataset.url = jsonResponse.url || "";
        }
        closeModal(submenuModal);
        if (tabCard) saveSubmenuOrder({ from: tabCard.querySelector(".submenu-list") });
      } else {
        alert("保存に失敗しました：" + (jsonResponse && jsonResponse.error ? jsonResponse.error : "不明なエラー"));
      }
    } catch (err) {
      console.error(err);
      alert("通信エラーが発生しました。コンソールを確認してください。");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  // -------------------- 削除 --------------------
  async function submitTabDelete(tabId, tabCard) {
    if (!tabId || !tabCard) return;
    const url = replaceIdInUrl(urls.tabDelete, tabId);
    try {
      const jsonResponse = await fetchJson(url, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() }
      });
      if (jsonResponse && jsonResponse.success) {
        tabCard.remove();
      } else {
        alert("削除できませんでした");
      }
    } catch (err) {
      console.error(err);
      alert("削除時の通信エラー");
    }
  }

  async function submitSubmenuDelete(subId, subItem) {
    if (!subId || !subItem) return;
    const url = replaceIdInUrl(urls.submenuDelete, subId);
    try {
      const jsonResponse = await fetchJson(url, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() }
      });
      if (jsonResponse && jsonResponse.success) {
        subItem.remove();
      } else {
        alert("削除できませんでした");
      }
    } catch (err) {
      console.error(err);
      alert("削除時の通信エラー");
    }
  }

  // -------------------- ドラッグ順序更新 --------------------
  if (tabList) {
    Sortable.create(tabList, {
      animation: 150,
      handle: ".tab-header",
      ghostClass: "dragging",
      onEnd: saveTabOrder
    });
  }

  // 初期の各サブメニューリストに Sortable を設定
  tabList && tabList.querySelectorAll(".submenu-list").forEach(list => {
    Sortable.create(list, {
      animation: 150,
      handle: ".submenu-item",
      ghostClass: "dragging",
      onEnd: saveSubmenuOrder
    });
  });

  // -------------------- 順序保存 --------------------
  async function saveTabOrder() {
    if (!tabList) return;
    const order = Array.from(tabList.children)
      .map(tab => tab.dataset.id)
      .filter(id => id);
    try {
      await fetchJson(urls.tabReorder, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken()
        },
        body: JSON.stringify({ order })
      });
    } catch (err) {
      console.error("順序保存エラー:", err);
    }
  }

  async function saveSubmenuOrder(evt) {
    // evt.from は Sortable のイベントオブジェクト形式に依存するので、安全に取得
    const list = evt && evt.from ? evt.from : evt;
    if (!list || !list.closest) return;
    const tabCard = list.closest(".tab-card");
    const tabId = tabCard ? tabCard.dataset.id : null;
    if (!tabId) return;
    const order = Array.from(list.children).map(sub => sub.dataset.id).filter(id => id);
    try {
      await fetchJson(urls.submenuReorder, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken()
        },
        body: JSON.stringify({ tab_id: tabId, order })
      });
    } catch (err) {
      console.error("サブ順序保存エラー:", err);
    }
  }

  // -------------------- 初期化: 既存タブにイベント付与 --------------------
  (function initExisting() {
    if (!tabList) return;
    tabList.querySelectorAll(".tab-card").forEach(tabCard => {
      // ensure submenu Sortable exists (in case DOM came from server)
      const submenuList = tabCard.querySelector(".submenu-list");
      if (submenuList && !submenuList._sortableInitialized) {
        Sortable.create(submenuList, {
          animation: 150,
          handle: ".submenu-item",
          ghostClass: "dragging",
          onEnd: saveSubmenuOrder
        });
        submenuList._sortableInitialized = true;
      }
      attachTabEvents(tabCard);
    });
  })();

}); // DOMContentLoaded end