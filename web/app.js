/* ============================================================
   信息流素材一键拼接 · 应用逻辑（Vue3 本地构建，无外部依赖）
   ============================================================ */
const { createApp, reactive, ref, computed, onMounted } = Vue;

/* ---------- API ---------- */
async function api(path, options = {}) {
  const resp = await fetch(path, options);
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return resp.json();
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp;
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

const transitionOptions = [
  { value: 'fade', label: '淡入淡出' },
  { value: 'dissolve', label: '溶解' },
  { value: 'slideleft', label: '左滑' },
  { value: 'slideright', label: '右滑' },
  { value: 'slideup', label: '上滑' },
  { value: 'slidedown', label: '下滑' },
  { value: 'wipeleft', label: '左擦除' },
  { value: 'wiperight', label: '右擦除' },
  { value: 'wipeup', label: '上擦除' },
  { value: 'wipedown', label: '下擦除' },
  { value: 'circleopen', label: '圆形打开' },
  { value: 'circleclose', label: '圆形关闭' },
  { value: 'circlecrop', label: '圆形裁剪' },
  { value: 'smoothleft', label: '平滑左移' },
  { value: 'smoothright', label: '平滑右移' },
  { value: 'smoothup', label: '平滑上移' },
  { value: 'smoothdown', label: '平滑下移' },
  { value: 'diagtl', label: '对角(左上)' },
  { value: 'diagtr', label: '对角(右上)' },
  { value: 'diagbl', label: '对角(左下)' },
  { value: 'diagbr', label: '对角(右下)' },
  { value: 'zoomin', label: '放大进入' },
  { value: 'pixelize', label: '像素化' },
  { value: 'fadeblack', label: '黑场淡变' },
  { value: 'fadewhite', label: '白场淡变' },
  { value: 'coverleft', label: '左覆盖' },
  { value: 'coverright', label: '右覆盖' },
  { value: 'coverup', label: '上覆盖' },
  { value: 'coverdown', label: '下覆盖' },
  { value: 'revealleft', label: '左揭示' },
  { value: 'revealright', label: '右揭示' },
  { value: 'revealup', label: '上揭示' },
  { value: 'revealdown', label: '下揭示' },
  { value: 'vertopen', label: '垂直打开' },
  { value: 'vertclose', label: '垂直关闭' },
  { value: 'horzopen', label: '水平打开' },
  { value: 'horzclose', label: '水平关闭' },
  { value: 'squeezeh', label: '水平挤压' },
  { value: 'squeezev', label: '垂直挤压' },
];

/* ---------- 全局状态 ---------- */
const state = reactive({
  step: 1,
  theme: localStorage.getItem('sppj_theme') || 'dark',
  serverOk: true,
  selectBusy: false,
  msg: { text: '', type: 'info' },
  folders: { head: '', tail: '', middle: '', bgm: '', output: '' },
  materials: { head: [], tail: [], middle: [], bgm: [] },
  fixed: { head: '', tail: '', middle: '', bgm: '' },
  middlePools: [{ id: 1, folder: '', items: [], count: 1, files: [], expanded: false, shown: 24 }],
  zoneExpanded: { head: false, tail: false, middle: false, bgm: false },
  zoneShown: { head: 24, tail: 24, middle: 24, bgm: 24 },
  params: {
    count: 10,
    workers: 2,
    resolution: '1080x1920',
    duration_mode: '不限制',
    output_name_template: 'output_{序号}_{开头}_{结尾}',
    dedupe_enabled: true,
    random_seed: 20260905,
    transition_mode: '不使用',
    transition_type: 'fade',
    transition_duration: 0.5,
    transition_types: transitionOptions.map((t) => t.value),
    bgm_mode: '不使用',
    bgm_volume: 0.2,
    bgm_fade: false,
    bgm_ducking: false,
    fixed_bgm: '',
    use_watermark: false,
    watermark_path: '',
    watermark_mode: '铺满全屏',
    watermark_position: '右下角',
    watermark_scale: 0.15,
    watermark_opacity: 0.6,
    normalize_audio: false,
    use_subtitle: false,
    fit_mode: 'fit',
  },
  presets: {},
  presetName: '',
  templateName: '',
  selectedTemplate: '',
  templates: [],
  history: [],
  precheckBad: [],
  job: {
    running: false, paused: false, done: false,
    current: 0, total: 0, success: 0, failed: 0, skipped: 0, cancelled: false,
    eta: null, speed: null, error: null, logs: [],
    success_items: [], failed_items: [],
  },
  previewUrl: '',
  outputFolder: '',
  outputFiles: [],
  materialZones: [
    { kind: 'head', title: '开头素材库', hint: '片头视频', placeholder: '例如：D:\\素材\\开头', uploadable: true },
    { kind: 'tail', title: '结尾素材库', hint: '片尾视频', placeholder: '例如：D:\\素材\\结尾', uploadable: true },
    { kind: 'bgm', title: '背景音乐', hint: '可选', placeholder: '可选：音乐文件夹', uploadable: false },
  ],
});

function showMsg(text, type = 'info') {
  state.msg = { text, type };
  clearTimeout(state.msg.timer);
  state.msg.timer = setTimeout(() => { state.msg.text = ''; }, 6000);
}

/* ---------- 页面标题 ---------- */
const pageTitle = computed(() => ['', '素材库', '参数配置', '生成与结果'][state.step]);
const pageDesc = computed(() => ({
  1: '选择素材文件夹，点击卡片设置固定片头/片尾',
  2: '配置分辨率、时长、转场、BGM、水印等参数',
  3: '预检素材、批量生成、查看结果与历史',
}[state.step]));

/* ---------- 水印滑块 ---------- */
const watermarkScalePct = computed({
  get: () => Math.round(state.params.watermark_scale * 100),
  set: (v) => { state.params.watermark_scale = v / 100; },
});
const watermarkOpacityPct = computed({
  get: () => Math.round(state.params.watermark_opacity * 100),
  set: (v) => { state.params.watermark_opacity = v / 100; },
});

/* ---------- 进度与结果 ---------- */
const progressPct = computed(() => {
  if (!state.job.total) return 0;
  return Math.min(100, Math.round((state.job.current / state.job.total) * 100));
});
const logHtml = computed(() => escapeHtml(state.job.logs.join('\n')));
const poolPickedCount = computed(() => state.middlePools.reduce((s, p) => s + p.items.length, 0));
const resultRows = computed(() => {
  const rows = [];
  (state.job.success_items || []).forEach((it) => rows.push({
    index: it.index, head: it.head, middle: it.middle, tail: it.tail,
    ok: true, downloadUrl: makeDownloadUrl(it.output), error: '',
  }));
  (state.job.failed_items || []).forEach((it) => rows.push({
    index: it.index, head: it.head, middle: it.middle, tail: it.tail,
    ok: false, downloadUrl: '', error: it.error,
  }));
  return rows.sort((a, b) => a.index - b.index);
});

function makeDownloadUrl(outputPath) {
  const m = String(outputPath).match(/^(.+)[\\/]([^\\/]+)$/);
  if (!m) return '#';
  return '/api/download?folder=' + encodeURIComponent(m[1]) + '&name=' + encodeURIComponent(m[2]);
}
function fileName(path) {
  return String(path || '').split(/[\\/]/).pop() || '—';
}
function shortError(err) {
  const s = String(err || '');
  return s.length > 36 ? s.slice(0, 36) + '…' : s;
}
function fmtEta(seconds) {
  const s = Math.max(1, Math.round(seconds));
  if (s < 60) return s + ' 秒';
  if (s < 3600) return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
  return Math.floor(s / 3600) + ' 时 ' + Math.floor((s % 3600) / 60) + ' 分';
}

/* ---------- 主题 ---------- */
function toggleTheme() {
  state.theme = state.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('sppj_theme', state.theme);
  document.documentElement.dataset.theme = state.theme;
}

/* ---------- 配置收集与回填 ---------- */
function collectConfig() {
  const activePools = state.middlePools
    .filter((p) => p.folder && p.folder.trim())
    .map((p) => ({ folder: p.folder.trim(), items: p.items.slice(), count: p.count }));
  return {
    head_folder: state.folders.head.trim(),
    tail_folder: state.folders.tail.trim(),
    middle_folder: activePools.length ? activePools[0].folder : state.folders.middle.trim(),
    output_folder: state.folders.output.trim(),
    fixed_head: state.fixed.head,
    fixed_tail: state.fixed.tail,
    fixed_middle: activePools.length ? activePools[0].items[0] || '' : '',
    middle_items: activePools.length ? activePools[0].items : [],
    middle_count: activePools.length ? activePools[0].count : 0,
    middle_pools: activePools,
    ...state.params,
    bgm_folder: state.folders.bgm.trim(),
  };
}

function applyConfig(cfg) {
  if (!cfg) return;
  state.folders.head = cfg.head_folder || '';
  state.folders.tail = cfg.tail_folder || '';
  state.folders.middle = cfg.middle_folder || '';
  state.folders.bgm = cfg.bgm_folder || '';
  state.folders.output = cfg.output_folder || '';
  state.fixed.head = cfg.fixed_head || '';
  state.fixed.tail = cfg.fixed_tail || '';
  state.fixed.middle = cfg.fixed_middle || '';
  // 中间素材池：优先多池结构，否则回退旧单池字段
  if (Array.isArray(cfg.middle_pools) && cfg.middle_pools.length) {
    state.middlePools = cfg.middle_pools.slice(0, 5).map((pool, i) => ({
      id: i + 1,
      folder: pool.folder || '',
      items: Array.isArray(pool.items) ? pool.items.slice() : [],
      count: pool.count ?? 1,
      files: [], expanded: false, shown: 24,
    }));
  } else {
    const legacyItems = Array.isArray(cfg.middle_items) ? cfg.middle_items.slice() : [];
    if (!legacyItems.length && cfg.fixed_middle) legacyItems.push(cfg.fixed_middle);
    const legacyCount = cfg.middle_count ?? 1;
    state.middlePools = [{ id: 1, folder: cfg.middle_folder || '', items: legacyItems, count: legacyCount, files: [], expanded: false, shown: 24 }];
  }
  const p = state.params;
  p.count = cfg.count ?? 10;
  p.workers = cfg.workers ?? 2;
  p.resolution = cfg.resolution || '1080x1920';
  p.duration_mode = cfg.duration_mode || '不限制';
  p.output_name_template = cfg.output_name_template || 'output_{序号}_{开头}_{结尾}';
  p.dedupe_enabled = cfg.dedupe_enabled !== false;
  p.random_seed = cfg.random_seed || 20260905;
  p.transition_mode = cfg.transition_mode || '不使用';
  p.transition_type = cfg.transition_type || 'fade';
  p.transition_duration = cfg.transition_duration || 0.5;
  p.transition_types = cfg.transition_types?.length ? cfg.transition_types : transitionOptions.map((t) => t.value);
  p.bgm_mode = cfg.bgm_mode || '不使用';
  p.bgm_volume = cfg.bgm_volume ?? 0.2;
  p.bgm_fade = !!cfg.bgm_fade;
  p.bgm_ducking = !!cfg.bgm_ducking;
  p.fixed_bgm = cfg.fixed_bgm || '';
  p.use_watermark = !!cfg.use_watermark;
  p.watermark_path = cfg.watermark_path || '';
  p.watermark_mode = cfg.watermark_mode || '铺满全屏';
  p.watermark_position = cfg.watermark_position || '右下角';
  p.watermark_scale = cfg.watermark_scale ?? 0.15;
  p.watermark_opacity = cfg.watermark_opacity ?? 0.6;
  p.normalize_audio = !!cfg.normalize_audio;
  p.use_subtitle = !!cfg.use_subtitle;
  ['head', 'tail', 'middle', 'bgm'].forEach((k) => scan(k));
}

/* ---------- 素材扫描与详情 ---------- */
async function scan(kind) {
  if (kind === 'middle') {
    state.middlePools.forEach((pool) => scanPool(pool));
    return;
  }
  const folder = state.folders[kind].trim();
  if (!folder) { state.materials[kind] = []; return; }
  try {
    const q = new URLSearchParams({ [kind]: folder });
    const data = await api('/api/scan?' + q.toString());
    const files = data[kind] || [];
    // 只处理新增文件，避免重复请求详情
    const existing = new Map(state.materials[kind].map((m) => [m.path, m]));
    const newFiles = files.filter((f) => !existing.has(f.path));
    const enriched = await enrichMaterials(newFiles);
    state.materials[kind] = files.map((f) => existing.get(f.path) || enriched.find((e) => e.path === f.path) || {
      path: f.path, name: f.name, ok: true, thumbUrl: thumbUrl(f.path),
    });
    if (kind === 'head' || kind === 'tail') syncFixed(kind);
  } catch (e) {
    showMsg('素材扫描失败：' + e.message, 'error');
  }
}

async function scanPool(pool) {
  const folder = (pool.folder || '').trim();
  if (!folder) { pool.files = []; return; }
  try {
    const q = new URLSearchParams({ middle: folder });
    const data = await api('/api/scan?' + q.toString());
    const files = data.middle || [];
    const existing = new Map(pool.files.map((m) => [m.path, m]));
    const newFiles = files.filter((f) => !existing.has(f.path));
    const enriched = await enrichMaterials(newFiles);
    pool.files = files.map((f) => existing.get(f.path) || enriched.find((e) => e.path === f.path) || {
      path: f.path, name: f.name, ok: true, thumbUrl: thumbUrl(f.path),
    });
    // 过滤已失效的勾选项
    const valid = new Set(pool.files.map((m) => m.path));
    pool.items = pool.items.filter((p) => valid.has(p));
  } catch (e) {
    showMsg('中间素材池扫描失败：' + e.message, 'error');
  }
}

function addMiddlePool() {
  if (state.middlePools.length >= 5) { showMsg('最多添加 5 个中间素材池', 'error'); return; }
  const nextId = state.middlePools.reduce((m, p) => Math.max(m, p.id), 0) + 1;
  state.middlePools.push({ id: nextId, folder: '', items: [], count: 1, files: [], expanded: false, shown: 24 });
  showMsg(`已添加中间素材池 ${nextId} 号`);
}

function removeMiddlePool(pool) {
  if (state.middlePools.length <= 1) { showMsg('至少保留 1 个中间素材池', 'error'); return; }
  state.middlePools = state.middlePools.filter((p) => p.id !== pool.id);
  showMsg('已移除中间素材池');
}

function togglePoolItem(pool, path) {
  const i = pool.items.indexOf(path);
  if (i >= 0) {
    pool.items.splice(i, 1);
  } else {
    if (pool.items.length >= 10) { showMsg('单个池固定素材最多 10 条', 'error'); return; }
    pool.items.push(path);
  }
}

function poolOrder(pool, path) {
  const i = pool.items.indexOf(path);
  return i >= 0 ? i + 1 : 0;
}

function togglePoolExpand(pool) {
  pool.expanded = !pool.expanded;
}
function showPoolMore(pool) {
  pool.shown += 24;
}

function thumbUrl(path) {
  return '/api/thumb?path=' + encodeURIComponent(path);
}

async function enrichMaterials(files, concurrency = 6) {
  const out = new Array(files.length);
  let next = 0;
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= files.length) return;
      const f = files[i];
      try {
        const d = await api('/api/material_detail?path=' + encodeURIComponent(f.path));
        const ok = d.ok && d.has_video;
        out[i] = {
          path: f.path, name: f.name, ok,
          thumbUrl: thumbUrl(f.path),
          duration: d.duration || 0,
          width: d.width || 0, height: d.height || 0,
          landscape: (d.width || 0) >= (d.height || 0),
          durationText: fmtDuration(d.duration),
          resText: d.width && d.height ? d.width + '×' + d.height : '',
        };
      } catch (e) {
        out[i] = { path: f.path, name: f.name, ok: false, thumbUrl: thumbUrl(f.path), durationText: '未知', resText: '' };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, files.length) }, worker));
  return out.filter(Boolean);
}

