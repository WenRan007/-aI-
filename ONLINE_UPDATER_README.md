# 常客 AI 在线更新器

`online_updater.py` 是独立的 Windows 更新器。它不依赖网站登录，也不会从另一台电脑抓取文件；更新包必须放在团队电脑可以访问的 HTTPS 地址、GitHub Release 或阿里云 OSS 上。

## 发布步骤

1. 把完整的 `常客AI2.2.zip` 或增量 ZIP 上传到 HTTPS 文件地址。GitHub 仓库适合放清单和小补丁；约 3.34GB 的完整包超过普通 Git 文件限制，建议用 GitHub Release 分片资产或阿里云 OSS。
2. 复制 `update-manifest.example.json` 为 `update-manifest.json`，填写 `version`、`package_url` 和 ZIP 的 SHA-256。不能把 API Key 放进完整包或清单。
3. 把 `update-manifest.json` 放到当前约定的 GitHub 地址：
   `https://raw.githubusercontent.com/WenRan007/-aI-/main/update-manifest.json`
4. 把打包出的 `常客AI在线更新器.exe` 和旁边的 `online_update_config.json` 发给团队。更新器会自动读取上述清单；管理员只需修改 JSON 中的 `manifest_url` 就能切换发布源，无需重新编译。

也可以直接双击 `常客AI在线更新.bat`。它会自动从 GitHub 下载最新更新器并启动，不需要团队成员手动复制补丁。

更新包为全量 ZIP 时，清单所在版本首次运行即可安装；更新包为增量 ZIP 时，目标电脑必须已经有 `_internal` 和 `常客AI2.2.exe`。更新前会备份并保留 `models_cache`、`stickers`、`temp_audio`、配置和密钥。

## 本地测试

可以把 `package_url` 写成 ZIP 的 `file:///C:/...` 地址，然后选择本地 manifest。在线使用时请改成 HTTPS，不能依赖某一台电脑的本地路径。
