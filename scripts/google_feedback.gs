/* Apps Script backend. Configure Script Properties; never put secrets in source.
 * FEEDBACK_SIGNING_KEY: same value as the Python sender's private configuration.
 * FEEDBACK_SPREADSHEET_ID: private sheet ID.
 * FEEDBACK_SHEET_NAME: tab that holds feedback rows (default: the first tab).
 *   可以与长期热点档案共用一份表格：把反馈 tab 放在第一张，或在这里写死 tab 名。
 *   FEEDBACK_SHEET_CSV_URL 里的 gid 必须指向这个 tab（见 docs/FEEDBACK.md）。
 * FEEDBACK_CSV_READ_TOKEN: a separate secret for read-only CSV export.
 * Columns A-D must stay: item | relevance | usefulness | t
 */
function _escape(value) {
  return String(value).replace(/[&<>"']/g, function(c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}

function _page(title, body) {
  return HtmlService.createHtmlOutput('<!doctype html><html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>' + _escape(title) + '</title></head><body><h1>' + _escape(title)
    + '</h1>' + body + '</body></html>');
}

function _encode(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, function(c) {
    return '%' + c.charCodeAt(0).toString(16).toUpperCase();
  }).replace(/%20/g, '+');
}

function _signature(params, key) {
  var body = Object.keys(params).filter(function(name) { return name !== 'sig'; }).sort()
    .map(function(name) { return _encode(name) + '=' + _encode(params[name]); }).join('&');
  return Utilities.computeHmacSha256Signature(body, key, Utilities.Charset.UTF_8)
    .map(function(byte) { return ('0' + (byte & 255).toString(16)).slice(-2); }).join('');
}

function _equal(a, b) {
  a = String(a || ''); b = String(b || '');
  if (a.length !== b.length) return false;
  var difference = 0;
  for (var i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

function _valid(params, key) {
  if (!key || !/^([0-9]{4}\.[0-9]{4,5}(v[0-9]+)?|[a-z.-]+\/[0-9]{7}(v[0-9]+)?|doi:10\.[0-9]{4,9}\/\S+)$/i.test(params.item || '')) return false;
  if (!/^[0-9]+$/.test(params.expires || '') || Number(params.expires) < Date.now() / 1000) return false;
  var hasLevel = false;
  for (var name of ['rel', 'use']) {
    if (params[name] !== undefined && params[name] !== '') {
      if (!/^[012]$/.test(params[name])) return false;
      hasLevel = true;
    }
  }
  return hasLevel && _equal(params.sig, _signature(params, key));
}

function _sheet(properties) {
  var id = properties.getProperty('FEEDBACK_SPREADSHEET_ID');
  if (!id) throw new Error('Missing spreadsheet configuration');
  var book = SpreadsheetApp.openById(id);
  var name = properties.getProperty('FEEDBACK_SHEET_NAME');
  if (!name) return book.getSheets()[0];
  var sheet = book.getSheetByName(name);
  if (!sheet) throw new Error('找不到反馈表：' + name);
  return sheet;
}

function _csvCell(value) {
  return '"' + String(value === null || value === undefined ? '' : value).replace(/"/g, '""') + '"';
}

function doGet(event) {
  var params = (event || {}).parameter || {};
  var properties = PropertiesService.getScriptProperties();
  if (params.action === 'csv') {
    var readToken = properties.getProperty('FEEDBACK_CSV_READ_TOKEN');
    if (!readToken || !_equal(params.token, readToken)) return _page('无权读取', '<p>读取凭据无效。</p>');
    var values = _sheet(properties).getDataRange().getDisplayValues();
    var csv = values.map(function(row) { return row.slice(0, 4).map(_csvCell).join(','); }).join('\n');
    return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.TEXT);
  }
  if (!_valid(params, properties.getProperty('FEEDBACK_SIGNING_KEY'))) return _page('链接无效', '<p>签名无效或链接已过期。</p>');
  var nonce = Utilities.getUuid();
  CacheService.getScriptCache().put('feedback:' + nonce, JSON.stringify(params), 900);
  var action = ScriptApp.getService().getUrl();
  return _page('确认这次反馈', '<p>' + _escape(params.item) + '</p><form method="post" target="_top" action="'
    + _escape(action) + '"><input type="hidden" name="nonce" value="' + _escape(nonce)
    + '"><button type="submit">确认记录</button></form>');
}

function doPost(event) {
  var nonce = String(((event || {}).parameter || {}).nonce || '');
  if (!/^[a-zA-Z0-9-]{1,100}$/.test(nonce)) return _page('请求无效', '<p>请重新打开邮件链接。</p>');
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var cache = CacheService.getScriptCache();
    var raw = cache.get('feedback:' + nonce);
    if (!raw) return _page('确认已过期', '<p>请重新打开邮件链接。</p>');
    var params = JSON.parse(raw);
    var properties = PropertiesService.getScriptProperties();
    if (!_valid(params, properties.getProperty('FEEDBACK_SIGNING_KEY'))) return _page('链接无效', '<p>签名无效或链接已过期。</p>');
    cache.remove('feedback:' + nonce);
    _sheet(properties).appendRow([params.item, params.rel || '', params.use || '', new Date().toISOString()]);
    return _page('已记录', '<p>' + _escape(params.item) + '</p>');
  } finally {
    lock.releaseLock();
  }
}
