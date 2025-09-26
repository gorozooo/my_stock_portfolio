/* holdings.js v121 — iOS Safari安定版
   - スワイプ固定維持（translateX + .is-open）
   - 詳細/編集/削除 全部動作
   - 「詳細開いた後、カードタップで閉じる」をガード無しで最優先
   - 削除連打防止
   - スパークライン描画同梱
*/
(() => {
  const START_SLOP = 8;
  const THRESHOLD  = 0.35;
  const GUARD_MS   = 280;
  const now = () => Date.now();

  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
      if (row !== except) row.classList.remove('is-open');
    });
  }
  function widthOf(el){
    const r = el.getBoundingClientRect();
    return r.width || parseFloat(getComputedStyle(el).getPropertyValue('--open-w')) || 220;
  }

  function bindRow(row){
    if (!row || row.__bound_v121) return;
    row.__bound_v121 = true;

    const actions   = row.querySelector('.actions');
    const track     = row.querySelector('.track');
    const btnDetail = row.querySelector('[data-action="detail"]');
    const btnDelete = row.querySelector('.item.delete');
    if (!actions || !track) return;

    let guardUntil = 0;

    // アクション内操作は確実に効かせる（HTMXの動作は阻害しない）
    actions.addEventListener('click', e => { e.stopPropagation(); }, {capture:false});
    // ★ iOSでのゴーストタップ抑制（追加）
    actions.addEventListener('touchstart', e => { e.stopPropagation(); }, {passive:true});

    // 詳細トグル：開閉＋パネルは閉じる
    if (btnDetail){
      btnDetail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 削除：二度押し防止
    if (btnDelete){
      btnDelete.addEventListener('click', e=>{
        if (btnDelete.dataset.busy === '1'){
          e.preventDefault();
          return;
        }
        btnDelete.dataset.busy = '1';
        document.body.addEventListener('htmx:afterOnLoad', function onload(){
          btnDelete.dataset.busy = '0';
          document.body.removeEventListener('htmx:afterOnLoad', onload);
        }, {once:true});
        document.body.addEventListener('htmx:responseError', ()=>{
          btnDelete.dataset.busy = '0';
        }, {once:true});
      });
    }

    // ★ カードタップで閉じる（詳細 → 無条件で最優先、パネル → ガードあり）
    track.addEventListener('click', (e)=>{
      // インタラクティブ要素は尊重
      if (e.target.closest('.actions, .item, a, button, input, select, textarea, label')) return;

      // 1) 詳細が開いていれば、ガード無視で閉じる（最優先）
      if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
        return;
      }
      // 2) パネルが開いていれば閉じる（ガードあり）
      if (row.classList.contains('is-open')){
        if (now() < guardUntil) return;
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      }
    });

    // 画面外タップで全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // ===== タッチスワイプ（追従はactions、確定はクラス） =====
    let sx=0, sy=0, dragging=false, horiz=false, baseOpen=false, openPull=0, closePush=0;

    function follow(dist){
      const w = widthOf(actions);
      const clamped = Math.max(-w, Math.min(0, dist));
      actions.style.transition = 'none';
      const pct = 100 + (clamped / w) * 100; // [-w..0] → [0..100]
      actions.style.transform = `translateX(${pct}%)`;
      actions.style.pointerEvents = (pct < 85) ? 'auto' : 'none';
    }
    function snap(open){
      actions.style.transition = '';
      actions.style.transform  = '';
      if (open){
        closeAll(row);
        row.classList.add('is-open');
      }else{
        row.classList.remove('is-open');
      }
      guardUntil = now() + GUARD_MS;
    }

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;
      if (!row.classList.contains('is-open')) closeAll(row);
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY;
      dragging=true; horiz=false; baseOpen=row.classList.contains('is-open');
      openPull=0; closePush=0;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!dragging) return;
      const t=e.touches[0], dx=t.clientX-sx, dy=t.clientY-sy;
      if (!horiz){
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz=true; else { dragging=false; return; }
      }
      e.preventDefault();
      const w = widthOf(actions);
      if (!baseOpen){
        openPull = Math.max(0, -dx);
        follow(-openPull);
      }else{
        closePush = Math.max(0, dx);
        follow(-w + closePush);
      }
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if (!dragging) return; dragging=false;
      const w = widthOf(actions);
      if (!baseOpen){
        snap(openPull > w * THRESHOLD);
      }else{
        const shouldClose = closePush > w * THRESHOLD;
        snap(!shouldClose);
      }
    });
  }

  function bindAll(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  // ===== スパークモーダル =====
const modal = {
  root:null, canvas:null, ctx:null, title:null,
  span:30, mode:'idx', data:null, color:'#22c55e'
};

function openSparkModal(svg){
  modal.root   = document.getElementById('sparkModal');
  modal.canvas = document.getElementById('sparkCanvas');
  modal.title  = document.getElementById('sparkTitle');
  modal.ctx    = modal.canvas.getContext('2d');

  const rate = parseFloat(svg.getAttribute('data-rate')||'0');
  modal.color = (isFinite(rate) && rate<0) ? '#ef4444' : '#22c55e';

  const reads = k => { try{return JSON.parse(svg.getAttribute(k)||'[]');}catch(_){return[];} };
  modal.data = {
    '7':  {idx:reads('data-s7i'),  raw:reads('data-s7r')},
    '30': {idx:reads('data-s30i'), raw:reads('data-s30r')},
    '90': {idx:reads('data-s90i'), raw:reads('data-s90r')}
  };
  modal.span = modal.data['7'].idx?.length ? 7 : (modal.data['30'].idx?.length ? 30 : 90);
  modal.mode = 'idx';
  modal.root.classList.add('is-open');
  renderSparkModal();
}

function renderSparkModal(){
  const d = modal.data[String(modal.span)][modal.mode] || [];
  const cvs=modal.canvas, ctx=modal.ctx;
  cvs.width=cvs.clientWidth; cvs.height=cvs.clientHeight;
  ctx.clearRect(0,0,cvs.width,cvs.height);
  if(d.length<2) return;

  let min=Math.min(...d), max=Math.max(...d);
  if(min===max){min-=1e-6;max+=1e-6;}
  const pad=8,W=cvs.width,H=cvs.height;
  const nx=i=>pad+(i*(W-2*pad)/(d.length-1));
  const ny=v=>H-pad-((v-min)/(max-min))*(H-2*pad);

  ctx.strokeStyle='rgba(255,255,255,.25)';
  ctx.setLineDash([3,3]);ctx.beginPath();
  ctx.moveTo(pad,ny((modal.mode==='idx')?1:(min+(max-min)/2)));
  ctx.lineTo(W-pad,ny((modal.mode==='idx')?1:(min+(max-min)/2)));
  ctx.stroke();ctx.setLineDash([]);

  ctx.strokeStyle=modal.color;ctx.lineWidth=2;
  ctx.beginPath();ctx.moveTo(nx(0),ny(d[0]));
  for(let i=1;i<d.length;i++)ctx.lineTo(nx(i),ny(d[i]));
  ctx.stroke();

  modal.title.textContent=`${modal.span}日 / ${modal.mode==='idx'?'指数':'実値'}`;
}

function bindSparkModal(){
  const root=document.getElementById('sparkModal');
  if(!root) return;
  root.addEventListener('click',e=>{
    if(e.target.id==='sparkClose'||e.target.id==='sparkModal') root.classList.remove('is-open');
  });
  root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(b=>b.classList.remove('is-on'));
      btn.classList.add('is-on');
      modal.span=parseInt(btn.getAttribute('data-span'),10);
      renderSparkModal();
    });
  });
  root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(b=>b.classList.remove('is-on'));
      btn.classList.add('is-on');
      modal.mode=btn.getAttribute('data-mode');
      renderSparkModal();
    });
  });
  document.addEventListener('click',e=>{
    const svg=e.target.closest&&e.target.closest('svg.spark');
    if(svg) openSparkModal(svg);
  });
}

// boot の最後に追加
function boot(){ bindAll(); drawAllSparks(); bindSparkModal(); }

  console.log('[holdings.js v121-fix] ready');
})();