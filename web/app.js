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

const dedupeLevels = [
  { value: 'off', label: '关闭' },
  { value: 'light', label: '轻度' },
  { value: 'deep', label: '深度' },
];
const dedupeOptions = [
  { key: 'visual', name: '画面微调', desc: '亮度/对比度/饱和度 ±5-10%，随机裁切缩放' },
  { key: 'segment', name: '片段差异化', desc: '随机入点偏移、素材顺序、插帧、随机转场' },
  { key: 'audio', name: '音频差异化', desc: 'BGM 随机起播位置、轻微变速' },
];
/* 深度差异化：对画面/声音的更强扰动（打破平台指纹更彻底，观感影响更明显） */
const deepDedupeOptions = [
  { key: 'speed', name: '变速不变调', desc: '整体速度 ±1-3%，画面与声音同步，观感几乎无感' },
  { key: 'mirror', name: '水平镜像', desc: '画面左右翻转（非对称画面适用）' },
  { key: 'noise', name: '轻噪点', desc: '叠加轻微胶片颗粒，打破画面指纹' },
  { key: 'pitch', name: '音调微移', desc: '声音整体升/降调 ≤3%，听感几乎无差' },
];
const deepStrengths = [
  { value: 'low', label: '低', hint: '±1% 扰动，观感几乎不变' },
  { value: 'medium', label: '中', hint: '±2% 扰动，推荐' },
  { value: 'high', label: '高', hint: '±3% 扰动，观感可察觉' },
];
const dedupeLevelHint = {
  off: '不做差异化，每条成片内容一致（适合单条投放）',
  light: '画面与音频轻微扰动，成片观感基本不变（适合少量版本）',
  deep: '片段级差异化（入点偏移/顺序/插帧/转场随机），每条结构不同（适合批量投放）',
};

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
  stepErrors: { 1: false, 2: false, 3: false },
  theme: localStorage.getItem('sppj_theme') || 'dark',
  serverOk: true,
  selectBusy: false,
  msg: { text: '', type: 'info' },
  health: null,
  healthChecking: false,
  previewTrans: 'fade',
  resultExpanded: false,
  similarPairs: [],   // 本次任务输出疑似重复对（感知哈希查重）
  queue: [],          // 待执行任务队列
  queueLabel: '',
  taskSnapshot: null,   // 本次/上次任务的参数快照（生成中改动参数不影响任务，快照用于核对）
  snapOpen: false,
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
    dedupe_level: 'off',
    dedupe_options: { visual: true, segment: true, audio: true, speed: false, mirror: false, noise: false, pitch: false, deep_strength: 'medium' },
    dedupe_versions: 1,
    random_seed: 20260905,
    transition_mode: '不使用',
    transition_type: 'fade',
    transition_duration: 0.5,
    transition_types: transitionOptions.map((t) => t.value),
    bgm_mode: '不使用',
    bgm_volume: 0.2,
    audio_volume: 1.0,
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
    encode_accel: 'auto',
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
  interrupted: false,
  interruptedInfo: null,
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
const pageTitle = computed(() => ['', '素材库', '参数配置', '去重差异化', '生成与结果'][state.step]);
const pageDesc = computed(() => ({
  1: '选择素材文件夹，点击卡片设置固定片头/片尾',
  2: '配置分辨率、时长、转场、BGM、水印等参数',
  3: '设置差异化强度与维度，降低同批投放被判重概率',
  4: '预检素材、批量生成、查看结果与历史',
}[state.step]));

/* ---------- 产出概览（预检后展示） ---------- */
const yieldTotal = computed(() => state.params.count * state.params.dedupe_versions);
const dedupeOn = computed(() => state.params.dedupe_level !== 'off');
const dedupeLevelLabel = computed(() => {
  const lv = dedupeLevels.find((l) => l.value === state.params.dedupe_level);
  return lv ? lv.label : '';
});

/* ---------- 生成中参数修改提示（不阻断操作，仅告知下次任务生效） ---------- */
let lastWarnTs = 0;
function warnIfRunning() {
  if (!state.job.running) return;
  const now = Date.now();
  if (now - lastWarnTs < 2500) return; // 防抖：2.5s 内只提示一次
  lastWarnTs = now;
  showMsg('生成中：当前修改将在下次任务生效，本次任务参数已锁定', 'warn');
}

