/* ============================================================
   纯工具函数（无状态，可独立测试）
   ============================================================ */

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

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
function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + 'm' + (s < 10 ? '0' : '') + s + 's';
}

/* 转场预览动画 class 映射（纯映射，无副作用） */
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
    radial: 'tp-radial',
    hlslice: 'tp-hlslice', hrslice: 'tp-hrslice',
    vuslice: 'tp-vuslice', vdslice: 'tp-vdslice',
    hblur: 'tp-hblur',
    wipetl: 'tp-wipe-tl', wipetr: 'tp-wipe-tr',
    fadegrays: 'tp-fadegray',
  };
  return map[value] || 'tp-fade';
}

function thumbUrl(path) {
  return '/api/thumb?path=' + encodeURIComponent(path);
}

function materialsName(kind) {
  return { head: '开头', tail: '结尾', middle: '中间素材' }[kind] || kind;
}