function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + 'm' + (s < 10 ? '0' : '') + s + 's';
}

function syncFixed(kind) {
  const paths = state.materials[kind].map((m) => m.path);
  if (state.fixed[kind] && !paths.includes(state.fixed[kind])) state.fixed[kind] = '';
}

function toggleFixed(kind, path) {
  if (!['head', 'tail'].includes(kind)) return;
  state.fixed[kind] = state.fixed[kind] === path ? '' : path;
  const name = materialsName(kind);
  showMsg(state.fixed[kind] ? `已固定${name}` : `已取消固定${name}`);
}

function toggleExpand(kind) {
  state.zoneExpanded[kind] = !state.zoneExpanded[kind];
}
function showMore(kind) {
  state.zoneShown[kind] += 24;
}
function expandAll() {
  ['head', 'tail', 'bgm'].forEach((k) => { state.zoneExpanded[k] = true; });
  state.middlePools.forEach((p) => { p.expanded = true; });
}
function collapseAll() {
  ['head', 'tail', 'bgm'].forEach((k) => { state.zoneExpanded[k] = false; });
  state.middlePools.forEach((p) => { p.expanded = false; });
}

function materialsName(kind) {
  return { head: '开头', tail: '结尾', middle: '中间素材' }[kind] || kind;
}

