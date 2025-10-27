const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* ===== Toast（スマホ下タブ回避） ===== */
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

/* ===== API ===== */
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

/* ===== ラベル ===== */
const riskLabel  = (v)=>({attack:'攻め', normal:'普通', defense:'守り', auto:'おまかせ'})[v] ?? v;
const styleLabel = (v)=>({short:'短期', mid:'中期', long:'長期', auto:'おまかせ'})[v] ?? v;

/* ===== チップUI ===== */
function setPressed(container, value){
  container.querySelectorAll('.chip').forEach(btn=>{
    const on = btn.dataset.val === value;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function getPressed(container){
  const el = container.querySelector('.chip[aria-pressed="true"]');
  return el ? el.dataset.val : null;
}
function wireChips(container){
  container.addEventListener('click', (e)=>{
    const btn = e.target.closest('.chip'); if(!btn) return;
    setPressed(container, btn.dataset.val);

    // 手動選択の見た目を即時反映（サーバ保存前の暫定表示）
    const risk = getPressed($('#riskChips'))  || 'normal';
    const hold = getPressed($('#styleChips')) || 'mid';
    updateBanner({
      banner: null,
      current: { risk_mode: risk, hold_style: hold },
      resolved: { effective: null } // 手動時はAI解決なし
    });
  });
}

/* ===== 表示更新（AIおまかせの“実モード”を優先使用） =====
   data 期待形:
   {
     banner: "…"(任意),
     current: { risk_mode: 'attack|normal|defense|auto', hold_style: 'short|mid|long|auto' },
     resolved: { effective: { risk_mode: 'attack|normal|defense', hold_style: 'short|mid|long' } | null }
   }
*/
function updateBanner(data){
  const aiBanner = $('#aiBanner');
  const curRisk  = data.current?.risk_mode;
  const curHold  = data.current?.hold_style;

  const hasAuto  = (curRisk === 'auto' || curHold === 'auto');

  // おまかせが含まれる場合は resolved.effective を優先
  const eff = (hasAuto && data.resolved && data.resolved.effective)
                ? data.resolved.effective
                : { risk_mode: curRisk, hold_style: curHold };

  const runningTxt = `${riskLabel(eff.risk_mode)} × ${styleLabel(eff.hold_style)}モード`;

  // バナー表示条件：おまかせを含む or 明示的な banner あり
  if (hasAuto || !!data.banner){
    aiBanner.hidden = false;
    $('#runningMode').textContent = runningTxt;
  }else{
    aiBanner.hidden = true;
  }

  // ページ内の常時表示（下の「運用中：…」行）
  const alt = $('#runningModeAlt');
  if (alt) alt.textContent = runningTxt;
}

/* ===== 初期化 ===== */
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    // 現在値を取得して反映
    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);
    updateBanner(js);

    // 保存
    $('#saveBtn').addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res  = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res);
        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // リセット（既定：普通×中期）
    $('#resetBtn').addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res);
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();