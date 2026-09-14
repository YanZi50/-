/* ============================================================
   网络层：后端 API 统一封装
   ============================================================ */

async function api(path, options = {}) {
  const resp = await fetch(path, options);
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return resp.json();
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp;
}
