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
  for (const file of ['reading-support.js','recent-reading.js']) new vm.Script(fs.readFileSync(file,'utf8'));
  for (const file of ['pages/home.html','pages/bookshelf.html','pages/online-search.html']) {
    for (const match of fs.readFileSync(file,'utf8').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  }
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
