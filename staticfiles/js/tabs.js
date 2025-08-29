async function loadTabs() {
  let response = await fetch("/api/tabs/");
  let tabs = await response.json();
  let list = document.getElementById("tabList");
  list.innerHTML = "";

  tabs.forEach(tab => {
    let li = document.createElement("li");
    li.innerHTML = `
      <span><i class="fa ${tab.icon}"></i> ${tab.name} (${tab.url_name})</span>
      <button onclick="deleteTab(${tab.id})">削除</button>
    `;
    list.appendChild(li);
  });
}

async function addTab() {
  let name = document.getElementById("tabName").value;
  let icon = document.getElementById("tabIcon").value;
  let url = document.getElementById("tabUrl").value;

  let response = await fetch("/api/tabs/save/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: name,
      icon: icon,
      url_name: url,
      order: 0
    }),
  });

  let data = await response.json();
  alert("保存できたよ！ → " + data.name);
  loadTabs();
}

async function deleteTab(id) {
  if (!confirm("削除しますか？")) return;
  await fetch(`/api/tabs/${id}/delete/`, { method: "DELETE" });
  loadTabs();
}

document.addEventListener("DOMContentLoaded", loadTabs);
