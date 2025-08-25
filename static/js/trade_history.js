// フィルター処理
document.addEventListener("DOMContentLoaded", function () {
    const yearFilter = document.getElementById("yearFilter");
    const monthFilter = document.getElementById("monthFilter");
    const tradeItems = document.querySelectorAll(".trade-item");

    function filterTrades() {
        const year = yearFilter.value;
        const month = monthFilter.value;

        tradeItems.forEach(item => {
            const dateText = item.querySelector(".trade-date").textContent;
            const [y, m] = dateText.split("/").map(v => parseInt(v));

            let show = true;

            if (year !== "all" && y !== parseInt(year)) show = false;
            if (month !== "all" && m !== parseInt(month)) show = false;

            item.style.display = show ? "flex" : "none";
        });
    }

    yearFilter.addEventListener("change", filterTrades);
    monthFilter.addEventListener("change", filterTrades);
});