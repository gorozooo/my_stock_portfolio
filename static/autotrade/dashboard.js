// static/autotrade/dashboard.js?v=1

(() => {
  const btn = document.getElementById("btnStop");
  if (!btn) return;

  const url = btn.getAttribute("data-url");
  if (!url) return; // 停止中などで data-url が無い時

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  btn.addEventListener("click", async () => {
    const ok = confirm("非常停止します。以後の自動売買を止めます。よろしいですか？");
    if (!ok) return;

    btn.disabled = true;
    btn.textContent = "停止中…";

    try {
      const csrftoken = getCookie("csrftoken");

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
        },
        body: JSON.stringify({ reason: "manual" }),
        credentials: "same-origin",
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        alert("非常停止に失敗しました（API）");
        btn.disabled = false;
        btn.textContent = "非常停止（ワンタップ）";
        return;
      }

      // 成功 → 画面更新（表示が「非常停止中」に切り替わる）
      location.reload();
    } catch (e) {
      alert("非常停止に失敗しました（通信）");
      btn.disabled = false;
      btn.textContent = "非常停止（ワンタップ）";
    }
  });
})();