/* ---------- 文件夹选择 ---------- */
async function selectFolder(kind) {
  if (state.selectBusy) { showMsg('文件夹选择窗口已打开', 'info'); return; }
  state.selectBusy = true;
  try {
    const data = await api('/api/select_folder?name=' + encodeURIComponent(kind));
    if (data.busy) { showMsg('文件夹选择窗口已打开', 'info'); return; }
    if (!data.path) return;
    state.folders[kind] = data.path;
    if (kind === 'output') { showMsg('输出路径已选择', 'success'); return; }
    scan(kind);
    if (kind === 'head' && !state.folders.output) state.folders.output = data.path.replace(/[\\/][^\\/]+$/, '') + '\\output';
  } catch (e) {
    showMsg('选择失败：' + e.message, 'error');
  } finally {
    state.selectBusy = false;
  }
}

async function selectPoolFolder(pool) {
  if (state.selectBusy) { showMsg('文件夹选择窗口已打开', 'info'); return; }
  state.selectBusy = true;
  try {
    const data = await api('/api/select_folder?name=' + encodeURIComponent('middle_pool' + pool.id));
    if (data.busy) { showMsg('文件夹选择窗口已打开', 'info'); return; }
    if (!data.path) return;
    pool.folder = data.path;
    scanPool(pool);
  } catch (e) {
    showMsg('选择失败：' + e.message, 'error');
  } finally {
    state.selectBusy = false;
  }
}

