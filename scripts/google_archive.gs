/**
 * 长期热点档案的 Apps Script 后端（v0.16）。
 *
 * 部署：扩展程序 → Apps Script → 粘贴本文件 → 项目设置里加两个脚本属性
 *   ARCHIVE_SPREADSHEET_ID = 目标表格 id（从 URL 里 /d/<id>/ 取）
 *   ARCHIVE_TOKEN          = 与 Python 端 ARCHIVE_TOKEN 相同的随机字符串
 * 然后「部署 → 新建部署 → 类型：Web 应用」，执行身份选「我」，
 * 访问权限选「任何人」。把部署 URL 填进 ARCHIVE_WEBHOOK_URL。
 *
 * 协议：Python 发送 {ts, sig, payload}，payload 是 base64 的 UTF-8 JSON；
 * sig 是 HMAC-SHA256(ARCHIVE_TOKEN, 原始字节) 的十六进制。脚本按第一列以外
 * 的复合键 upsert（papers: week_start+id；weekly_topics: week_start+topic；
 * weekly_overview: week_start；dict: topic），因此每天重复写同一批行是幂等的。
 */

var SHEETS = {
  papers: ['week_start','week_label','date','id','doi','venue','source_kind','pack',
           'categories','topics','keywords','title','title_zh','first_author','n_authors',
           'summary_snippet','score','rating','s_keyword','s_zotero','s_category','s_library',
           's_feedback','shown','deep','relevance','relevance_kind','verdict','concepts',
           'n_numbers','n_red_flags','basis','confidence'],
  weekly_topics: ['week_start','week_label','topic','pack','papers','share','prev_papers','delta_pct',
                  'ma4','arxiv_papers','journal_papers','mean_score','top_concepts','first_week','finalized'],
  weekly_overview: ['week_start','week_label','sampled','arxiv_papers','journal_papers','unique_categories',
                    'deep_reads','venues','top5_topics','top5_concepts','rising','falling','finalized'],
  dict: ['topic','aliases','pack','source','first_week','last_week','total_papers'],
  feedback_view: ['week_start','week_label','topic','votes','mean_relevance','mean_usefulness',
                  'top_useful','updated']
};

function _prop(name) {
  return PropertiesService.getScriptProperties().getProperty(name);
}

function _book() {
  var id = _prop('ARCHIVE_SPREADSHEET_ID');
  if (!id) throw new Error('缺少脚本属性 ARCHIVE_SPREADSHEET_ID');
  return SpreadsheetApp.openById(id);
}

function _sheet(name) {
  var book = _book();
  var sheet = book.getSheetByName(name);
  if (!sheet) sheet = book.insertSheet(name);
  return sheet;
}

function _hex(bytes) {
  var out = '';
  for (var i = 0; i < bytes.length; i++) {
    var value = bytes[i] < 0 ? bytes[i] + 256 : bytes[i];
    out += ('0' + value.toString(16)).slice(-2);
  }
  return out;
}

function _equal(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function _json(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function _verify(envelope) {
  var token = _prop('ARCHIVE_TOKEN');
  if (!token) throw new Error('缺少脚本属性 ARCHIVE_TOKEN');
  var ts = Number(envelope.ts);
  if (!isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > 600) throw new Error('时间戳过期');
  var bytes = Utilities.base64Decode(envelope.payload || '');
  var expected = _hex(Utilities.computeHmacSha256Signature(bytes, token));
  if (!_equal(expected.toLowerCase(), String(envelope.sig || '').toLowerCase())) {
    throw new Error('签名不匹配');
  }
  return JSON.parse(Utilities.newBlob(bytes).getDataAsString('UTF-8'));
}

function _ensure(sheet, name) {
  var headers = SHEETS[name];
  var values = sheet.getDataRange().getDisplayValues();
  if (values.length === 0 || (values.length === 1 && values[0].join('').trim() === '')) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    return {headers: headers, rows: []};
  }
  var header = values[0].map(String);
  if (header.slice(0, headers.length).join('\u0001') !== headers.join('\u0001')) {
    throw new Error('表头不一致：' + name + ' -> ' + header.join(','));
  }
  return {headers: header, rows: values.slice(1)};
}

function _read(name, where) {
  var sheet = _sheet(name);
  var current = _ensure(sheet, name);
  var rows = current.rows;
  var filters = Object.keys(where || {});
  if (filters.length) {
    var indexes = filters.map(function (column) {
      var index = current.headers.indexOf(column);
      if (index < 0) throw new Error('未知列：' + column);
      return index;
    });
    rows = rows.filter(function (row) {
      for (var i = 0; i < filters.length; i++) {
        if (String(row[indexes[i]]) !== String(where[filters[i]])) return false;
      }
      return true;
    });
  }
  return {headers: current.headers, rows: rows};
}

function _upsert(name, headers, keyColumns, rows) {
  if (!rows || !rows.length) return 0;
  if (headers.join('|') !== SHEETS[name].join('|')) throw new Error('列定义不一致：' + name);
  var sheet = _sheet(name);
  var current = _ensure(sheet, name);
  var header = current.headers;
  var keyIndexes = keyColumns.map(function (column) {
    var index = header.indexOf(column);
    if (index < 0) throw new Error('未知键列：' + column);
    return index;
  });
  var positions = {};
  var existing = current.rows;
  for (var r = 0; r < existing.length; r++) {
    var key = keyIndexes.map(function (index) { return String(existing[r][index]); }).join('|');
    positions[key] = r + 2;  // 1-based，且第 1 行是表头
  }
  var updates = [];
  var appends = [];
  rows.forEach(function (row) {
    if (row.length !== header.length) throw new Error('行列数不一致：' + row.length + ' != ' + header.length);
    var key = keyIndexes.map(function (index) { return String(row[index]); }).join('|');
    if (positions[key]) updates.push([positions[key], row]);
    else appends.push(row);
  });
  updates.forEach(function (pair) {
    sheet.getRange(pair[0], 1, 1, header.length).setValues([pair[1]]);
  });
  if (appends.length) {
    var start = sheet.getLastRow() + 1;
    sheet.getRange(start, 1, appends.length, header.length).setValues(appends);
  }
  return updates.length + appends.length;
}

function doPost(e) {
  try {
    var data = _verify(JSON.parse(e.postData.contents));
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(15000)) throw new Error('表格正忙，稍后重试');
    try {
      if (data.action === 'read') {
        var read = _read(data.sheet, data.where);
        return _json({ok: true, headers: read.headers, rows: read.rows});
      }
      if (data.action === 'upsert') {
        return _json({ok: true, written: _upsert(data.sheet, data.headers, data.key, data.rows)});
      }
      if (data.action === 'health') {
        return _json({ok: true, sheets: Object.keys(SHEETS), time: new Date().toISOString()});
      }
      throw new Error('未知操作：' + data.action);
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return _json({ok: false, error: String(err && err.message ? err.message : err)});
  }
}

function doGet() {
  return HtmlService.createHtmlOutput(
    '<meta charset="utf-8"><h1>arxiv digest archive</h1>' +
    '<p>这是一个只接收签名 POST 的端点。用 <code>python -m arxiv_digest.archive --health</code> 检查。</p>');
}
