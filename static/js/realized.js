// static/js/realized.js
document.addEventListener('DOMContentLoaded', function () {
  // --- 必須要素 ---
  var table   = document.getElementById('realizedTable');
  var tbody   = table ? table.querySelector('tbody') : null;

  var sumCountEl      = document.getElementById('sumCount');
  var winRateEl       = document.getElementById('winRate');
  var netProfitEl     = document.getElementById('netProfit');

  // 見つからない場合は何もしない（スマホでも落ちない）
  if (!table || !tbody || !sumCountEl || !winRateEl || !netProfitEl) return;

  // --- オプションKPI（あれば更新） ---
  var totalProfitEl   = document.getElementById('totalProfit');
  var totalLossEl     = document.getElementById('totalLoss');
  var avgNetEl        = document.getElementById('avgNet');
  var avgProfitOnlyEl = document.getElementById('avgProfitOnly');
  var avgLossOnlyEl   = document.getElementById('avgLossOnly');

  // --- フィルタ ---
  var yearFilter  = document.getElementById('yearFilter');
  var monthFilter = document.getElementById('monthFilter');
  var chips = (function(){ 
    var n = document.querySelectorAll('.quick-chips .chip'); 
    return Array.prototype.slice.call(n);
  })();

  // ===== ユーティリティ =====
  function toNumber(text) {
    if (text == null) return 0;
    // 「+」「,」「%」「空白」などを除去して数値化
    var s = String(text).replace(/[^\-0-9.]/g, '');
    var v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  }
  function fmtInt(n) {
    // 整数丸め + 桁区切り
    return Math.round(n).toLocaleString('ja-JP');
  }
  function allDataRows() {
    // データ行のみ（グループ行は class="group-row"）
    var rows = tbody.querySelectorAll('tr');
    var out = [];
    for (var i=0; i<rows.length; i++) {
      if (!rows[i].classList.contains('group-row')) out.push(rows[i]);
    }
    return out;
  }
  function visibleRows() {
    var rows = allDataRows();
    var out = [];
    for (var i=0; i<rows.length; i++) {
      if (rows[i].style.display !== 'none') out.push(rows[i]);
    }
    return out;
  }

  // ===== KPI計算・描画 =====
  function updateKPI(rows) {
    // rows は「表示対象のデータ行」
    var count = rows.length;
    var vals = [];
    for (var i=0; i<rows.length; i++) {
      // 損益額 = 5列目（index 4）
      var cell = rows[i].children[4];
      vals.push(toNumber(cell ? cell.textContent : '0'));
    }

    // 勝ち/負け
    var wins = 0, posSum = 0, negSum = 0, net = 0;
    for (var j=0; j<vals.length; j++) {
      var v = vals[j];
      net += v;
      if (v > 0) { wins += 1; posSum += v; }
      if (v < 0) { negSum += v; }
    }

    // 平均
    var avgNet = count ? net / count : 0;
    var avgPos = 0, avgNeg = 0, posCnt = 0, negCnt = 0;
    for (var k=0; k<vals.length; k++) {
      if (vals[k] > 0) posCnt++;
      if (vals[k] < 0) negCnt++;
    }
    avgPos = posCnt ? (posSum / posCnt) : 0;
    avgNeg = negCnt ? (negSum / negCnt) : 0;

    // 1行目（必須3項目）
    sumCountEl.textContent  = String(count);
    winRateEl.textContent   = count ? (Math.round((wins / count) * 100) + '%') : '0%';
    netProfitEl.textContent = fmtInt(net);
    // 色付け
    netProfitEl.classList.remove('profit','loss');
    if (net > 0) netProfitEl.classList.add('profit');
    if (net < 0) netProfitEl.classList.add('loss');

    // 2行目
    if (totalProfitEl) totalProfitEl.textContent = fmtInt(posSum);
    if (totalLossEl)   totalLossEl.textContent   = fmtInt(negSum);

    // 3行目
    if (avgNetEl) {
      avgNetEl.textContent = fmtInt(avgNet);
      avgNetEl.classList.remove('profit','loss');
      if (avgNet > 0) avgNetEl.classList.add('profit');
      if (avgNet < 0) avgNetEl.classList.add('loss');
    }
    if (avgProfitOnlyEl) avgProfitOnlyEl.textContent = fmtInt(avgPos);
    if (avgLossOnlyEl)   avgLossOnlyEl.textContent   = fmtInt(avgNeg);
  }

  // ===== フィルタ適用 =====
  var emptyState = document.getElementById('emptyState');

  function applyFilter() {
    var y = yearFilter ? yearFilter.value : '';
    var m = monthFilter ? monthFilter.value : '';

    var rows = allDataRows();
    for (var i=0; i<rows.length; i++) {
      var row  = rows[i];
      // data-date="YYYY-MM-DD" を持っている（あなたのHTMLは持ってます）
      var date = row.getAttribute('data-date') || '';
      var yy = date.split('-')[0] || '';
      var mm = date.split('-')[1] || '';
      var show = true;
      if (y && yy !== y)   show = false;
      if (m && mm !== m)   show = false;
      row.style.display = show ? '' : 'none';
    }

    // 空メッセージ
    if (emptyState) {
      emptyState.style.display = visibleRows().length ? 'none' : '';
    }

    // フィルタ後の可視行でKPI更新
    updateKPI(visibleRows());
  }

  // クイックフィルタ
  function pad2(n){ return n<10 ? '0'+n : ''+n; }
  function clearActiveChips(){ for (var i=0;i<chips.length;i++) chips[i].classList.remove('active'); }

  for (var i=0; i<chips.length; i++) {
    chips[i].addEventListener('click', function(){
      var now = new Date();
      var y = now.getFullYear();
      var m = now.getMonth()+1;
      var mm = pad2(m);
      var key = this.getAttribute('data-range');

      if (key === 'this-month'){
        if (yearFilter)  yearFilter.value  = String(y);
        if (monthFilter) monthFilter.value = mm;
      } else if (key === 'last-month'){
        var d = new Date(y, m-2, 1);
        if (yearFilter)  yearFilter.value  = String(d.getFullYear());
        if (monthFilter) monthFilter.value = pad2(d.getMonth()+1);
      } else if (key === 'this-year'){
        if (yearFilter)  yearFilter.value  = String(y);
        if (monthFilter) monthFilter.value = '';
      } else {
        if (yearFilter)  yearFilter.value  = '';
        if (monthFilter) monthFilter.value = '';
      }
      clearActiveChips();
      this.classList.add('active');
      applyFilter();
    });
  }

  if (yearFilter)  yearFilter.addEventListener('change', function(){ clearActiveChips(); applyFilter(); });
  if (monthFilter) monthFilter.addEventListener('change', function(){ clearActiveChips(); applyFilter(); });

  // ===== 初期処理 =====
  // 1) まず全データでKPIを表示（ここが一番大事）
  updateKPI(allDataRows());
  // 2) その後、現状のフィルタ状態を反映（未選択なら全件のまま）
  applyFilter();
});