/* ---------- 上传 ---------- */
function pickFolderUpload(kind, input) {
  if (!input) return;
  input.value = '';
  input.onchange = async () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      try {
        await uploadFileXhr(kind, f, (pct) => {
          showMsg(`正在上传 ${i + 1}/${files.length}：${f.name} ${pct}%`, 'info');
        });
        ok++;
      } catch (e) {
        showMsg(`上传失败：${f.name} ${e.message}`, 'error');
      }
    }
    const data = await api('/api/upload_path?kind=' + encodeURIComponent(kind));
    if (data.path) {
      state.folders[kind] = data.path;
      const m = data.path.match(/(head|tail|middle)$/);
      if (m && !state.folders.output) state.folders.output = data.path.replace(/(head|tail|middle)$/, 'output');
      showMsg(`上传完成 ${ok}/${files.length} 个文件`, 'success');
      scan(kind);
    }
  };
  input.click();
}

function pickPoolUpload(pool) {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.accept = 'video/*';
  const kind = 'middle_pool' + pool.id;
  input.onchange = async () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      try {
        await uploadFileXhr(kind, f, (pct) => {
          showMsg(`正在上传 ${i + 1}/${files.length}：${f.name} ${pct}%`, 'info');
        });
        ok++;
      } catch (e) {
        showMsg(`上传失败：${f.name} ${e.message}`, 'error');
      }
    }
    const data = await api('/api/upload_path?kind=' + encodeURIComponent(kind));
    if (data.path) {
      pool.folder = data.path;
      showMsg(`上传完成 ${ok}/${files.length} 个文件`, 'success');
      scanPool(pool);
    }
  };
  input.click();
}

