// 계산 엔진: Pyodide(브라우저 안 파이썬) + HiGHS 로 plan.py·optimize.py 를 그대로 돌림.
// 화면이 멈추지 않도록 워커에서 실행. bridge.js 와 메시지로 주고받음
// 최신 Pyodide 는 모듈 워커만 지원 (bridge.js 에서 { type: 'module' } 로 띄움)
import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';

const APP = '/home/pyodide/app';
let handle = null, manualText = null;
const progress = text => self.postMessage({ type: 'progress', text });

async function init({ bundle, manual }) {
  progress('파이썬 불러오는 중…');
  const py = await loadPyodide({ indexURL: PYODIDE });
  progress('최적화 솔버(HiGHS) 불러오는 중…');
  await py.loadPackage(['numpy', 'highspy']);
  // networkx 는 순수 파이썬인데 패키지 목록상 matplotlib 까지 끌고 와서, 휠만 따로 받음
  await py.loadPackage(PYODIDE + 'networkx-3.6.1-py3-none-any.whl');
  progress('게임 데이터 불러오는 중…');
  const buf = await (await fetch(bundle)).arrayBuffer();
  py.FS.mkdirTree(APP);
  py.unpackArchive(buf, 'zip', { extractDir: APP });
  if (manual) py.FS.writeFile(APP + '/data/manual_recipes.json', manual);
  progress('데이터 정리하는 중…');
  py.runPython(`import os, sys; sys.path.insert(0, "${APP}"); os.chdir("${APP}")`);
  const web = py.pyimport('webapi');
  handle = (method, path, body) => web.handle(method, path, body).toJs();
  manualText = () => web.manual_text();
}

let ready = null;
self.onmessage = async ({ data: m }) => {
  if (m.type === 'init') {
    ready = init(m).then(() => self.postMessage({ type: 'ready' }),
      err => self.postMessage({ type: 'failed', text: String(err && err.message || err) }));
    return;
  }
  try {
    await ready;
    const [status, text] = handle(m.method, m.path, m.body === undefined ? null : JSON.stringify(m.body));
    const reply = { type: 'reply', id: m.id, status, text };
    if (m.path.startsWith('/api/manual_recipes') && m.method !== 'GET') reply.manual = manualText();
    self.postMessage(reply);
  } catch (err) {
    self.postMessage({ type: 'reply', id: m.id, status: 500, text: JSON.stringify({ error: String(err && err.message || err).slice(-400) }) });
  }
};
