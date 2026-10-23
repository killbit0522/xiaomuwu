const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('pages/catalog.html','utf8');
const source=html.slice(html.indexOf('  // Pagination stays outside'),html.indexOf('  function renderFilters()'));
function harness(){
  function element(){return {style:{},children:[],setAttribute(){},append(...items){this.children.push(...items)},appendChild(item){this.children.push(item)},replaceChildren(...items){this.children=items},after(item){this.pager=item},getBoundingClientRect(){return {top:100}}};}
  const list=element(),stored=new Map();
  const ctx={document:{createElement:element,createDocumentFragment:element},list,books:Array.from({length:151},(_,i)=>({id:i})),active:'all',page:1,pageSize:50,totalPages:1,returnState:null,makeRow:b=>b,
    window:{scrollY:120,scrollTo(){}},sessionStorage:{setItem(k,v){stored.set(k,v)}},requestAnimationFrame:fn=>fn()};
  vm.createContext(ctx);
  vm.runInContext(html.slice(html.indexOf('  function saveBrowseState()'),html.indexOf('  function isUnlocked()'))+source,ctx);
  ctx.render();return {ctx,list,stored};
}
test('directory next/previous keep the same buttons and persist their page',()=>{
  const {ctx,list,stored}=harness(),pager=list.pager,button=ctx.next;
  let stopped=0;const event={preventDefault(){stopped++},stopPropagation(){stopped++}};
  ctx.next.onclick(event);assert.equal(ctx.page,2);assert.equal(ctx.label.textContent,'2 / 4 · 共 151 本');
  assert.equal(list.children[0].children[0].id,50);
  assert.equal(list.pager,pager);assert.equal(ctx.next,button);
  assert.equal(JSON.parse(stored.get('xiaomuwu-catalog-browse')).page,2);
  ctx.previous.onclick(event);assert.equal(ctx.page,1);assert.equal(stopped,4);
});
test('directory page requests clamp to valid integer pages',()=>{
  const {ctx}=harness();
  ctx.changePage(999);assert.equal(ctx.page,4);assert.equal(ctx.next.disabled,true);
  ctx.changePage(-10);assert.equal(ctx.page,1);assert.equal(ctx.previous.disabled,true);
  ctx.changePage(2.8);assert.equal(ctx.page,2);
});
test('repeated directory pagination never navigates away',()=>{
  const {ctx}=harness();
  Object.defineProperty(ctx.window,'location',{set(){throw Error('Unexpected navigation')}});
  for(let i=0;i<20;i++){ctx.next.onclick();ctx.previous.onclick();}
  assert.equal(ctx.page,1);
  assert.doesNotMatch(html,/location\.href=['"]\/pages\/home\.html\?card=required/);
});
