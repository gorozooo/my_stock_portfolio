document.addEventListener("DOMContentLoaded", function() {
    const yearFilter = document.getElementById("yearFilter");
    const monthFilter = document.getElementById("monthFilter");
    const rows = document.querySelectorAll("#realizedTable tbody tr");

    // フィルター処理
    function filterTable() {
        const year = yearFilter.value;
        const month = monthFilter.value;

        rows.forEach(row => {
            const date = row.dataset.date;
            const rowYear = date.split("-")[0];
            const rowMonth = date.split("-")[1];

            let show = true;
            if (year && rowYear !== year) show = false;
            if (month && rowMonth !== month) show = false;

            row.style.display = show ? "" : "none";
        });
    }

    yearFilter.addEventListener("change", filterTable);
    monthFilter.addEventListener("change", filterTable);

    // ===== モーダル処理（スマホ対応） =====
    const modal = document.getElementById("stockModal");
    const closeBtn = modal.querySelector(".close");
    const modalName = document.getElementById("modalName");
    const modalPrice = document.getElementById("modalPrice");
    const modalSector = document.getElementById("modalSector");
    const modalPurchase = document.getElementById("modalPurchase");
    const modalQuantity = document.getElementById("modalQuantity");
    const modalProfit = document.getElementById("modalProfit");
    const modalRate = document.getElementById("modalRate");

    document.querySelectorAll(".stock-name").forEach(td => {
        // click + touchstart でスマホでも反応
        const openModal = (e) => {
            e.preventDefault();
            modalName.textContent = td.dataset.name;
            modalPrice.textContent = td.dataset.price;
            modalSector.textContent = td.dataset.sector;
            modalPurchase.textContent = td.dataset.purchase;
            modalQuantity.textContent = td.dataset.quantity;
            modalProfit.textContent = td.dataset.profit;
            modalRate.textContent = td.dataset.rate;
            modal.style.display = "flex";
        };
        td.addEventListener("click", openModal);
        td.addEventListener("touchstart", openModal);
    });

    closeBtn.addEventListener("click", () => {
        modal.style.display = "none";
    });

    window.addEventListener("click", (e) => {
        if(e.target == modal){
            modal.style.display = "none";
        }
    });
});
