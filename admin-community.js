(function () {
  'use strict';
  if (!/\/pages\/admin\.html$/.test(location.pathname)) return;
  if (window.cabinCommunityVersion === '20261012') return;
  window.cabinCommunityVersion = '20261012';
  var dashboard = document.getElementById('dashboard');
  if (!dashboard) return;
  var oldPanel = document.getElementById('community-tools');
  if (oldPanel) oldPanel.remove();
  var panel = document.createElement('section'); panel.id = 'community-tools'; panel.className = 'panel';
  panel.innerHTML = '<h2>木木有话说</h2><p class="muted">每次发布都会永久保存为一条消息；有效阅读会员可从导航的“木木有话说”随时查看。最新一条也会在会员进入时提示。</p><label>标题<input id="announcement-title" maxlength="80" placeholder="例如：本周推荐"></label><label>正文<textarea id="announcement-body" maxlength="3000" rows="4" placeholder="写给会员的消息"></textarea></label><label>配图（可选，5MB 以内）<input id="announcement-image" type="file" accept="image/jpeg,image/png,image/webp,image/gif"></label><p id="announcement-image-status" class="muted"></p><button id="announcement-send" type="button">发布给所有会员</button><p id="announcement-status" class="muted"></p><div class="card-tools"><button id="delete-selected-messages" class="delete-btn" type="button">删除勾选的消息</button><span id="message-action-status" class="muted"></span></div><div id="admin-member-messages"></div><hr><h2>想看类型 · 回复</h2><p class="muted">读者会在“想看类型”里看到你的公开回复，请勿写入个人隐私。点击“回复”后显示“已发出”，代表读者端已能读取。</p><div class="card-tools"><button id="delete-selected-reviews" class="delete-btn" type="button">删除勾选的留言</button><span id="review-action-status" class="muted"></span></div><div id="admin-type-requests"></div><hr><h2>缺书登记</h2><p class="muted">勾选已经处理或不再需要的登记后删除。</p><div class="card-tools"><button id="delete-selected-book-requests" class="delete-btn" type="button">删除勾选的登记</button><span id="request-action-status" class="muted"></span></div><div id="admin-book-requests"></div>';
  dashboard.insertBefore(panel, dashboard.querySelector('section') || null);
  var imageUrl = '';
  var imageInput = document.getElementById('announcement-image');
  var imageStatus = document.getElementById('announcement-image-status');
  imageInput.addEventListener('change', function () {
    var file = imageInput.files && imageInput.files[0]; if (!file) return;
    if (file.size > 5 * 1024 * 1024) { imageStatus.textContent = '图片不能超过 5MB。'; return; }
    imageStatus.textContent = '正在上传图片…';
    fetch('/api/admin/announcement-image', { method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name) }, body: file }).then(function (response) { if (!response.ok) throw Error(); return response.json(); }).then(function (data) { imageUrl = data.imageUrl || ''; imageStatus.textContent = imageUrl ? '图片已准备好，会随公告一起发布。' : '图片上传失败。'; }).catch(function () { imageStatus.textContent = '图片上传失败，请重试。'; });
  });
  document.getElementById('announcement-send').onclick = function () {
    var button = this, status = document.getElementById('announcement-status');
    var payload = { title: document.getElementById('announcement-title').value.trim(), body: document.getElementById('announcement-body').value.trim(), imageUrl: imageUrl };
    button.disabled = true; status.textContent = '正在发布…';
    fetch('/api/admin/announcement', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(function (response) { if (!response.ok) throw Error(); return response.json(); }).then(function () { status.textContent = '已发布并保存。会员可以在“木木有话说”里持续查看。'; document.getElementById('announcement-title').value=''; document.getElementById('announcement-body').value=''; imageInput.value=''; imageUrl=''; imageStatus.textContent=''; loadMessages(); }).catch(function () { status.textContent = '发布失败，请确认标题和内容都已填写。'; }).finally(function () { button.disabled = false; });
  };
  function loadMessages() {
    fetch('/api/admin/messages', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : { items: [] }; }).then(function (data) {
      var box = document.getElementById('admin-member-messages'); box.replaceChildren();
      var heading = document.createElement('h3'); heading.textContent = '已发布的消息'; box.appendChild(heading);
      (data.items || []).slice(0, 100).forEach(function (item) { var row=document.createElement('article'), choose=document.createElement('label'), checkbox=document.createElement('input');row.className='item';checkbox.type='checkbox';checkbox.className='message-select';checkbox.value=item.id||'';choose.append(checkbox,document.createTextNode(' 选择这条消息'));var title=document.createElement('strong');title.textContent=item.title||'未命名';var date=document.createElement('div');date.className='muted';date.textContent=item.updatedAt||'';var body=document.createElement('p');body.textContent=item.body||'';row.append(choose,title,date,body);box.appendChild(row); });
      if (!(data.items || []).length) { var empty=document.createElement('p');empty.className='muted';empty.textContent='暂未发布消息。';box.appendChild(empty); }
    }).catch(function () {});
  }
  function loadRequests() {
    fetch('/api/admin/data', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
      if (!data) return; var box = document.getElementById('admin-type-requests'); box.replaceChildren();
      (data.reviews || []).forEach(function (item) {
        var row = document.createElement('article'), choose=document.createElement('label'), checkbox=document.createElement('input'); row.className = 'item'; checkbox.type='checkbox';checkbox.className='review-select';checkbox.value=item.id;choose.append(checkbox,document.createTextNode(' 选择这条留言')); var title = document.createElement('h3'); title.textContent = item.title || item.book || '想看的类型'; var body = document.createElement('p'); body.textContent = item.body || '';
        var reply = document.createElement('textarea'); reply.rows = 2; reply.placeholder = '回复给读者（公开可见）'; reply.value = item.admin_reply || '';
        var save = document.createElement('button'); save.type = 'button'; save.textContent = item.admin_reply ? '更新回复' : '回复';
        save.onclick = function () { save.disabled = true; fetch('/api/admin/reviews/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: item.id, reply: reply.value }) }).then(function (response) { if (!response.ok) throw Error(); return response.json(); }).then(function () { save.textContent = '已发出（读者可见）'; document.getElementById('review-action-status').textContent='回复已保存，读者端现在可以查看。'; loadRequests(); }).catch(function () { save.textContent = '发送失败'; }).finally(function () { save.disabled = false; }); };
        row.append(choose,title, body, reply, save); box.appendChild(row);
      });
      if (!(data.reviews || []).length) box.textContent = '暂时没有读者留言。';
    }).catch(function () {});
  }
  function loadBookRequests() {
    fetch('/api/admin/data', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
      if (!data) return; var box=document.getElementById('admin-book-requests');box.replaceChildren();
      (data.requests || []).forEach(function(item){var row=document.createElement('article'),choose=document.createElement('label'),checkbox=document.createElement('input'),title=document.createElement('h3'),date=document.createElement('div'),detail=document.createElement('p');row.className='item';checkbox.type='checkbox';checkbox.className='book-request-select';checkbox.value=item.id;choose.append(checkbox,document.createTextNode(' 选择这条登记'));title.textContent='《'+(item.title||'未命名')+'》';date.className='muted';date.textContent=item.created_at||'';detail.textContent='作者：'+(item.author||'未填写');row.append(choose,title,date,detail);box.appendChild(row)});
      if (!(data.requests || []).length) box.appendChild(Object.assign(document.createElement('p'),{className:'muted',textContent:'暂时没有缺书登记。'}));
    }).catch(function(){});
  }
  function selectedValues(selector) { return Array.prototype.map.call(document.querySelectorAll(selector+':checked'), function(input){return input.value}).filter(Boolean); }
  function deleteSelected(selector, endpoint, statusId, label, refresh) {
    var ids=selectedValues(selector),status=document.getElementById(statusId);if(!ids.length){status.textContent='请先勾选需要删除的'+label+'。';return;}
    if(!confirm('确定删除选中的 '+ids.length+' 条'+label+'吗？此操作不能恢复。'))return;
    fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:ids})}).then(function(response){if(!response.ok)throw Error();return response.json()}).then(function(data){status.textContent='已删除 '+(data.deleted||0)+' 条'+label+'。';refresh()}).catch(function(){status.textContent='删除失败，请刷新后重试。'});
  }
  document.getElementById('delete-selected-messages').onclick=function(){deleteSelected('.message-select','/api/admin/messages/delete','message-action-status','消息',loadMessages)};
  document.getElementById('delete-selected-reviews').onclick=function(){deleteSelected('.review-select','/api/admin/reviews/delete','review-action-status','留言',loadRequests)};
  document.getElementById('delete-selected-book-requests').onclick=function(){deleteSelected('.book-request-select','/api/admin/requests/delete','request-action-status','缺书登记',loadBookRequests)};
  function installCardDeleteControls() {
    var readerList=document.getElementById('reader-card-list'),downloadButton=document.getElementById('delete-selected-cards');if(downloadButton){downloadButton.textContent='删除勾选的卡密';downloadButton.onclick=function(){var codes=selectedValues('.card-select'),message=document.getElementById('card-action-message');if(!codes.length){message.textContent='请先勾选卡密。';return}if(!confirm('确定删除选中的 '+codes.length+' 张卡密吗？删除后立即失效且不能恢复。'))return;fetch('/api/admin/cards/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({codes:codes})}).then(function(response){if(!response.ok)throw Error();return response.json()}).then(function(data){message.textContent='已删除 '+data.deleted+' 张卡密。';location.reload()}).catch(function(){message.textContent='删除失败，请刷新后重试。'})}}if(!readerList)return;var section=readerList.closest('section');if(!section||document.getElementById('reader-card-delete-selected'))return;var tools=document.createElement('div'),button=document.createElement('button'),status=document.createElement('span');tools.className='card-tools';button.id='reader-card-delete-selected';button.type='button';button.className='delete-btn';button.textContent='删除勾选的阅读卡';status.id='reader-card-action-status';status.className='muted';tools.append(button,status);section.querySelector('h2').after(tools);button.onclick=function(){var codes=Array.prototype.map.call(section.querySelectorAll('.card-select:checked'),function(input){return input.value}),message=status;if(!codes.length){message.textContent='请先勾选阅读卡。';return}if(!confirm('确定删除选中的 '+codes.length+' 张阅读卡吗？删除后立即失效且不能恢复。'))return;fetch('/api/admin/cards/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({codes:codes})}).then(function(response){if(!response.ok)throw Error();return response.json()}).then(function(data){message.textContent='已删除 '+data.deleted+' 张阅读卡。';location.reload()}).catch(function(){message.textContent='删除失败，请刷新后重试。'})};
  }
  loadRequests(); loadMessages(); loadBookRequests(); setTimeout(installCardDeleteControls, 0); setInterval(function(){loadRequests();loadMessages();loadBookRequests();installCardDeleteControls()}, 30000);
})();
