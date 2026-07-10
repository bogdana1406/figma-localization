// code.js — ядро плагина локализации
//
// Получает из UI распарсенные строки CSV и настройки, сопоставляет их с узлами
// открытого файла и вписывает переводы. Два режима поиска: по id и по тексту.
// Работает в два шага: preview (ничего не пишет) и apply (пишет).

figma.showUI(__html__, { width: 420, height: 640 });

// ─── Нормализация текста (та же логика, что в Python: значимые переносы, режем хвостовой шум) ───
function normalize(text) {
  if (text === null || text === undefined) return "";
  return String(text)
    .split("\n")
    .map(function (line) { return line.replace(/[ \t]+$/, ""); })
    .join("\n")
    .replace(/[\n ]+$/, "");
}

function isNumber(text) {
  return /^\d+$/.test(String(text).trim());
}

// ─── Имя слайда (фрейма верхнего уровня) для узла ─────────────────────────────
// Поднимаемся вверх до прямого ребёнка страницы (это слайд/фрейм) и берём его имя.
function slideNameOf(node) {
  var cur = node;
  var last = node;
  while (cur && cur.parent && cur.parent.type !== "PAGE" && cur.parent.type !== "DOCUMENT") {
    cur = cur.parent;
  }
  // cur теперь — прямой ребёнок страницы (слайд), либо сам узел, если он на холсте
  if (cur && cur.parent && (cur.parent.type === "PAGE")) {
    return cur.name || "";
  }
  return "";
}

// ─── Сжатие номеров слайдов в компактную строку ───────────────────────────────
// Числовые смежные → диапазоны (3–5), нечисловые → перечисление.
function formatSlides(names) {
  var uniq = {};
  names.forEach(function (n) { if (n !== "" && n != null) uniq[n] = true; });
  var list = Object.keys(uniq);

  var nums = [], other = [];
  list.forEach(function (n) {
    if (/^\d+$/.test(n)) nums.push(parseInt(n, 10)); else other.push(n);
  });
  nums.sort(function (a, b) { return a - b; });

  var parts = [];
  var i = 0;
  while (i < nums.length) {
    var start = nums[i], end = nums[i];
    while (i + 1 < nums.length && nums[i + 1] === end + 1) { end = nums[i + 1]; i++; }
    parts.push(start === end ? String(start) : (start + "–" + end));
    i++;
  }
  other.sort();
  return parts.concat(other).join(", ");
}

// ─── Сбор текстовых узлов области ────────────────────────────────────────────
// scope: "selection" | "page" | "all"
async function collectTextNodes(scope) {
  var roots = [];
  if (scope === "selection") {
    roots = figma.currentPage.selection.slice();
    if (roots.length === 0) return { nodes: [], emptySelection: true };
  } else if (scope === "page") {
    roots = [figma.currentPage];
  } else { // all
    await figma.loadAllPagesAsync();
    roots = figma.root.children.slice(); // все страницы
  }

  var result = [];
  for (var i = 0; i < roots.length; i++) {
    var root = roots[i];
    if (root.type === "TEXT") {
      result.push(root);
    } else if ("findAllWithCriteria" in root) {
      // Для PageNode при dynamic-page нужно, чтобы страница была загружена.
      if (root.type === "PAGE" && root.loadAsync) {
        try { await root.loadAsync(); } catch (e) {}
      }
      var texts = root.findAllWithCriteria({ types: ["TEXT"] });
      for (var j = 0; j < texts.length; j++) result.push(texts[j]);
    } else if ("findAll" in root) {
      var t2 = root.findAll(function (n) { return n.type === "TEXT"; });
      for (var k = 0; k < t2.length; k++) result.push(t2[k]);
    }
  }
  return { nodes: result, emptySelection: false };
}

