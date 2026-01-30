// static/autotrade/js/dashboard.js?v=1

(() => {
  const btn = document.getElementById("btnStop");
  if (!btn) return;

  // 次フェーズで API を作ったらここで POST して停止させる
  btn.addEventListener("click", () => {
    alert("非常停止は次フェーズで実装します（停止フラグ→ジョブが参照）");
  });
})();