function uploadFileXhr(kind, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?kind=' + encodeURIComponent(kind) + '&name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        data.ok ? resolve(data) : reject(new Error(data.error || '上传失败'));
      } catch (e) { reject(new Error('响应解析失败')); }
    };
    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.send(file);
  });
}

async function pickWatermark(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  try {
    await uploadFileXhr('watermark', file);
    const data = await api('/api/upload_path?kind=watermark');
    if (data.path) {
      state.params.watermark_path = data.path + '\\' + file.name;
      showMsg('水印已选择：' + file.name, 'success');
    }
  } catch (err) {
    showMsg('水印上传失败：' + err.message, 'error');
  }
  e.target.value = '';
}

/* ---------- 转场 chips ---------- */
function toggleChip(value) {
  const pool = state.params.transition_types;
  state.params.transition_types = pool.includes(value)
    ? pool.filter((v) => v !== value)
    : [...pool, value];
}

/* ---------- 平台预设 ---------- */
async function applyPreset() {
  if (!state.presetName) return;
  const preset = await api('/api/platform_presets/apply?name=' + encodeURIComponent(state.presetName));
  if (preset.resolution) state.params.resolution = preset.resolution;
  if (preset.duration_mode) state.params.duration_mode = preset.duration_mode;
  showMsg('预设已应用：' + state.presetName, 'success');
}

