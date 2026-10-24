(function () {
  'use strict';

  if (!/\/pages\/admin\.html$/.test(location.pathname) || document.getElementById('community-board-admin')) return;
  var dashboard = document.getElementById('dashboard');
  if (!dashboard) return;

  var panel = document.createElement('section');
  panel.id = 'community-board-admin';
  panel.className = 'panel';
  panel.innerHTML = '<h2>木木小广场</h2>' +
    '<p class="muted">这里和“木木有话说”分开：你发布图文感受，所有有效阅读会员可在下方评论。图片限制 3MB，并按需加载以节省流量。</p>' +
    '<label>标题<input id="community-post-title" maxlength="80" placeholder="例如：今天想和大家分享"></label>' +
    '<label>感受<textarea id="community-post-body" maxlength="3000" rows="4" placeholder="写下想说的话"></textarea></label>' +
    '<div style="margin:12px 0"><strong>添加手机图片（可选）</strong>' +
    '<p class="muted">支持从手机相册选择 JPG、PNG、WebP 或 GIF，单张不超过 3MB。</p>' +
    '<input id="community-post-image" type="file" accept="image/jpeg,image/png,image/webp,image/gif" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none" tabindex="-1" aria-label="选择手机图片">' +
    '<button id="community-image-choose" type="button">从手机相册选择图片</button> ' +
    '<button id="community-image-clear" type="button" hidden>移除图片</button>' +
    '<div id="community-image-preview-wrap" hidden style="margin-top:10px"><img id="community-image-preview" alt="待发布图片预览" style="display:block;max-width:min(100%,420px);max-height:320px;object-fit:contain;border-radius:10px;border:1px solid #aaa"></div>' +
    '</div><p id="community-image-status" class="muted" aria-live="polite"></p>' +
    '<button id="community-post-send" type="button">发布到木木小广场</button><p id="community-post-status" class="muted" aria-live="polite"></p><div id="community-post-list"></div>';
  dashboard.insertBefore(panel, dashboard.querySelector('section') || null);

  var heading = panel.querySelector('h2'), toggle = document.createElement('button'), content = document.createElement('div');
  var collapseKey = 'xiaomuwu-collapse-community-board';
  while (heading.nextSibling) content.appendChild(heading.nextSibling);
  toggle.type = 'button';
  toggle.className = 'panel-toggle';
  heading.appendChild(toggle);
  heading.after(content);
  function setCollapsed(collapsed) {
    content.hidden = collapsed;
    toggle.textContent = collapsed ? '展开' : '收起';
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    try { localStorage.setItem(collapseKey, collapsed ? '1' : '0'); } catch (_) {}
  }
  try { setCollapsed(localStorage.getItem(collapseKey) === '1'); } catch (_) { setCollapsed(false); }
  toggle.onclick = function () { setCollapsed(!content.hidden); };

  var imageUrl = '', previewUrl = '', imageBusy = false;
  var imageInput = content.querySelector('#community-post-image');
  var imageStatus = content.querySelector('#community-image-status');
  var chooseImage = content.querySelector('#community-image-choose');
  var clearImage = content.querySelector('#community-image-clear');
  var previewWrap = content.querySelector('#community-image-preview-wrap');
  var preview = content.querySelector('#community-image-preview');
  chooseImage.onclick = function () { imageInput.click(); };
  clearImage.onclick = function () {
    imageUrl = '';
    imageInput.value = '';
    clearImage.hidden = true;
    previewWrap.hidden = true;
    preview.removeAttribute('src');
    imageStatus.textContent = '已移除图片。';
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
  };
  imageInput.onchange = function () {
    var file = imageInput.files && imageInput.files[0];
    if (!file) return;
    imageUrl = '';
    if (file.size > 3 * 1024 * 1024) {
      imageInput.value = '';
      imageStatus.textContent = '图片不能超过 3MB，请压缩后再选择。';
      return;
    }
    var allowedExtension = /\.(jpe?g|png|webp|gif)$/i.test(file.name);
    var allowedMime = !file.type || /^image\/(jpeg|png|webp|gif)$/i.test(file.type);
    if (!allowedExtension || !allowedMime) {
      imageInput.value = '';
      imageStatus.textContent = '暂不支持此图片格式，请选择 JPG、PNG、WebP 或 GIF。';
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    previewWrap.hidden = false;
    clearImage.hidden = true;
    imageBusy = true;
    chooseImage.disabled = true;
    imageStatus.textContent = '正在上传图片…';
    fetch('/api/admin/community-image', {
      method: 'POST',
      headers: { 'X-Filename': encodeURIComponent(file.name) },
      body: file
    }).then(function (response) {
      if (!response.ok) return response.json().catch(function () { return {}; }).then(function (data) {
        throw new Error(data.message || '图片上传失败，请重试。');
      });
      return response.json();
    }).then(function (data) {
      imageUrl = data.imageUrl || '';
      if (!imageUrl) throw new Error('图片上传失败，请重试。');
      imageStatus.textContent = '图片已准备好，发布动态后会员即可看到。';
      clearImage.hidden = false;
    }).catch(function (error) {
      imageUrl = '';
      imageStatus.textContent = error.message || '图片上传失败，请重试。';
    }).finally(function () {
      imageBusy = false;
      chooseImage.disabled = false;
    });
  };

  function time(value) { return String(value || '').replace('T', ' ').replace(/\.\d+\+00:00$/, ''); }
  function load() {
    fetch('/api/admin/community', { cache: 'no-store' }).then(function (response) {
      if (!response.ok) throw new Error('加载失败');
      return response.json();
    }).then(function (data) {
      var box = content.querySelector('#community-post-list');
      box.replaceChildren();
      var title = document.createElement('h3');
      title.textContent = '已发布动态';
      box.appendChild(title);
      (data.items || []).forEach(function (post) {
        var row = document.createElement('article'), head = document.createElement('strong');
        var date = document.createElement('div'), body = document.createElement('p'), remove = document.createElement('button');
        row.className = 'item';
        head.textContent = post.title || '未命名';
        date.className = 'muted';
        date.textContent = time(post.created_at);
        body.textContent = post.body || '';
        remove.type = 'button';
        remove.className = 'delete-btn';
        remove.textContent = '删除这条动态';
        remove.onclick = function () {
          if (!confirm('确定删除这条动态和它的全部评论吗？')) return;
          fetch('/api/admin/community/posts/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: post.id }) })
            .then(function (response) { if (!response.ok) throw new Error(); load(); })
            .catch(function () { alert('删除失败，请刷新后重试。'); });
        };
        row.append(head, date, body);
        if (post.image_url) {
          var postImage = document.createElement('img');
          postImage.src = post.image_url;
          postImage.alt = '动态配图';
          postImage.loading = 'lazy';
          postImage.style.cssText = 'display:block;max-width:min(100%,420px);max-height:320px;object-fit:contain;border-radius:10px;margin:8px 0';
          row.appendChild(postImage);
        }
        row.appendChild(remove);
        (post.comments || []).forEach(function (comment) {
          var commentRow = document.createElement('div'), commentText = document.createElement('p');
          var commentDate = document.createElement('span'), commentDelete = document.createElement('button');
          commentRow.className = 'item';
          commentText.textContent = '会员：' + (comment.display_id || '读者') + '：' + (comment.body || '');
          commentDate.className = 'muted';
          commentDate.textContent = time(comment.created_at);
          commentDelete.type = 'button';
          commentDelete.className = 'copy-btn delete-btn';
          commentDelete.textContent = '删除评论';
          commentDelete.onclick = function () {
            fetch('/api/admin/community/comments/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: comment.id }) })
              .then(function (response) { if (!response.ok) throw new Error(); load(); })
              .catch(function () { alert('删除失败，请刷新后重试。'); });
          };
          commentRow.append(commentText, commentDate, commentDelete);
          row.appendChild(commentRow);
        });
        box.appendChild(row);
      });
      if (!(data.items || []).length) {
        var empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = '暂未发布动态。';
        box.appendChild(empty);
      }
    }).catch(function () {});
  }

  content.querySelector('#community-post-send').onclick = function () {
    var button = this, status = content.querySelector('#community-post-status');
    var title = content.querySelector('#community-post-title').value.trim();
    var body = content.querySelector('#community-post-body').value.trim();
    if (imageBusy) { status.textContent = '图片还在上传，请稍等。'; return; }
    button.disabled = true;
    status.textContent = '正在发布…';
    fetch('/api/admin/community/posts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title, body: body, imageUrl: imageUrl })
    }).then(function (response) {
      if (!response.ok) throw new Error();
      return response.json();
    }).then(function () {
      status.textContent = '已发布，会员现在可以评论。';
      content.querySelector('#community-post-title').value = '';
      content.querySelector('#community-post-body').value = '';
      imageInput.value = '';
      imageUrl = '';
      imageStatus.textContent = '';
      clearImage.hidden = true;
      previewWrap.hidden = true;
      preview.removeAttribute('src');
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = '';
      load();
    }).catch(function () {
      status.textContent = '发布失败，请填写标题和感受后重试。';
    }).finally(function () { button.disabled = false; });
  };

  load();
  setInterval(load, 30000);
})();
