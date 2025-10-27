/* policy.js v2025-10-27 r3
   - 手動保存時に 🧠バナーが消えない問題を修正
   - hidden 属性と style.display の両方で確実にトグル
   - 値が欠けても落ちないようにガード
*/
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* ---- Toast（スマホ下タブ回避） ---- */
function computeToastBottomPx(){
  let insetBottom = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140;
}
function toast(msg){
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  t.style.bottom = computeToastBottomPx()+'px';
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity = '1');
  const onV = ()=> t.style.bottom = computeToastBottomPx()+'px';
  if (window.visualViewport) window.visualViewport.addEventListener('resize', onV);
  setTimeout(()=>{ t.style.opacity='0'; setTimeout(()=>{ if(window.visualViewport) window.visualViewport.removeEventListener('resize', onV); t.remove(); }, 250); }, 1800);
}

/* ---- API ---- */
async function getJSON(url){
  const r = await fetch(abs(url), {headers:{'Cache-Control':'no-store'}});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}
async function postJSON(url, body){
  const r = await fetch(abs(url), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}

/* ---- UI処理 ---- */
function setPressed(container, value){
  if(!container) return;
  container.querySelectorAll('.chip').forEach(btn=>{
    const on = btn.dataset.val === value;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function getPressed(container){
  if(!container) return null;
  const el = container.querySelector('.chip[aria-pressed="true"]');
  return el ? el.dataset.val : null;
}
function wireChips(container){
  if(!container) return;
  container.addEventListener('click', (e)=>{
    const btn = e.target.closest('.chip'); if(!btn) return;
    setPressed(container, btn.dataset.val);
  });
}

/* ---- バナー反映（確実に表示/非表示を切替） ---- */
function updateBanner(bannerText, resolvedLabels){
  const aiBanner = $('#aiBanner');
  const running  = $('#runningMode');

  if (resolvedLabels && running){
    running.textContent = `${resolvedLabels.risk} × ${resolvedLabels.style}モード`;
  }

  const show = !!bannerText;               // 文字列が入っている時だけ表示
  if (aiBanner){
    aiBanner.toggleAttribute('hidden', !show);   // hidden を確実に付け外し
    aiBanner.style.display = show ? '' : 'none'; // CSSに勝つため二重で効かせる
  }
}

/* ---- 初期化 ---- */
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current?.risk_mode);
    setPressed($('#styleChips'), js.current?.hold_style);
    updateBanner(js.banner, js.resolved?.labels);

    // 保存
    $('#saveBtn')?.addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res  = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });

        setPressed($('#riskChips'),  res.current?.risk_mode);
        setPressed($('#styleChips'), res.current?.hold_style);
        updateBanner(res.banner, res.resolved?.labels);
        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // リセット（既定: 普通 × 中期）
    $('#resetBtn')?.addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current?.risk_mode);
        setPressed($('#styleChips'), res.current?.hold_style);
        updateBanner(res.banner, res.resolved?.labels);
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();