const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('pages/reader.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const save = source.slice(source.indexOf('function save('), source.indexOf('function keepSettings'));
const loader = source.slice(source.indexOf('async function loadBook('), source.indexOf("}fetch('/api/access'") + 1);
test('all changed browser scripts parse', () => {
  new vm.Script(source);
  new vm.Script(fs.readFileSync('browser-compat.js','utf8'));
  for (const file of ['reading-support.js','recent-reading.js','site-preferences.js','shelf-store.js','member-tools.js','type-requests.js','admin-community.js','reader-stability.js']) new vm.Script(fs.readFileSync(file,'utf8'));
  for (const file of ['pages/home.html','pages/bookshelf.html','pages/online-search.html','pages/admin.html']) {
    for (const match of fs.readFileSync(file,'utf8').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  }
});
test('core pages load fallbacks for older embedded mobile browsers before their app scripts', () => {
  for (const page of ['pages/home.html','pages/search.html','pages/catalog.html','pages/online-search.html','pages/bookshelf.html','pages/community.html','pages/reader.html']) {
    const content=fs.readFileSync(page,'utf8');
    assert.match(content,/browser-compat\.js\?v=20261024-1/);
  }
  const compat=fs.readFileSync('browser-compat.js','utf8');
  assert.match(compat,/replaceChildren/);
  assert.match(compat,/URLSearchParams/);
});
test('tap zones and chapter controls are present in the reader', () => {
  assert.match(html, /id="chapter-prev"[^>]*>上一章/);
  assert.match(html, /id="chapter-next"[^>]*>下一章/);
  assert.match(source, /document\.addEventListener\('pointerup',finishTap,true\)/);
  assert.match(source, /if\(y>=\.66\)\{stepRead\(1\);return\}/);
  assert.match(source, /now-downAt>420/);
  assert.match(source, /now-lastScrollAt<140/);
  assert.match(source, /scrollFrame=requestAnimationFrame/);
  assert.doesNotMatch(source, /scrollBy\(\{top:direction\*Math\.max\(280,innerHeight\*\.76\),behavior:'smooth'\}\)/);
  assert.match(source, /if\(x>\.22&&x<\.78&&y>\.20&&y<\.80\)\{controlsPanel\.classList\.toggle\('open'\);return\}/);
  assert.match(source, /function stepChapter\(direction\)/);
});
test('reader batches large-book DOM work and lets the loading notice paint first', () => {
  assert.match(source, /document\.createDocumentFragment\(\)/);
  assert.match(source, /status\.textContent='正文已收到，正在排版…'/);
  assert.match(source, /await new Promise\(function\(resolve\)\{requestAnimationFrame\(resolve\)\}\)/);
});
test('page turning has a visible-text fallback instead of leaving a blank page', () => {
  const stability=fs.readFileSync('reader-stability.js','utf8');
  assert.match(stability,/hasVisibleText/);
  assert.match(stability,/candidates = \[original - step/);
  assert.match(html,/reader-stability\.js\?v=20261011-1/);
  assert.match(fs.readFileSync('site-preferences.css','utf8'),/height:calc\(100vh - 72px\)/);
});
test('reader offers stable in-book search from the settings panel', () => {
  assert.match(html, /id="search-text"/);
  assert.match(html, /id="search-next"/);
  assert.match(source, /function findText\(\)/);
  assert.match(source, /function jumpText\(i,offset\)/);
});
test('reader explains wrong card type before requesting protected text', () => {
  assert.match(source, /fetch\('\/api\/access',\{cache:'no-store'\}\)/);
  assert.match(source, /access\.cardType!=='reader'/);
  assert.match(source, /当前登录的是下载卡，只能下载本地书/);
});
test('new-book notice uses the update endpoint and limits the message to two books', () => {
  const preferences=fs.readFileSync('site-preferences.js','utf8');
  assert.match(preferences,/\/api\/updates/);
  assert.match(preferences,/data\.items\.slice\(0,2\)/);
  assert.match(preferences,/xiaomuwu-last-update-notice/);
});
test('catalog and search always revalidate the book index after uploads', () => {
  for (const page of ['pages/catalog.html','pages/search.html']) {
    assert.match(fs.readFileSync(page,'utf8'), /catalog\.json', \{ cache: 'no-cache'/);
  }
});
test('typed search covers every directory and supports direct page jumps', () => {
  const search=fs.readFileSync('pages/search.html','utf8');
  assert.match(search,/return matchesQuery\(book\) && \(terms\.length \|\| \(inSection && inFormat\)\)/);
  assert.match(search,/title===whole/);
  assert.match(search,/title\.length-whole\.length/);
  assert.match(search,/aria-label', '跳转页码'/);
  assert.match(search,/sm-filters" hidden/);
});
test('catalog categories support collapse and half-screen browsing', () => {
  const catalog=fs.readFileSync('pages/catalog.html','utf8');
  assert.match(catalog,/catalog-panel-toggle/);
  assert.match(catalog,/catalog-panel-mode/);
  assert.match(catalog,/ct-filter-panel\.is-half/);
});
test('member guide, recent books, replies, and announcements are wired into the reader experience', () => {
  const memberTools=fs.readFileSync('member-tools.js','utf8');
  const adminTools=fs.readFileSync('admin-community.js','utf8');
  assert.match(memberTools,/使用指南/);
  assert.match(memberTools,/近期新增/);
  assert.match(memberTools,/木木有话说/);
  assert.match(memberTools,/\/api\/announcement/);
  assert.match(adminTools,/\/api\/admin\/reviews\/reply/);
  assert.match(adminTools,/\/api\/admin\/announcement-image/);
  assert.match(adminTools,/已发出（读者可见）/);
  assert.match(adminTools,/删除勾选的消息/);
  assert.match(adminTools,/删除勾选的登记/);
  assert.match(adminTools,/xiaomuwu-collapse-member-messages/);
  assert.match(adminTools,/xiaomuwu-collapse-book-requests/);
  assert.match(fs.readFileSync('pages/recent.html','utf8'),/最近入库的 50 本书/);
  assert.match(fs.readFileSync('pages/messages.html','utf8'),/木木有话说/);
});
test('navigation excludes the unstable comics site and keeps auto-close and unread counters', () => {
  const preferences=fs.readFileSync('site-preferences.js','utf8');
  const preferencesCss=fs.readFileSync('site-preferences.css','utf8');
  const memberTools=fs.readFileSync('member-tools.js','utf8');
  assert.doesNotMatch(preferences,/manwa\.hezidh\.com/);
  assert.match(preferences,/function addMemberExternalLinks\(\)/);
  assert.match(preferences,/if\(data\.ok\)addMemberExternalLinks\(\)/);
  assert.match(preferences,/ao3\.href='\/go\/ao3'/);
  assert.match(preferences,/link\.href='\/go\/comics'/);
  assert.ok(preferences.indexOf("primaryNav.insertBefore(ao3,accountLinks)") < preferences.indexOf("primaryNav.insertBefore(comics,accountLinks)"), 'AO3 should sit immediately before the comics section');
  assert.match(preferencesCss,/\.gw-sidebar,\.sh-side\{[^}]*background-color:color-mix/);
  assert.match(preferencesCss,/\.gw-sidebar-bg,\.sh-side-bg\{[^}]*opacity:\.28!important/);
  assert.match(preferences,/timer=setTimeout\(hideSide,10000\)/);
  assert.match(preferences,/if\(side\.classList\.contains\('is-auto-hidden'\)\)showSide\(\);else hideSide\(\)/);
  assert.match(memberTools,/nav-unread-badge/);
  assert.match(memberTools,/xiaomuwu-messages-read-at/);
  assert.match(memberTools,/xiaomuwu-community-read-at/);
  assert.match(memberTools,/addReaderNavigation\(attempt\)/);
  assert.match(memberTools,/setTimeout\(function \(\) \{ addReaderNavigation\(attempt \+ 1\); \}/);
  assert.match(preferences,/Number\(data\.expiresInSeconds\)>0&&Number\(data\.expiresInSeconds\)<=86400/);
  assert.match(preferences,/您的卡密还有一天到期，如需续期找木木哦/);
  assert.match(preferencesCss,/\.site-side-reveal\.is-open\{[^}]*top:50%[^}]*translate\(-100%,-50%\)/);
});
test('online search remains available outside navigation, without a duplicate nav link', () => {
  const preferences=fs.readFileSync('site-preferences.js','utf8');
  const online=fs.readFileSync('pages/online-search.html','utf8');
  assert.doesNotMatch(preferences,/联网找书|onlineLink/);
  assert.match(online,/联网找书/);
  assert.match(fs.readFileSync('pages/home.html','utf8'),/href="\.\/search\.html"/);
});
test('online search results can be saved directly and still open in the in-site reader', () => {
  const online=fs.readFileSync('pages/online-search.html','utf8');
  assert.match(online,/link\.textContent='站内阅读'/);
  assert.match(online,/save\.textContent=saved\?'已在书架':'加入书架'/);
  assert.match(online,/onlineId:item\.id/);
  assert.match(online,/p:onlinePath\(item\)/);
  assert.match(online,/localStorage\.setItem\('xiaomuwu-shelf'/);
  assert.match(online,/render\(local\.items\|\|\[\],\[\],\{searching:true\}\)/);
  assert.match(online,/error\.name==='AbortError'\)throw error/);
});
test('home gate identifies the active card type and remaining downloads', () => {
  const home=fs.readFileSync('pages/home.html','utf8');
  assert.match(home,/下载卡 · 剩余 /);
  assert.match(home,/阅读卡 · 已验证/);
});
test('catalog page turns use portable scrolling and preserve the selected row on return', () => {
  const catalog=fs.readFileSync('pages/catalog.html','utf8');
  assert.doesNotMatch(catalog,/behavior:'instant'/);
  assert.match(catalog,/behavior:'auto'/);
  assert.match(catalog,/xiaomuwu-catalog-return/);
  assert.match(catalog,/state\.path/);
  assert.match(catalog,/returnParams\.set\('returnPage'/);
  assert.match(catalog,/returnParams\.get\('returnPage'\)/);
  assert.match(catalog,/returnParams\.set\('returnPath',book\.searchablePath\)/);
  assert.match(catalog,/encodeURIComponent\(returnUrl\)/);
  assert.match(html,/back\.addEventListener\('click'/);
  assert.match(html,/addEventListener\('popstate'/);
});
test('community comments allow a persistent reader display ID without exposing visitor tokens', () => {
  const community=fs.readFileSync('pages/community.html','utf8');
  const server=fs.readFileSync('scripts/server.py','utf8');
  assert.match(community,/community-display-id/);
  assert.match(community,/xiaomuwu-community-display-id/);
  assert.match(community,/displayId:displayId/);
  assert.match(community,/item\.display_id/);
  assert.match(server,/ALTER TABLE community_comments ADD COLUMN display_id/);
  assert.match(server,/comment\.pop\("visitor", None\)/);
});
test('admin can select a mobile gallery photo, preview it, and publish after upload', () => {
  const adminBoard=fs.readFileSync('community-board-admin.js','utf8');
  assert.match(adminBoard,/从手机相册选择图片/);
  assert.match(adminBoard,/accept="image\/jpeg,image\/png,image\/webp,image\/gif"/);
  assert.match(adminBoard,/community-image-preview/);
  assert.match(adminBoard,/file\.size > 3 \* 1024 \* 1024/);
  assert.match(adminBoard,/if \(imageBusy\)/);
  assert.match(adminBoard,/imageUrl: imageUrl/);
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