/* ---------- 模板与配置 ---------- */
async function refreshTemplates() {
  const data = await api('/api/templates');
  state.templates = data.templates || [];
}
async function saveTemplate() {
  const name = state.templateName.trim();
  if (!name) { showMsg('请填写模板名称', 'error'); return; }
  const data = await api('/api/templates/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, config: collectConfig() }),
  });
  showMsg(data.ok ? '模板已保存' : (data.error || '保存失败'), data.ok ? 'success' : 'error');
  refreshTemplates();
}
async function loadTemplateByName() {
  const name = state.selectedTemplate || state.templateName.trim();
  if (!name) { showMsg('请选择或填写模板名称', 'error'); return; }
  const cfg = await api('/api/templates/load', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (cfg && Object.keys(cfg).length) { applyConfig(cfg); showMsg('模板已载入', 'success'); }
  else showMsg('模板不存在', 'error');
}
async function deleteTemplate() {
  const name = state.templateName.trim();
  if (!name) { showMsg('请填写要删除的模板名称', 'error'); return; }
  const data = await api('/api/templates/delete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  showMsg(data.ok ? '模板已删除' : '模板不存在', data.ok ? 'success' : 'error');
  refreshTemplates();
}
async function saveConfig() {
  const data = await api('/api/config/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(collectConfig()),
  });
  showMsg(data.ok ? '配置已保存' : '保存失败', data.ok ? 'success' : 'error');
}
async function loadConfig() {
  const cfg = await api('/api/config/load');
  if (!cfg || !Object.keys(cfg).length) { showMsg('还没有保存过配置', 'info'); return; }
  applyConfig(cfg);
  showMsg('配置已载入', 'success');
}

/* ---------- 历史 ---------- */
async function loadHistory() {
  const data = await api('/api/history');
  state.history = (data.history || []).slice(0, 20);
}
async function clearHistory() {
  if (!confirm('确定要清空所有任务历史记录吗？')) return;
  await api('/api/history/clear');
  loadHistory();
}
function loadFromHistory(h) {
  applyConfig(h.config || {});
  state.step = 2;
  showMsg('已载入历史任务配置', 'success');
}

/* ---------- 预检 ---------- */
async function precheck() {
  try {
    const data = await api('/api/precheck', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectConfig()),
    });
    if (!data.ok) { showMsg(data.error || '预检失败', 'error'); return; }
    state.precheckBad = data.bad || [];
    const total = Object.values(data.report).reduce((n, arr) => n + arr.length, 0);
    if (state.precheckBad.length) {
      showMsg(`预检完成：${total} 个素材，发现 ${state.precheckBad.length} 个问题`, 'error');
    } else {
      showMsg(`预检完成：${total} 个素材全部正常`, 'success');
    }
  } catch (e) {
    showMsg('预检失败：' + e.message, 'error');
  }
}

