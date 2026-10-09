const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('pages/reader.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const save = source.slice(source.indexOf('function save('), source.indexOf('function keepSettings'));
const loader = source.slice(source.indexOf('async function loadBook('), source.indexOf("if(format==='pdf'){status.hidden"));
test('all changed browser scripts parse', () => {
  new vm.Script(source);
  for (const file of ['reading-support.js','recent-reading.js','site-preferences.js','shelf-store.js']) new vm.Script(fs.readFileSync(file,'utf8'));
  for (const file of ['pages/home.html','pages/bookshelf.html','pages/online-search.html','pages/admin.html']) {
    for (const match of fs.readFileSync(file,'utf8').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  }
});
test('tap zones and chapter controls are present in the reader', () => {
  assert.match(html, /id="chapter-prev"[^>]*>上一章/);
  assert.match(html, /id="chapter-next"[^>]*>下一章/);
  assert.match(source, /document\.addEventListener\('pointerup',finishTap,true\)/);
  assert.match(source, /if\(y>=\.66\)\{stepRead\(1\);return\}/);
  assert.match(source, /if\(x>\.22&&x<\.78&&y>\.20&&y<\.80\)\{controlsPanel\.classList\.toggle\('open'\);return\}/);
  assert.match(source, /function stepChapter\(direction\)/);
});
test('reader batches large-book DOM work and lets the loading notice paint first', () => {
  assert.match(source, /document\.createDocumentFragment\(\)/);
  assert.match(source, /status\.textContent='正文已收到，正在排版…'/);
  assert.match(source, /await new Promise\(function\(resolve\)\{requestAnimationFrame\(resolve\)\}\)/);
});
test('reader offers stable in-book search from the settings panel', () => {
  assert.match(html, /id="search-text"/);
  assert.match(html, /id="search-next"/);
  assert.match(source, /function findText\(\)/);
  assert.match(source, /function jumpText\(i,offset\)/);
});
test('new-book notice uses the update endpoint and limits the message to two books', () => {
  const preferences=fs.readFileSync('site-preferences.js','utf8');
  assert.match(preferences,/\/api\/updates/);
  assert.match(preferences,/data\.items\.slice\(0,2\)/);
  assert.match(preferences,/xiaomuwu-last-update-notice/);
});
test('catalog and search always request a fresh book index after uploads', () => {
  for (const page of ['pages/catalog.html','pages/search.html']) {
    assert.match(fs.readFileSync(page,'utf8'), /catalog\.json', \{ cache: 'no-store'/);
  }
});
test('catalog categories support collapse and half-screen browsing', () => {
  const catalog=fs.readFileSync('pages/catalog.html','utf8');
  assert.match(catalog,/catalog-panel-toggle/);
  assert.match(catalog,/catalog-panel-mode/);
  assert.match(catalog,/ct-filter-panel\.is-half/);
});
test('shelf data is mirrored locally so changing a card does not clear it', () => {
  const shelf=fs.readFileSync('shelf-store.js','utf8');
  assert.match(shelf,/xiaomuwu-shelf-backup/);
  assert.match(shelf,/Storage\.prototype\.setItem/);
});
function saveContext() {
  const writes = new Map();
  const ctx = {restoring:false,layoutReady:true,content:{hidden:false},saveTimer:null,clearTimeout(){},setTimeout(fn){ctx.pending=fn;},localStorage:{setItem(k,v){writes.set(k,JSON.parse(v));}},key:'book',mode:'scroll',size:18,paperTone:'cream',progress:{value:'620'},path:'book.txt',bookTitle:'测试',format:'txt',onlineId:'',sourceName:'',sourceUrl:''};
  vm.createContext(ctx);vm.runInContext(save,ctx);return {ctx,writes};
}
test('failed loading and initial restoration cannot overwrite progress', () => {
  const {ctx,writes}=saveContext();
  for(const field of ['restoring','layoutReady','content']) {
    ctx.restoring=field==='restoring';ctx.layoutReady=field!=='layoutReady';ctx.content.hidden=field==='content';ctx.save(true);
  }
  assert.equal(writes.size,0);
});
test('page exit flushes progress and recent-book metadata synchronously', () => {
  const {ctx,writes}=saveContext();ctx.save(true);
  assert.equal(writes.get('book').progress,620);assert.equal(writes.get('xiaomuwu-last-read').path,'book.txt');
});
test('delayed save rechecks restoration guard', () => {
  const {ctx,writes}=saveContext();ctx.save();ctx.restoring=true;ctx.pending();assert.equal(writes.size,0);
});
function loadContext(code) {
  const scheduled=[];
  const ctx={status:{append(){}},src:'/read/test',onlineId:'',AbortController,clearTimeout(){},setTimeout(fn,delay){scheduled.push(delay);return 1;},fetch:async()=>({ok:false,status:code}),document:{createElement(){return {};},createTextNode(){return {};},dispatchEvent(){}},CustomEvent:class{},bookTitle:'测试',path:'test',renderText(){throw Error('unexpected render');}};
  vm.createContext(ctx);vm.runInContext(loader,ctx);return {ctx,scheduled};
}
test('permission and missing-file failures do not retry automatically', async () => {
  for(const code of [403,404]) {const {ctx,scheduled}=loadContext(code);await ctx.loadBook(0);assert.deepEqual(scheduled,[20000]);assert.match(ctx.status.textContent,code===403?/权限/:/找不到/);}
});
test('transient failures retry once and then show a network/server message', async () => {
  const {ctx,scheduled}=loadContext(503);await ctx.loadBook(0);assert.ok(scheduled.includes(1200));await ctx.loadBook(1);assert.match(ctx.status.textContent,/服务器/);
});
