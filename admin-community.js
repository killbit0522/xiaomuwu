(function () {
  'use strict';
  if (!/\/pages\/admin\.html$/.test(location.pathname)) return;
  var dashboard = document.getElementById('dashboard');
  if (!dashboard || document.getElementById('community-tools')) return;
  var panel = document.createElement('section'); panel.id = 'community-tools'; panel.className = 'panel';
  panel.innerHTML = '<h2>会员公告</h2><p class="muted">公告会在有效阅读卡用户进入网站时展示；它是站内公告，不会向手机系统发送通知。</p><label>标题<input id="announcement-title" maxlength="80" placeholder="例如：本周推荐"></label><label>内容<textarea id="announcement-body" maxlength="3000" rows="4" placeholder="写给会员的消息"></textarea></label><label>配图（可选，5MB 以内）<input id="announcement-image" type="file" accept="image/jpeg,image/png,image/webp,image/gif"></label><p id="announcement-image-status" class="muted"></p><button id="announcement-send" type="button">发布给所有会员</button><p id="announcement-status" class="muted"></p><hr><h2>想看类型 · 回复</h2><p class="muted">读者会在“想看类型”里看到你的公开回复，请勿写入个人隐私。</p><div id="admin-type-requests"></div>';
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
    fetch('/api/admin/announcement', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(function (response) { if (!response.ok) throw Error(); return response.json(); }).then(function () { status.textContent = '已发布。会员下次进入网站时会看到。'; }).catch(function () { status.textContent = '发布失败，请确认标题和内容都已填写。'; }).finally(function () { button.disabled = false; });
  };
  function loadRequests() {
    fetch('/api/admin/data', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
      if (!data) return; var box = document.getElementById('admin-type-requests'); box.replaceChildren();
      (data.reviews || []).forEach(function (item) {
        var row = document.createElement('article'); row.className = 'item'; var title = document.createElement('h3'); title.textContent = item.title || item.book || '想看的类型'; var body = document.createElement('p'); body.textContent = item.body || '';
        var reply = document.createElement('textarea'); reply.rows = 2; reply.placeholder = '回复给读者（公开可见）'; reply.value = item.admin_reply || '';
        var save = document.createElement('button'); save.type = 'button'; save.textContent = item.admin_reply ? '更新回复' : '回复';
        save.onclick = function () { save.disabled = true; fetch('/api/admin/reviews/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: item.id, reply: reply.value }) }).then(function (response) { if (!response.ok) throw Error(); save.textContent = '已保存'; }).catch(function () { save.textContent = '保存失败'; }).finally(function () { save.disabled = false; }); };
        row.append(title, body, reply, save); box.appendChild(row);
      });
      if (!(data.reviews || []).length) box.textContent = '暂时没有读者留言。';
    }).catch(function () {});
  }
  loadRequests(); setInterval(loadRequests, 30000);
})();