/* ---------- 本次任务参数快照（展示用） ---------- */
const snapRows = computed(() => {
  const s = state.taskSnapshot;
  if (!s) return [];
  const rows = [];
  const v = (s.dedupe_level !== 'off' ? s.dedupe_versions || 1 : 1);
  rows.push({ label: '出片', value: `${s.count || 0} 条` + (v > 1 ? ` × ${v} 版 = ${(s.count || 0) * v} 条` : '') });
  rows.push({ label: '并发', value: `${s.workers || 1} 路` });
  rows.push({ label: '编码加速', value: s.encode_accel === 'nvenc' ? '显卡加速 (NVENC)' : s.encode_accel === 'cpu' ? '仅 CPU' : '自动检测' });
  rows.push({ label: '深度差异化', value: (s.dedupe_options || {}).speed || (s.dedupe_options || {}).mirror || (s.dedupe_options || {}).noise || (s.dedupe_options || {}).pitch ? '开启' : '关闭' });
  rows.push({ label: '分辨率', value: s.resolution || '-' });
  rows.push({ label: '目标时长', value: s.duration_mode || '不限制' });
  rows.push({ label: '画面适配', value: s.fit_mode || '黑边' });
  rows.push({ label: '转场', value: s.transition_mode || '不使用' });
  rows.push({ label: 'BGM', value: s.bgm_mode || '不使用' });
  rows.push({ label: '水印', value: s.use_watermark ? (s.watermark_mode || '') + (s.watermark_path ? ' · ' + String(s.watermark_path).split(/[\\/]/).pop() : '') : '关闭' });
  rows.push({ label: '去重', value: s.dedupe_level === 'off' ? '未开启' : ((dedupeLevels.find((l) => l.value === s.dedupe_level) || {}).label || s.dedupe_level) + ` · 版本×${s.dedupe_versions || 1}` + (s.dedupe_level !== 'off' && s.dedupe_options && (s.dedupe_options.speed || s.dedupe_options.mirror || s.dedupe_options.noise || s.dedupe_options.pitch) ? ' · 深度差异化' : '') });
  rows.push({ label: '命名模板', value: s.output_name_template || '-' });
  rows.push({ label: '开头素材', value: s.head_folder || '-' });
  rows.push({ label: '结尾素材', value: s.tail_folder || '-' });
  rows.push({ label: '中间素材池', value: (s.middle_pools || []).length ? (s.middle_pools || []).length + ' 个池' : '无' });
  rows.push({ label: '输出目录', value: s.output_folder || '-' });
  return rows;
});

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
const visibleRows = computed(() =>
  state.resultExpanded ? resultRows.value : resultRows.value.slice(0, 5)
);
function toggleResults() { state.resultExpanded = !state.resultExpanded; }