// ─── Запись перевода в один узел ──────────────────────────────────────────────
// Возвращает { ok: bool, reason?: string }
async function writeNode(node, translation, renameLayers) {
  if (node.removed) return { ok: false, reason: "узел удалён" };
  if (node.locked) return { ok: false, reason: "слой залочен" };

  // Загружаем все шрифты, используемые в узле (узел может иметь смешанные шрифты).
  try {
    var len = node.characters.length;
    var fonts = [];
    if (len === 0) {
      fonts = [node.fontName];               // пустой узел — один шрифт
    } else {
      fonts = node.getRangeAllFontNames(0, len);
    }
    for (var i = 0; i < fonts.length; i++) {
      if (fonts[i] === figma.mixed) continue;
      await figma.loadFontAsync(fonts[i]);
    }
  } catch (e) {
    return { ok: false, reason: "не удалось загрузить шрифт: " + String(e.message || e) };
  }

  // По умолчанию имя слоя не должно ехать за текстом → autoRename = false.
  // Если пользователь выбрал «переводить имена» → оставляем autoRename как есть (true),
  // тогда Figma сама переименует слой в текст перевода (поведение CopyDoc).
  try {
    if (!renameLayers && "autoRename" in node) {
      node.autoRename = false;
    }
    node.characters = translation;
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: "ошибка записи: " + String(e.message || e) };
  }
}

// ─── Построение плана (preview) ───────────────────────────────────────────────
// rows: [{id, figma_text_en, figma_text, category, file_key}]
// settings: {mode, scope, onlyNewChanged, renameLayers}
async function buildPlan(rows, settings) {
  var plan = { toWrite: [], notFound: [], skipped: [], fileKeyMismatch: false,
               csvFileKey: "", docFileKey: "", slides: [] };

  // file_key из CSV (берём первый непустой)
  for (var r = 0; r < rows.length; r++) {
    if (rows[r].file_key) { plan.csvFileKey = rows[r].file_key; break; }
  }

  if (settings.mode === "id") {
    // ── Режим по id ──
    // Проверка «тот ли файл» через figma.fileKey (может быть недоступен — тогда пропускаем).
    try { plan.docFileKey = figma.fileKey || ""; } catch (e) { plan.docFileKey = ""; }
    if (plan.docFileKey && plan.csvFileKey && plan.docFileKey !== plan.csvFileKey) {
      plan.fileKeyMismatch = true;
    }

    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      // фильтр «только новые и изменённые»
      if (settings.onlyNewChanged &&
          row.category !== "new" && row.category !== "changed") {
        plan.skipped.push({ id: row.id, reason: "не new/changed (фильтр)" });
        continue;
      }
      var node = null;
      try { node = await figma.getNodeByIdAsync(String(row.id).trim()); } catch (e) { node = null; }
      if (!node || node.type !== "TEXT") {
        plan.notFound.push({ key: row.id, en: row.figma_text_en });
        continue;
      }
      plan.toWrite.push({ nodeId: node.id, translation: row.figma_text, en: row.figma_text_en, category: row.category || "" });
      if (row.frame) plan.slides.push(row.frame);
    }
  } else {
    // ── Режим по тексту ──
    // Карта нормализованный английский → перевод; плюс множество переводов
    // (чтобы отличить непереведённый английский от уже переведённого текста).
    var map = {};
    var translations = {};
    for (var m = 0; m < rows.length; m++) {
      var en = normalize(rows[m].figma_text_en);
      if (en !== "") map[en] = rows[m].figma_text;
      var tr = normalize(rows[m].figma_text);
      if (tr !== "") translations[tr] = true;
    }
    var collected = await collectTextNodes(settings.scope);
    if (collected.emptySelection) { plan.emptySelection = true; return plan; }

    for (var n = 0; n < collected.nodes.length; n++) {
      var tn = collected.nodes[n];
      var cur = normalize(tn.characters);
      if (cur === "") continue;
      if (Object.prototype.hasOwnProperty.call(map, cur)) {
        plan.toWrite.push({ nodeId: tn.id, translation: map[cur], en: cur, category: "" });
        plan.slides.push(slideNameOf(tn));
      } else if (Object.prototype.hasOwnProperty.call(translations, cur)) {
        // текст узла — один из наших переводов → узел уже переведён, всё ок, молчим.
      } else {
        // Текст не знаком ни как английский исходник, ни как наш перевод.
        // Значит это, вероятно, новый/изменённый английский, которого нет в CSV —
        // его стоит показать как «требует перевода».
        plan.notFound.push({ key: "(по тексту)", en: tn.characters.slice(0, 80) });
      }
    }
  }
  return plan;
}

