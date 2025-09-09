// ユーティリティ
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
const parseCSV = (s) => (s || "").split(",").map(x => parseFloat(x)).filter(x => !isNaN(x));

// ===== リング（総資産進捗） =====
(function ringGauge(){
  const el = document.querySelector(".ring");
  if(!el) return;
  const val = parseFloat(el.dataset.value || "0");
  const max = parseFloat(el.dataset.max || "0");
  const circ = 2 * Math.PI * 50;            // r=50
  const fg = el.querySelector(".fg");
  let ratio = 1;
  if(max > 0) ratio = clamp(val / max, 0, 1);
  fg.style.strokeDasharray = `${circ}`;
  fg.style.strokeDashoffset = `${circ * (1 - ratio)}`;
})();

// ===== スパークライン描画（軽量SVG） =====
(function spark(){
  const host = document.getElementById("assetSpark");
  if(!host) return;
  const points = parseCSV(host.dataset.points);
  const w = host.clientWidth || 320, h = host.clientHeight || 60;
  if(points.length === 0){
    host.innerHTML = '';
    return;
  }
  const min = Math.min(...points), max = Math.max(...points);
  const norm = (v) => (max === min) ? h/2 : h - ( (v - min) / (max - min) ) * (h - 8) - 4;

  let d = `M 0 ${norm(points[0])}`;
  points.forEach((p, i) => {
    const x = i * (w / (points.length - 1));
    d += ` L ${x} ${norm(p)}`;
  });

  host.innerHTML = `
    <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <path d="${d}" fill="none" stroke="rgba(96,165,250,.95)" stroke-width="2" />
    </svg>
  `;
})();

// ===== ゲージバー（実現損益） =====
(function gauges(){
  document.querySelectorAll(".gauge").forEach(g => {
    const val = parseFloat(g.dataset.val || "0");
    const span = g.querySelector(".g-bar > span");
    if(!span) return;
    // ±それぞれの最大幅を仮想的に 100 万円で正規化（必要なら調整）
    const unit = 1_000_000;
    const ratio = clamp(Math.abs(val) / unit, 0, 1);
    span.style.width = `${ratio * 100}%`;
  });
})();

// ===== 最近のアクティビティ：クイックフィルタ（クライアント側で擬似絞り込み） =====
(function activityFilter(){
  const list = document.getElementById("timeline");
  if(!list) return;
  const chips = document.querySelectorAll(".activity .chips .chip");
  const rows = Array.from(list.querySelectorAll(".chiprow"));

  function setActive(btn){
    chips.forEach(c => c.classList.toggle('active', c === btn));
  }

  function filter(range){
    const now = new Date();
    let cutoff = null;
    if(range !== 'all'){
      const days = parseInt(range, 10) || 7;
      cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days);
    }
    rows.forEach(row => {
      const dateEl = row.querySelector('.cdate');
      if(!dateEl){ row.style.display = ''; return; }
      // "n/j" を Date に概算変換（年は今年想定）
      const txt = dateEl.textContent.trim(); // e.g., "9/1"
      const parts = txt.split('/');
      let show = true;
      if(cutoff && parts.length === 2){
        const m = parseInt(parts[0],10), d = parseInt(parts[1],10);
        const dt = new Date(now.getFullYear(), m - 1, d);
        show = dt >= cutoff;
      }
      row.style.display = show ? '' : 'none';
    });
  }

  chips.forEach(btn => {
    btn.addEventListener('click', () => {
      setActive(btn);
      filter(btn.dataset.range || '7');
    });
  });

  // 初期
  filter(document.querySelector('.activity .chip.active')?.dataset.range || '7');
})();