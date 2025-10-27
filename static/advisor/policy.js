// policy.js r8 — 「おまかせ」時はAIが決めた具体モードを表示
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

// ---- ラベル（既存命名を尊重）
const riskLabel  = (v)=> ({attack:'攻め', normal:'普通', defense:'守り', auto:'おまかせ'})[v] ?? v;
const styleLabel = (v)=> ({short:'短期', mid:'中期', long:'長期',     auto:'おまかせ'})[v] ?? v;

// ---- チップ選択UI
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

    // 手動選択の瞬間から「運用中：…」は即時更新
    const current = {
      risk_mode:  getPressed($('#riskChips'))  || 'normal',
      hold_style: getPressed($('#styleChips')) || 'mid',
    };
    // 手動の段階では resolved が無いので current の表示でOK（バナーは手動なら非表示）
    updateBanner(null, current, null);
  });
}

// ---- バナー／運用中表示
// bannerText: 文字列 or null
// current: {risk_mode, hold_style}
// resolved: {effective:{risk_mode, hold_style}, labels:{risk, hold}} or null
function updateBanner(bannerText, current, resolved){
  const aiBanner = $('#aiBanner');
  const hasAuto = (current?.risk_mode === 'auto' || current?.hold_style === 'auto');

  // 実際に表示するモード（おまかせ時は resolved を優先）
  const effRisk = hasAuto ? (resolved?.effective?.risk_mode  || current?.risk_mode  || 'normal')
                          : (current?.risk_mode  || 'normal');
  const effHold = hasAuto ? (resolved?.effective?.hold_style || current?.hold_style || 'mid')
                          : (current?.hold_style || 'mid');

  // ラベル（resolved.labels が来ていればそれを優先）
  const riskText  = hasAuto ? (resolved?.labels?.risk  || riskLabel(effRisk)) : riskLabel(effRisk);
  const styleText = hasAuto ? (resolved?.labels?.hold  || styleLabel(effHold)) : styleLabel(effHold);
  const running   = `${riskText} × ${styleText}モード`;

  // おまかせが含まれるときだけ AIバナー表示
  if (hasAuto || bannerText){
    aiBanner.hidden = false;
    $('#runningMode').textContent = running;
  }else{
    aiBanner.hidden = true;
  }

  // 常時見える方（テンプレ側に <b id="runningModeAlt"> がある場合）
  const alt = $('#runningModeAlt');
  if (alt) alt.textContent = running;
}

// ---- 初期化
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    // 現在値取得
    // 期待する応答例：
    // {
    //   current: { risk_mode: 'auto'|'attack'|'normal'|'defense', hold_style: 'auto'|'short'|'mid'|'long' },
    //   resolved: {
    //     effective: { risk_mode: 'attack'|'normal'|'defense', hold_style: 'short'|'mid'|'long' },
    //     labels:    { risk:'攻め', hold:'短期' }
    //   },
    //   banner: "任意の説明文 or 空"
    // }
    const js = await getJSON('/advisor/api/policy/');

    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);
    updateBanner(js.banner || null, js.current, js.resolved || null);

    // 保存
    $('#saveBtn').addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res  = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });

        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res.banner || null, res.current, res.resolved || null);

        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // リセット（普通×中期）
    $('#resetBtn').addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res.banner || null, res.current, res.resolved || null);
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();