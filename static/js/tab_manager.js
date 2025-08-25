document.addEventListener('DOMContentLoaded', () => {
  const fab = document.getElementById('add-tab-fab');

  fab.addEventListener('click', () => {
    alert('タブ追加画面を開く処理をここに追加');
  });

  // 既存タブの編集・削除ボタン処理
  document.querySelectorAll('.edit-tab-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      const tabId = e.target.closest('.tab-card').dataset.id;
      alert(`タブ ${tabId} 編集`);
    });
  });

  document.querySelectorAll('.delete-tab-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      const tabId = e.target.closest('.tab-card').dataset.id;
      alert(`タブ ${tabId} 削除`);
    });
  });

  // サブメニュー追加ボタン
  document.querySelectorAll('.add-submenu-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      const tabId = e.target.closest('.tab-card').dataset.id;
      alert(`タブ ${tabId} にサブメニュー追加`);
    });
  });
});
