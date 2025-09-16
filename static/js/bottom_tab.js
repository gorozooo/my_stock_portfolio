document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");

  // 長押し検出
  let pressTimer;
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("touchstart", () => {
      pressTimer = setTimeout(() => {
        submenu.classList.add("show");
      }, 600); // 0.6秒で長押し
    });
    btn.addEventListener("touchend", () => clearTimeout(pressTimer));
    btn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      submenu.classList.toggle("show");
    });
  });

  // 背景クリックで閉じる
  document.addEventListener("click", (e) => {
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) {
      submenu.classList.remove("show");
    }
  });
});