document.addEventListener("DOMContentLoaded", function() {
    const yearFilter = document.getElementById("yearFilter");
    const monthFilter = document.getElementById("monthFilter");
    const rows = document.querySelectorAll("#realizedTable tbody tr");

    function filterTable() {
        const year = yearFilter.value;
        const month = monthFilter.value;

        rows.forEach(row => {
            const date = row.dataset.date; // "2025-08-01"
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
});