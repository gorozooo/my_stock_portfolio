document.addEventListener("DOMContentLoaded", () => {
  console.log("保有株一覧ページが読み込まれました");

  // 例：クリックしたらハイライト
  const cards = document.querySelectorAll(".stock-card");
  cards.forEach(card => {
    card.addEventListener("click", () => {
      card.classList.toggle("highlight");
    });
  });
});