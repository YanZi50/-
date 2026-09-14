/* ============================================================
   静态配置表（转场选项 / 去重级别 / 档位 / 差异化选项）
   纯数据，无状态、无副作用，供 app.js 引用
   ============================================================ */

const dedupeLevels = [
  { value: 'off', label: '关闭' },
  { value: 'light', label: '轻度' },
  { value: 'deep', label: '深度' },
];
const countSteps = [10, 20, 50, 100, 200];
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

/* 默认转场 keys 快照：用于判断用户是否改过转场池（后端动态表同步时保护用户选择） */
const DEFAULT_TRANSITION_KEYS = transitionOptions.map((t) => t.value);