/* ---------- 转场预览 ---------- */
const previewTransClass = computed(() =>
  transitionClass(state.params.transition_mode === '固定' ? state.params.transition_type : state.previewTrans)
);
const previewTransName = computed(() => {
  const v = state.params.transition_mode === '固定' ? state.params.transition_type : state.previewTrans;
  const t = transitionOptions.find((x) => x.value === v);
  return t ? t.label : '';
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
function randomizeSeed() {
  state.params.random_seed = Math.floor(Math.random() * 1e9);
  showMsg('已随机刷新种子：' + state.params.random_seed, 'success');
}
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
  p.encode_accel = cfg.encode_accel || 'auto';
  p.resolution = cfg.resolution || '1080x1920';
  p.duration_mode = cfg.duration_mode || '不限制';
  p.output_name_template = cfg.output_name_template || 'output_{序号}_{开头}_{结尾}';
  p.dedupe_enabled = cfg.dedupe_enabled !== false;
  p.dedupe_level = cfg.dedupe_level || 'off';
  p.dedupe_options = Object.assign({ visual: true, segment: true, audio: true, speed: false, mirror: false, noise: false, pitch: false, deep_strength: 'medium' }, cfg.dedupe_options || {});
  p.dedupe_versions = Math.max(1, Math.min(5, cfg.dedupe_versions || 1));
  p.random_seed = cfg.random_seed || 20260905;
  p.transition_mode = cfg.transition_mode || '不使用';
  p.transition_type = cfg.transition_type || 'fade';
  p.transition_duration = cfg.transition_duration || 0.5;
  p.transition_types = cfg.transition_types?.length ? cfg.transition_types : transitionOptions.map((t) => t.value);
  p.bgm_mode = cfg.bgm_mode || '不使用';
  p.bgm_volume = cfg.bgm_volume ?? 0.2;
  p.audio_volume = cfg.audio_volume ?? 1.0;
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
  if (kind === 'output') { state.stepErrors[1] = false; return; } // 必填项已填：素材库取消标红（输出目录无需扫描素材）
  try {
    const q = new URLSearchParams({ [kind]: folder });
    const data = await api('/api/scan?' + q.toString());
    const files = data[kind] || [];
    // 只处理新增文件，避免重复请求详情
    const existing = new Map(state.materials[kind].map((m) => [m.path, m]));
    const newFiles = files.filter((f) => !existing.has(f.path));
    const enriched = await enrichMaterials(newFiles, kind);
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

async function enrichMaterials(files, kind = '', concurrency = 6) {
  const out = new Array(files.length);
  let next = 0;
  // 按内容指纹标记重复素材（同指纹除首个外标 dup）
  const seen = new Set();
  for (const f of files) {
    f._dup = f.fp ? seen.has(f.fp) : false;
    if (f.fp) seen.add(f.fp);
  }
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= files.length) return;
      const f = files[i];
      try {
        const d = await api('/api/material_detail?path=' + encodeURIComponent(f.path) + '&kind=' + encodeURIComponent(kind));
        // BGM 为纯音频（无视频流），有音频流即正常；视频素材必须有视频流
        const ok = kind === 'bgm' ? (d.ok && (d.has_video || d.has_audio)) : (d.ok && d.has_video);
        out[i] = {
          path: f.path, name: f.name, ok, dup: !!f._dup,
          thumbUrl: thumbUrl(f.path),
          duration: d.duration || 0,
          width: d.width || 0, height: d.height || 0,
          landscape: (d.width || 0) >= (d.height || 0),
          durationText: fmtDuration(d.duration),
          resText: d.width && d.height ? d.width + '×' + d.height : '',
        };
      } catch (e) {
        out[i] = { path: f.path, name: f.name, ok: false, dup: !!f._dup, thumbUrl: thumbUrl(f.path), durationText: '未知', resText: '' };
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
    if (kind === 'output') { state.stepErrors[1] = false; showMsg('输出路径已选择', 'success'); return; }
    scan(kind);
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

/* ---------- 水印选择（服务端文件对话框，直接引用本地文件，不拷贝） ---------- */

async function pickWatermark(e) {
  e.preventDefault();
  try {
    const data = await api('/api/select_watermark');
    if (data.busy) { showMsg('已有窗口打开，请先完成当前选择', 'warn'); return; }
    if (data.path) {
      state.params.watermark_path = data.path;
      showMsg('水印已选择：' + data.path.split(/[\\/]/).pop(), 'success');
    }
  } catch (err) {
    showMsg('选择水印失败：' + err.message, 'error');
  }
  if (e && e.target) e.target.value = '';
}

/* ---------- 转场 chips ---------- */
function toggleChip(value) {
  const pool = state.params.transition_types;
  state.params.transition_types = pool.includes(value)
    ? pool.filter((v) => v !== value)
    : [...pool, value];
  state.previewTrans = value; // 点击即预览该转场
}

/* ---------- 转场预览 ---------- */
function transitionClass(value) {
  const map = {
    fade: 'tp-fade', dissolve: 'tp-fade',
    slideleft: 'tp-slide-l', slideright: 'tp-slide-r', slideup: 'tp-slide-u', slidedown: 'tp-slide-d',
    wipeleft: 'tp-wipe-l', wiperight: 'tp-wipe-r', wipeup: 'tp-wipe-u', wipedown: 'tp-wipe-d',
    circleopen: 'tp-circle', circleclose: 'tp-circle', circlecrop: 'tp-circle',
    smoothleft: 'tp-slide-l', smoothright: 'tp-slide-r', smoothup: 'tp-slide-u', smoothdown: 'tp-slide-d',
    diagtl: 'tp-wipe-l', diagtr: 'tp-wipe-r', diagbl: 'tp-wipe-u', diagbr: 'tp-wipe-d',
    zoomin: 'tp-zoom', pixelize: 'tp-pixel',
    fadeblack: 'tp-fade', fadewhite: 'tp-fade',
    coverleft: 'tp-slide-l', coverright: 'tp-slide-r', coverup: 'tp-slide-u', coverdown: 'tp-slide-d',
    revealleft: 'tp-slide-l', revealright: 'tp-slide-r', revealup: 'tp-slide-u', revealdown: 'tp-slide-d',
    vertopen: 'tp-vert', vertclose: 'tp-vert',
    horzopen: 'tp-horz', horzclose: 'tp-horz',
    squeezeh: 'tp-squeeze-h', squeezev: 'tp-squeeze-v',
  };
  return map[value] || 'tp-fade';
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
  if (state.healthChecking) return;
  state.healthChecking = true;
  try {
    const data = await api('/api/health_check', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectConfig()),
    });
    if (!data.ok) { showMsg(data.error || '体检失败', 'error'); return; }
    state.health = data;
    state.precheckBad = data.report ? Object.values(data.report).flat().filter((m) => !m.ok) : [];
    const total = data.report ? Object.values(data.report).reduce((n, arr) => n + arr.length, 0) : 0;
    if (data.errors.length) {
      showMsg(`体检未通过：${data.errors[0].msg}`, 'error');
    } else if (data.warns.length) {
      showMsg(`体检通过：${total} 个素材，${data.warns.length} 项提醒（${data.warns[0].msg}）`, 'info');
    } else {
      showMsg(`体检通过：${total} 个素材全部正常`, 'success');
    }
  } catch (e) {
    showMsg('体检失败：' + e.message, 'error');
  } finally {
    state.healthChecking = false;
  }
}

/* ---------- 任务控制 ---------- */
async function startJob() {
  const payload = collectConfig();
  if (!payload.head_folder || !payload.tail_folder) { showMsg('请先填写开头和结尾文件夹', 'error'); return; }
  if (!payload.output_folder) {
    state.stepErrors[1] = true; // 必填项缺失：素材库步骤标红
    showMsg('请选择输出目录（左侧「素材库」已标红）', 'error');
    return;
  }
  state.stepErrors[1] = false;
  // 开始前全局体检（每次实时检查，避免素材改动后状态过期）：有阻断问题则不启动
  await precheck();
  if (state.health && state.health.errors.length) {
    showMsg('体检未通过，无法生成：' + state.health.errors[0].msg, 'error');
    return;
  }
  const data = await api('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!data.ok) { showMsg(data.error || '启动失败', 'error'); return; }
  state.taskSnapshot = { ...payload };  // 参数快照：生成期间改动参数不影响本次任务，此处留档核对
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

/* ---------- 断点续跑 ---------- */
async function resumeJob() {
  const data = await api('/api/resume', { method: 'POST' });
  if (!data.ok) { showMsg(data.error || '无法继续', 'error'); return; }
  state.interrupted = false;
  state.interruptedInfo = null;
  state.job = { ...state.job, running: true, done: false, current: 0, total: 0, logs: [], success: 0, failed: 0, skipped: 0, cancelled: false, success_items: [], failed_items: [], error: null };
  showMsg('继续生成中（已完成输出自动跳过）', 'success');
}
async function discardInterrupted() {
  await api('/api/discard_interrupted', { method: 'POST' });
  state.interrupted = false;
  state.interruptedInfo = null;
  showMsg('已放弃上次中断的任务', 'info');
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
    // 断点续跑：检测到上次任务中断（服务重启后快照恢复）
    if (s.interrupted && s.interrupted.exists && !state.interrupted) {
      state.interrupted = true;
      state.interruptedInfo = { label: s.interrupted.label || '任务', total: s.interrupted.total || 0 };
      showMsg(`检测到上次${state.interruptedInfo.label}未完成（共 ${state.interruptedInfo.total} 条），可在「生成与结果」页继续`, 'warn');
    } else if (!s.interrupted && state.interrupted) {
      state.interrupted = false;
    }
    // 刷新页面后从后端恢复参数快照（断点续跑/重试用原任务参数，快照保持一致）
    if (s.last_config && !state.taskSnapshot) {
      state.taskSnapshot = s.last_config;
    }
    if (!s.running && state.job.done === false && (s.success > 0 || s.failed > 0 || s.skipped > 0 || s.cancelled)) {
      state.job.done = true;
      if (s.success_items && s.success_items[0] && state.previewUrl === '' && state.job.total === 1) {
        const it = s.success_items[0];
        const m = String(it.output).match(/^(.+)[\\/]([^\\/]+)$/);
        if (m) state.previewUrl = '/api/download?folder=' + encodeURIComponent(m[1]) + '&name=' + encodeURIComponent(m[2]);
      }
      refreshOutputFiles();
      loadHistory();
      loadSimilar();
      if (s.cancelled) showMsg('任务已取消', 'info');
      else if (s.error) showMsg('任务出错：' + s.error, 'error');
      else showMsg(`任务完成：成功 ${s.success}，失败 ${s.failed}，跳过 ${s.skipped}`, s.failed ? 'error' : 'success');
    }
  } catch (e) {
    state.serverOk = false;
  }
}

async function loadSimilar() {
  try {
    const s = await api('/api/similar');
    state.similarPairs = (s && s.pairs) || [];
  } catch (e) { /* 服务未就绪忽略 */ }
}

async function loadQueue() {
  try {
    const s = await api('/api/queue/list');
    state.queue = (s && s.queue) || [];
  } catch (e) { /* 服务未就绪忽略 */ }
}

async function addToQueue() {
  const cfg = collectConfig();
  if (!cfg) return;
  const label = state.queueLabel.trim() || '';
  const r = await api('/api/queue/add', { label, ...cfg });
  if (r && r.ok) {
    state.queueLabel = '';
    showMsg(r.queue_len ? `已加入队列，共 ${r.queue_len} 项等待` : '已加入队列', 'success');
    loadQueue();
  } else if (r && r.error) showMsg(r.error, 'error');
}

async function removeFromQueue(id) {
  await api('/api/queue/remove', { id });
  loadQueue();
}

async function clearQueue() {
  await api('/api/queue/clear', {});
  showMsg('队列已清空', 'info');
  loadQueue();
}

function exportCsv() {
  const items = state.job.success_items || [];
  if (!items.length) { showMsg('没有可导出的成片', 'warn'); return; }
  const esc = v => {
    const s = String(v == null ? '' : v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const rows = [
    ['序号', '成片文件名', '完整路径', '开头素材', '中间素材', '结尾素材'],
    ...items.map(it => {
      const out = String(it.output || '');
      const name = out.split(/[\\/]/).pop() || out;
      return [it.index, name, out, fileName(it.head), (it.middle || '').replace(/^ \+ /, ''), fileName(it.tail)];
    }),
  ];
  const csv = '\uFEFF' + rows.map(r => r.map(esc).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  a.href = URL.createObjectURL(blob);
  a.download = `投放清单_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
  showMsg(`已导出投放清单（${items.length} 条）`, 'success');
}

createApp({
  setup() {
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
      loadSimilar();
      loadQueue();
      setInterval(poll, 1000);
      poll();
    });

    return {
      ...Vue.toRefs(state),
      state, pageTitle, pageDesc,
      transitionOptions, dedupeLevels, dedupeOptions, dedupeLevelHint, watermarkScalePct, watermarkOpacityPct,
      yieldTotal, dedupeOn, dedupeLevelLabel, warnIfRunning, snapRows,
      progressPct, logHtml, poolPickedCount, resultRows,
      toggleTheme, toggleChip, randomizeSeed,
      selectFolder, pickWatermark,
      scan, toggleFixed, fileName, shortError, fmtEta, makeDownloadUrl,
      selectPoolFolder, addMiddlePool, removeMiddlePool, scanPool,
      togglePoolItem, poolOrder, togglePoolExpand, showPoolMore,
      toggleExpand, showMore, expandAll, collapseAll,
      applyPreset, saveTemplate, loadTemplateByName, deleteTemplate, saveConfig, loadConfig,
      loadHistory, clearHistory, loadFromHistory,
      precheck, startJob, previewJob, togglePause, cancelJob, retryFailed,
      resumeJob, discardInterrupted,
      downloadZip, Math,
      previewTransClass, previewTransName,
      visibleRows, toggleResults,
      deepDedupeOptions, deepStrengths,
      addToQueue, removeFromQueue, clearQueue, exportCsv,
    };
  },
}).mount('#app');