/* ---------- 任务控制 ---------- */
async function startJob() {
  const payload = collectConfig();
  if (!payload.head_folder || !payload.tail_folder) { showMsg('请先填写开头和结尾文件夹', 'error'); return; }
  if (!payload.output_folder) { showMsg('请选择输出路径', 'error'); return; }
  const data = await api('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!data.ok) { showMsg(data.error || '启动失败', 'error'); return; }
  state.outputFolder = payload.output_folder;
  state.job = { ...state.job, running: true, done: false, current: 0, total: payload.count, logs: [], success: 0, failed: 0, skipped: 0, cancelled: false, success_items: [], failed_items: [], error: null };
  state.previewUrl = '';
  showMsg('任务已启动', 'success');
}

async function previewJob() {
  const payload = collectConfig();
  const data = await api('/api/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!data.ok) { showMsg(data.error || '预览失败', 'error'); return; }
  state.job = { ...state.job, running: true, done: false, current: 0, total: 1, logs: [], success: 0, failed: 0, skipped: 0, cancelled: false, success_items: [], failed_items: [], error: null };
  state.previewUrl = '';
  showMsg('预览生成中', 'info');
}

async function togglePause() {
  const data = await api('/api/pause', { method: 'POST' });
  state.job.paused = data.paused;
}
async function cancelJob() {
  await api('/api/cancel', { method: 'POST' });
}
async function retryFailed() {
  const data = await api('/api/retry_failed', { method: 'POST' });
  if (!data.ok) { showMsg(data.error || '没有可重试的失败项', 'error'); return; }
  showMsg('正在重试失败项', 'info');
}

/* ---------- 输出文件与打包 ---------- */
async function refreshOutputFiles() {
  if (!state.outputFolder) return;
  try {
    const data = await api('/api/list_output?folder=' + encodeURIComponent(state.outputFolder));
    state.outputFiles = data.files || [];
  } catch (e) { /* 静默 */ }
}
async function downloadZip() {
  if (!state.outputFolder) return;
  showMsg('正在打包…', 'info');
  try {
    const resp = await fetch('/api/zip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: state.outputFolder }),
    });
    if (!resp.ok) { showMsg('打包失败', 'error'); return; }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'output_' + new Date().toISOString().slice(0, 10) + '.zip';
    a.click();
    URL.revokeObjectURL(url);
    showMsg('打包完成', 'success');
  } catch (e) {
    showMsg('打包失败：' + e.message, 'error');
  }
}

/* ---------- 轮询 ---------- */
async function poll() {
  try {
    const s = await api('/api/status');
    state.serverOk = true;
    state.job.running = s.running;
    state.job.paused = s.paused;
    state.job.current = s.current;
    state.job.total = s.total;
    state.job.success = s.success;
    state.job.skipped = s.skipped;
    state.job.failed = s.failed;
    state.job.cancelled = s.cancelled;
    state.job.error = s.error;
    state.job.eta = s.eta_seconds;
    state.job.speed = s.speed_per_sec != null ? s.speed_per_sec * 60 : null;
    state.job.logs = s.logs || [];
    state.job.success_items = s.success_items || [];
    state.job.failed_items = s.failed_items || [];
    if (!s.running && state.job.done === false && (s.success > 0 || s.failed > 0 || s.skipped > 0 || s.cancelled)) {
      state.job.done = true;
      if (s.success_items && s.success_items[0] && state.previewUrl === '' && state.job.total === 1) {
        const it = s.success_items[0];
        const m = String(it.output).match(/^(.+)[\\/]([^\\/]+)$/);
        if (m) state.previewUrl = '/api/download?folder=' + encodeURIComponent(m[1]) + '&name=' + encodeURIComponent(m[2]);
      }
      refreshOutputFiles();
      loadHistory();
      if (s.cancelled) showMsg('任务已取消', 'info');
      else if (s.error) showMsg('任务出错：' + s.error, 'error');
      else showMsg(`任务完成：成功 ${s.success}，失败 ${s.failed}，跳过 ${s.skipped}`, s.failed ? 'error' : 'success');
    }
  } catch (e) {
    state.serverOk = false;
  }
}

createApp({
  setup() {
    const dirPicker = ref(null);
    const onPickFolderUpload = (kind) => pickFolderUpload(kind, dirPicker.value);

    onMounted(() => {
      document.documentElement.dataset.theme = state.theme;
      Promise.all([
        api('/api/config/load'),
        api('/api/templates'),
        api('/api/platform_presets'),
      ]).then(([cfg, tpls, presets]) => {
        if (cfg && Object.keys(cfg).length) applyConfig(cfg);
        state.templates = tpls.templates || [];
        state.presets = presets.presets || {};
      }).catch(() => { /* 服务未就绪由轮询提示 */ });
      loadHistory();
      setInterval(poll, 1000);
      poll();
    });

    return {
      ...Vue.toRefs(state),
      state, pageTitle, pageDesc,
      transitionOptions, watermarkScalePct, watermarkOpacityPct,
      progressPct, logHtml, poolPickedCount, resultRows,
      toggleTheme, toggleChip,
      selectFolder, onPickFolderUpload, pickWatermark, dirPicker,
      scan, toggleFixed, fileName, shortError, fmtEta, makeDownloadUrl,
      selectPoolFolder, pickPoolUpload, addMiddlePool, removeMiddlePool, scanPool,
      togglePoolItem, poolOrder, togglePoolExpand, showPoolMore,
      toggleExpand, showMore, expandAll, collapseAll,
      applyPreset, saveTemplate, loadTemplateByName, deleteTemplate, saveConfig, loadConfig,
      loadHistory, clearHistory, loadFromHistory,
      precheck, startJob, previewJob, togglePause, cancelJob, retryFailed,
      downloadZip, Math,
    };
  },
}).mount('#app');