// ─── Применение плана (apply) ─────────────────────────────────────────────────
async function applyPlan(rows, settings) {
  var plan = await buildPlan(rows, settings);
  var written = 0;
  var failures = [];
  var writtenSlides = [];
  var writtenNodes = [];
  var writtenRows = [];

  for (var i = 0; i < plan.toWrite.length; i++) {
    var item = plan.toWrite[i];
    var node = null;
    try { node = await figma.getNodeByIdAsync(item.nodeId); } catch (e) { node = null; }
    if (!node) { failures.push({ en: item.en, reason: "узел исчез" }); continue; }
    var res = await writeNode(node, item.translation, settings.renameLayers);
    if (res.ok) {
      written++;
      var slide = slideNameOf(node);
      writtenSlides.push(slide);
      writtenNodes.push(node);
      writtenRows.push({
        frame: slide,
        layer_name: node.name,
        figma_text_en: item.en,
        figma_text: item.translation,
        category: item.category || ""
      });
    }
    else failures.push({ en: item.en, reason: res.reason });
  }

  // Выделяем записанные узлы текущей страницы — чтобы сразу видеть, что тронуто.
  var onPage = writtenNodes.filter(function (nd) {
    try {
      var p = nd; while (p && p.type !== "PAGE") p = p.parent;
      return p === figma.currentPage;
    } catch (e) { return false; }
  });
  var selectedCount = 0;
  if (onPage.length) {
    try {
      figma.currentPage.selection = onPage;
      figma.viewport.scrollAndZoomIntoView(onPage);
      selectedCount = onPage.length;
    } catch (e) {}
  }

  return {
    written: written,
    writtenSlides: formatSlides(writtenSlides),
    selectedCount: selectedCount,
    writtenRows: writtenRows,
    failures: failures,
    notFound: plan.notFound,
    skipped: plan.skipped
  };
}

// ─── Гарантируем доступ ко ВСЕМ узлам: все страницы + скрытые слои ─────────────
// Без этого getNodeByIdAsync возвращает null для узлов на незагруженных страницах
// и внутри скрытых инстансов — отсюда ложные «не найдено» и скачущие числа.
async function ensureFullAccess() {
  try { figma.skipInvisibleInstanceChildren = false; } catch (e) {}
  try { await figma.loadAllPagesAsync(); } catch (e) {}
}

// ─── Связь с UI ───────────────────────────────────────────────────────────────
figma.ui.onmessage = async function (msg) {
  try {
    if (msg.type === "preview") {
      await ensureFullAccess();
      var plan = await buildPlan(msg.rows, msg.settings);
      figma.ui.postMessage({
        type: "preview-result",
        emptySelection: !!plan.emptySelection,
        fileKeyMismatch: !!plan.fileKeyMismatch,
        csvFileKey: plan.csvFileKey,
        docFileKey: plan.docFileKey,
        toWrite: plan.toWrite.length,
        slides: formatSlides(plan.slides),
        notFound: plan.notFound.slice(0, 200),
        notFoundCount: plan.notFound.length,
        skippedCount: plan.skipped.length
      });
    } else if (msg.type === "apply") {
      await ensureFullAccess();
      var result = await applyPlan(msg.rows, msg.settings);
      figma.ui.postMessage({
        type: "apply-result",
        written: result.written,
        writtenSlides: result.writtenSlides,
        selectedCount: result.selectedCount,
        writtenRows: result.writtenRows,
        failures: result.failures.slice(0, 200),
        failuresCount: result.failures.length,
        notFoundCount: result.notFound.length,
        skippedCount: result.skipped.length
      });
    } else if (msg.type === "close") {
      figma.closePlugin();
    }
  } catch (e) {
    figma.ui.postMessage({ type: "error", message: String(e && e.message ? e.message : e) });
  }
};
