document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");

  let pressTimer;
  document.querySelectorAll(".tab-btn").forEach(btn => {
    // シングルタップ → ページ遷移
    btn.addEventListener("click", () => {
      const link = btn.dataset.link;
      if (link) window.location.href = link;
    });

    // 長押し（スマホ）
    btn.addEventListener("touchstart", () => {
      pressTimer = setTimeout(() => {
        submenu.classList.add("show");
      }, 600); // 0.6秒で長押し判定
    });
    btn.addEventListener("touchend", () => clearTimeout(pressTimer));

    // 右クリック（PC）
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