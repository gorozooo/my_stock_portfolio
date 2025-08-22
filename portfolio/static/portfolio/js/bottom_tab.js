// 今は特にJSは不要ですが、将来的にアニメやクリックイベント追加可能
document.addEventListener('DOMContentLoaded', function() {
    // 例: アクティブタブの色変更
    const tabs = document.querySelectorAll('.tab-link');
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
        });
    });
});
