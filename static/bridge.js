// 정적 사이트용: 화면(index.html)의 서버 요청을 브라우저 안 계산 엔진(worker.js)으로 돌림.
// index.html 은 window.PLANNER_API 가 있으면 fetch 대신 이걸 씀
(() => {
  const me = document.currentScript, V = me.dataset.v, MANUAL_KEY = 'bdo-planner-manual';
  const lsGet = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* 저장 불가(사생활 보호 모드 등) */ } };

  // 처음 열 때 엔진 준비 상태 안내 (파이썬·솔버를 받는 동안)
  const bar = document.createElement('div');
  bar.setAttribute('role', 'status');
  bar.style.cssText = 'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:50;max-width:calc(100% - 32px);' +
    'padding:10px 16px;border-radius:10px;background:var(--panel);color:var(--ink);border:1px solid var(--line);' +
    'box-shadow:0 4px 16px rgba(0,0,0,.15);font-size:13px;line-height:1.5';
  const show = html => { bar.innerHTML = html; if (!bar.isConnected) document.body.appendChild(bar); };
  const first = !lsGet('bdo-planner-engine-ok');
  const hint = first ? '<br><small style="color:var(--muted)">처음 한 번은 계산 엔진(약 30MB)을 받느라 1분쯤 걸릴 수 있습니다. 다음부터는 빠릅니다.</small>' : '';
  document.addEventListener('DOMContentLoaded', () => { if (!done) show(`계산 엔진 준비 중…${hint}`); });

  const worker = new Worker('worker.js?v=' + V, { type: 'module' });
  const waiting = new Map();
  let seq = 0, done = false, failed = null, readyResolve;
  const ready = new Promise(r => { readyResolve = r; });
  worker.onmessage = ({ data: m }) => {
    if (m.type === 'progress') show(`계산 엔진 준비 중 · ${m.text}${hint}`);
    else if (m.type === 'ready') { done = true; bar.remove(); lsSet('bdo-planner-engine-ok', '1'); readyResolve(); }
    else if (m.type === 'failed') {
      done = true; failed = m.text; readyResolve();
      show(`<b style="color:var(--bad)">계산 엔진을 불러오지 못했습니다.</b> 새로고침해 보세요.<br><small style="color:var(--muted)">${m.text.replace(/</g, '&lt;').slice(0, 200)}</small>`);
    } else if (m.type === 'reply') {
      if (m.manual) lsSet(MANUAL_KEY, m.manual);
      const w = waiting.get(m.id); waiting.delete(m.id);
      let data; try { data = JSON.parse(m.text); } catch { data = { error: '결과를 읽지 못했습니다' }; }
      w({ ok: m.status < 400, status: m.status, data });
    }
  };
  worker.onerror = e => { if (!done) worker.onmessage({ data: { type: 'failed', text: e.message || '워커 오류' } }); };
  worker.postMessage({ type: 'init', bundle: 'bundle.zip?v=' + V, manual: lsGet(MANUAL_KEY) });

  window.PLANNER_API = {
    async call(method, path, body) {
      await ready;
      if (failed) throw new Error('계산 엔진 오류');
      const id = ++seq;
      return new Promise(res => { waiting.set(id, res); worker.postMessage({ id, method, path, body }); });
    },
  };
})();
