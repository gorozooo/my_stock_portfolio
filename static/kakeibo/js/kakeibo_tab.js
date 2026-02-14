/*
[FILE] kakeibo_tab.js
[PATH] <project_root>/static/kakeibo/js/kakeibo_tab.js

このファイルは何？
- 今開いているURLに応じて、下タブを「アクティブ表示」にする。
*/

document.addEventListener("DOMContentLoaded", () => {
  const path = location.pathname || "/";
  const btns = document.querySelectorAll(".kbtab-btn");

  function mark(key){
    btns.forEach(b => {
      b.classList.toggle("is-active", b.dataset.kbtab === key);
    });
  }

  if (path === "/kakeibo/" || path === "/kakeibo") return mark("home");
  if (path.startsWith("/kakeibo/expense")) return mark("expense");
  if (path.startsWith("/kakeibo/income")) return mark("income");
  if (path.startsWith("/kakeibo/bank")) return mark("bank");
  if (path.startsWith("/kakeibo/settings")) return mark("settings");
  return mark("home");
});