# 小木屋找书网站

## 本机运行

运行 `start-local-site.ps1`，然后打开 `http://127.0.0.1:4173/pages/home.html`。默认读取桌面 `textbook`，新增、修改、删除和后台上传都会自动同步。

管理后台：`/pages/admin.html`。初始管理员密码：`MUMU-ADMIN-2026`，登录后请立即修改。

## 云端部署必须满足的条件

本项目不是纯静态网站。卡密、阅读期限、下载次数、缺书登记、读后感、文件上传和自动书目同步都依赖 Python 服务，因此不能只上传到 GitHub Pages 等静态托管。

请选择支持以下能力的云服务器或容器平台：

- 能持续运行 Python；
- 能挂载持久磁盘；
- 可以把持久磁盘的两个目录分别用于数据库和书库；
- 建议只运行一个网站实例，因为当前使用 SQLite；
- 反向代理需允许最大 100 MB 的上传请求。

启动命令：

```bash
python scripts/server.py
```

建议设置这些环境变量：

```text
PORT=平台提供的端口
TEXTBOOK_ROOT=/data/textbook
CABIN_DATA_DIR=/data/database
CABIN_ADMIN_KEY=首次部署使用的管理员初始密码
CABIN_SESSION_SECRET=一串至少32位的随机字符
```

将 `/data` 挂载为持久磁盘。这样服务器重启、重新发布代码后，以下内容仍然保留：

- 阅读卡和下载卡；
- 阅读卡到期时间和下载卡剩余次数；
- 缺书登记与读后感；
- 后台上传及原有书籍。

健康检查地址为 `/health`，首页为 `/pages/home.html`。

## 权限说明

- 阅读卡按月生成：可在线阅读 TXT/PDF、加入书架，不能下载本地文件。
- 下载卡按次数生成：可下载本地文件，不能阅读本地书或使用书架。
- 两类卡都可以搜索并跳转外部网站；外部跳转不扣下载次数。
- 书架保存在读者自己的浏览器中。更换手机、浏览器或清除浏览器数据后，个人书架需要重新添加；卡密和后台数据不受影响。

## 云端备份

至少定期备份：

- `${CABIN_DATA_DIR}/library.db`
- `${TEXTBOOK_ROOT}` 整个目录

不要把 `CABIN_ADMIN_KEY`、`CABIN_SESSION_SECRET` 或数据库文件提交到公开代码仓库。
