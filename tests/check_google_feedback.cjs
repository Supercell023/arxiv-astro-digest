// Offline Apps Script logic checks. No Google account, network, or filesystem writes.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

function run() {
  const rows = [['item', 'relevance', 'usefulness', 't']];
  const cache = new Map();
  const properties = {FEEDBACK_SIGNING_KEY: 'unit-test-key', FEEDBACK_SPREADSHEET_ID: 'test-sheet', FEEDBACK_CSV_READ_TOKEN: 'read-test'};
  const sandbox = {
    Utilities: {Charset: {UTF_8: 'utf-8'}, getUuid: () => 'test-nonce',
      computeHmacSha256Signature: (body, key) => [...crypto.createHmac('sha256', key).update(body).digest()]},
    PropertiesService: {getScriptProperties: () => ({getProperty: key => properties[key]})},
    CacheService: {getScriptCache: () => ({put: (k,v) => cache.set(k,v), get: k => cache.get(k), remove: k => cache.delete(k)})},
    HtmlService: {createHtmlOutput: text => text},
    ContentService: {MimeType: {TEXT: 'text/plain'}, createTextOutput: text => ({text, setMimeType() {return this;}})},
    SpreadsheetApp: {openById: () => ({getSheets: () => [{appendRow: row => rows.push(row), getDataRange: () => ({getDisplayValues: () => rows})}]})},
    ScriptApp: {getService: () => ({getUrl: () => 'https://example.invalid/exec'})},
    LockService: {getScriptLock: () => ({waitLock() {}, releaseLock() {}})},
  };
  vm.createContext(sandbox);
  new vm.Script(fs.readFileSync(path.join(__dirname, '../scripts/google_feedback.gs'), 'utf8')).runInContext(sandbox);
  const vector = {item: 'doi:10.1234/a+b(c)~', rel: '2', t: '中文 2026-10-26', expires: '2000000000'};
  // Expected hash generated independently by Python urllib.parse.urlencode + hmac.
  assert.equal(sandbox._signature(vector, 'unit-test-key'), '205eee4e1dfc0cf143e8f76d09af00d889b7bb015cc481d63a5d7f86d371fd84');
  const params = {item: '2610.12345v1', rel: '0', expires: String(Math.floor(Date.now()/1000)+3600)};
  params.sig = sandbox._signature(params, properties.FEEDBACK_SIGNING_KEY);
  assert.equal(sandbox._valid(params, properties.FEEDBACK_SIGNING_KEY), true);
  assert.equal(sandbox._valid({...params, rel: '2'}, properties.FEEDBACK_SIGNING_KEY), false);
  sandbox.doGet({parameter: params});
  assert.equal(rows.length, 1, 'GET must not append a vote');
  sandbox.doPost({parameter: {nonce: 'test-nonce'}});
  assert.equal(rows.length, 2);
  assert.equal(rows[1][1], '0', 'zero must not be treated as missing');
  sandbox.doPost({parameter: {nonce: 'test-nonce'}});
  assert.equal(rows.length, 2, 'reused nonce must not append twice');
  assert.match(sandbox.doGet({parameter: {action: 'csv', token: 'wrong'}}), /无权读取/);
  assert.match(sandbox.doGet({parameter: {action: 'csv', token: 'read-test'}}).text, /2610.12345v1/);
  assert.equal(sandbox._escape('<script>'), '&lt;script&gt;');
  return {checks: 10, network: false, writes: false};
}
module.exports = {run};
if (require.main === module) console.log(JSON.stringify(run()));
