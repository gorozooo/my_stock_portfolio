// policy.js r7-fix — attack/defenseを維持・チップ操作そのまま・おまかせ時のみAIバナー表示
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

// ---- Toast（スマホ下タブ回避）
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

// ---- API
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

// ---- ラベル（命名は attack/normal/defense/auto を維持）
const riskLabel  = (v)=> ({attack:'攻め', normal:'普通', defense:'守り', auto:'おまかせ'})[v] ?? v;
const styleLabel = (v)=> ({short:'短期', mid:'中期', long:'長期', auto:'おまかせ'})[v] ?? v;

// ---- チップ選択UI（r7のまま：委譲でクリック拾う）
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
    // 手動で選んだ瞬間に「運用中：◯×◯」を即反映（AIバナーはおまかせ時だけ）
    const risk = getPressed($('#riskChips'))  || 'normal';
    const hold = getPressed($('#styleChips')) || 'mid';
    updateBanner(null, {risk, hold});
  });
}

// ---- バナー／運用中表示（ここが修正点）
function updateBanner(bannerText, opts){
  const aiBanner = $('#aiBanner');
  const running  = `${riskLabel(opts.risk)} × ${styleLabel(opts.hold)}モード`;

  // おまかせ（どちらかが auto）時だけAIバナーを表示
  const showAIBanner = (opts.risk === 'auto' || opts.hold === 'auto' || !!bannerText);
  if (aiBanner){
    aiBanner.hidden = !showAIBanner;
  }
  // バナー内の表示は存在する時だけ更新
  const rm = $('#runningMode'); if (rm) rm.textContent = running;

  // もう一方（常時見える方）があれば更新（存在チェック付き）
  const rmAlt = $('#runningModeAlt'); if (rmAlt) rmAlt.textContent = running;
}

// ---- 初期化（r7のまま）
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);
    updateBanner(js.banner || null, {risk: js.current.risk_mode, hold: js.current.hold_style});

    // 保存
    $('#saveBtn').addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res  = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res.banner || null, {risk: res.current.risk_mode, hold: res.current.hold_style});
        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // リセット（普通×中期）
    $('#resetBtn').addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res.banner || null, {risk: res.current.risk_mode, hold: res.current.hold_style});
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();