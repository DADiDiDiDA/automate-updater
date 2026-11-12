# PROJECT · 项目地图（给 AI / 开发者）

> 本文面向「需要接着改这份代码」的人或 AI。读完应该能回答：代码在哪、一次检查/更新怎么走、哪些约定不能破、改完怎么验证。
> 人类用户请看 [README.md](README.md)，版本变更请看 [update.md](update.md)。

**30 秒版**：`core.py` 是不带 Qt 的纯逻辑引擎（扫描 / 下载 / 解压 / 覆盖 / 回滚 / 网络），`app.py` 是窗口 + 6 个后台 worker + 单实例入口，`pages.py` 是首页/列表/设置与弹窗。业务判定集中在 `core.py`；**下载与镜像策略经过多次返工，改之前务必读第 8 节的不变量**。

---

## 1. 定位与边界

| 做什么 | 不做什么 |
| --- | --- |
| 扫描本机若干根目录，识别其中的 git 仓库 / GitHub Release 绿色程序，比对版本并更新 | 不做游戏日常检查（那是姊妹项目 [daily-checker](https://github.com/DADiDiDiDA/daily-checker) 的职责） |
| 自动代理探测、镜像测速、社区加速节点同步、断点续传、校验、备份与回滚 | 不碰未绑定的程序、不改动用户的文件夹名称、不安装系统级依赖 |
| 托盘常驻、每天一次自动检查、通知与静默更新 | 不常驻联网轮询；每天一次用日期闸门控制 |

两个仓库 `daily-checker` / `automate-updater` 的 `app.py` 前 ~110 行、`theme.py`、`backdrop.py`、`widgets.py` 大量代码同源；在一个项目修了通用控件的坑，评估是否同步到另一个。

## 2. 运行环境

- Windows 10/11（`ctypes` 亚克力、注册表自启、托盘、`tasklist` 判断进程占用、`CREATE_NO_WINDOW`）
- Python 3 + `PySide6`；`pillow`（图标）；`pyinstaller`（打包）；`py7zr` 或本机 7-Zip（`.7z` 解压）；`git`（仓库类程序）
- `core.py` **只依赖标准库**（`urllib` / `zipfile` / `concurrent.futures` / `subprocess` / `threading`…），不 import PySide6，可独立测试
- 子进程一律带 `SILENT = {"creationflags": 0x08000000}`（Windows），禁止弹控制台窗口

## 3. 文件地图

| 文件 | 层 | 职责 / 关键入口 |
| --- | --- | --- |
| `core.py` | 逻辑 | 常量与 `BUILTIN_PROGRAMS`、`default_config()` / `_normalize()`、镜像与节点管理、`Item` dataclass、`Engine`（扫描/更新/回滚/通知）、`download()`、解压与覆盖工具 |
| `app.py` | 应用 | `ScanWorker` / `UpdateWorker` / `UndoWorker` / `MirrorWorker` / `NodesSyncWorker`；`MainWindow`、`AssetDialog`、`BindDialog`、单实例、`main()`、`_excepthook` |
| `pages.py` | 页面 | `HomePage`、`ItemListView`（列表行直接持有 `rows`）、`SettingsPage`、各 `ThemedDialog`（`MirrorDialog` / `PreserveDialog` / `VersionDialog` / `ExcludeDialog`）、`_date_ord` / `_time_ord` 排序辅助 |
| `widgets.py` | 控件 | 与姊妹项目同源的通用控件（`NumberStepper`、`WrapLabel`、`ThemedCombo`、`Card`、`Badge`…） |
| `theme.py` / `backdrop.py` | 主题 | 3 套皮肤 × 明暗；`tint()` / `flat()` / `scoped()`；玻璃背景实时取景 |
| `tray.py` | 托盘 | 图标（蓝紫渐变 + 白箭头 + 红点角标）、右键菜单、悬停提示 |
| `autostart.py` | 系统 | 开机自启注册表，值名 `AutomateUpdater` |
| `AutomateUpdater.spec` | 构建 | PyInstaller onefile，`assets/` 打进包，`console=False` |
| `assets/` · `tools/` | 资源 / 开发脚本 | 图标；诊断用脚本（多数被 `.gitignore` 排除） |

依赖方向（**单向**）：

```
app.py  ──>  pages.py  ──>  widgets.py ──> theme.py / backdrop.py
   │             │
   └──> core.py（纯逻辑，不认识任何 Qt 类） <── pages.py 也直接调用
```

`MainWindow` 持有 worker 实例并通过 `Signal` 回主线程；`ItemListView` 用行级 signal（`row_asset` 等）把操作冒泡给窗口，页面之间不互相引用。

## 4. 核心数据模型：`Item`（`core.py`）

| 字段组 | 字段 | 含义 |
| --- | --- | --- |
| 身份 | `key` / `name` / `kind`(git\|release) / `path` / `repo` | `key` 是列表与快照的唯一键 |
| 检查结果 | `status` / `local` / `remote` / `detail` / `published` / `notes` / `notes_url` | 见第 5 节状态机 |
| 资产 | `asset` / `asset_size` / `asset_digest` / `asset_regex` / `asset_pick` / `asset_options` / `download_url` / `anchor` | 见第 7 节资产选择 |
| 更新 | `preserve` / `version_spec` / `state` / `scanned` / `scanning` / `updated_at` | `version_spec` 描述本地版本怎么读 |
| UI | `progress` / `message` / `error` / `archived` / `local_size` / `local_manual` | `message` 是阶段/线路文字，**不要被纯进度刷新覆盖** |

`Item.to_dict()` 直接返回 `__dict__.copy()`；`Engine.SNAP_FIELDS` 是把结果存进 `records.snapshot` 供离线还原的白名单（`status / local / remote / detail / published / local_manual / local_size / asset / asset_size / asset_digest / download_url / anchor / updated_at`）。**新增需要离线还原的字段必须加进 `SNAP_FIELDS`**。

## 5. 状态机

| 字段 | 取值 | 语义 |
| --- | --- | --- |
| `status` | `unknown` · `update` · `latest` · `error` · `skip` · `unbound` · `choose` · `running` | 检查结果。`choose` = 多个安装包等用户选择，**禁止直接更新** |
| `state` | `idle` · `queued` · `running` · `done` · `failed` | 任务执行状态，与 `status` 正交 |
| `scanned` / `scanning` | bool | 本轮已检查 / 正在检查（列表显示「检查中」而不是「待检查」） |

状态到界面文案的映射在 `pages.py`（`choose` → 「待选择」/ warning）。失败时 `it.state = "failed"`，并按原因设 `status` 为 `error` 或 `skip`。

## 6. 配置结构（`config.json`，`version: 3`）

`load_config()` = `default_config()` + 用户文件浅合并 + `_normalize()` 归一化升级（旧字段清理、类型修正、枚举校验）。

| 段 | 关键键 |
| --- | --- |
| `roots[]` | `path` · `exclude[]`（只写文件夹名）· `depth` · `enabled` |
| `preserve` / `preserve_map` | 全局保留名单；程序名(小写) → `{"custom": true, "list": [...]}` |
| `proxy_ports` / `proxy` | 探测端口表；`mode` = auto / manual / off，`url`（**仅 `http://`**） |
| `manual_versions` | 程序名(小写) → 手动标记的本地版本 |
| `mirrors` | `enabled` · `list`（用户可编辑）· `seeded` · `daily_check` · `chosen` · `ranking`([[毫秒, url]]，`-1` = 未响应) · `ranking_sig`（候选 md5）· `auto_nodes` · `nodes_url` · `nodes` · `nodes_fetched` · `nodes_attempt` · `nodes_error` · `probe_before` · `daily` |
| `update` | `timeout_min`(10) · `install_mode`(clean/overwrite) · `retry_on_locked` · `retry_wait_min` |
| `background` | `close_to_tray` · `run_at_startup` · `check_enabled` · `auto_mode`(off/silent/notify) · `notify_methods[]` · `interval_min`（**v1.6.7 起不再使用，仅兼容**） |
| `backup` / `dingtalk` / `ui` / `programs` / `last_scan` | 备份份数、通知凭证（默认全空）、`ui.sort` + `ui.prior`、程序定制 |

**每日闸门**：`daily_state(cfg)` 读写 `mirrors.daily`，跨天自动清空，记录 `builtin` / `nodes` / `ranked` / `scan` 四段；`Engine.scan_pending()` 是启动与定时器的统一闸门，`mark_scan_done()` **只在未取消时**写。`background.interval_min` 与 `slow_switch` / `stall_policy` / `min_speed_kbps` 都是历史遗留，升级时清理，不要再读它们做决策。

## 7. 主流程

**启动（`app.py` / `MainWindow._startup_check`）**
1. 先 `_load_cached_items()`：`Engine.collect()` 出本地目录 + `apply_snapshot()` 还原 `records.snapshot`，**离线**把上次的结论铺到界面。
2. 当天已检查 → 结束，不联网；未检查 → `ScanWorker` 先 `daily_bootstrap()` 再 `scan_all()`。
3. 定时器（30 分钟一跳）只负责跨天补一次检查。手动「立即检查」/ 托盘「立即检查」不受闸门限制。

**每日准备（`Engine.daily_bootstrap`，顺序固定）**：内置加速地址测速 → `sync_nodes()` → `_probe_daily(force=True)`。当天跑过直接返回。
- `sync_nodes()`：从 `NODES_URL`（`hubporg/ghproxy-next/components/nodes.ts`）取 `value:` 域名，`parse_nodes_ts()` 做中文→punycode、过滤 `github.com`、去重；抓取走「代理 → 直连 → `top_mirrors(8)` 转发 `raw.githubusercontent.com`」，24 秒总时限，**不依赖用户开本地加速**。成功/失败都写 `nodes_attempt`（当天不重试），失败保留旧 `nodes`。节点单独存 `mirrors.nodes`，**不混入可编辑的 `list`**。
- `_probe_daily()`：`probe_mirrors()` 并发自适应测速，结果写 `mirrors.ranking` + `ranking_sig`（候选集合指纹，变了就重测）；`sort_by_ranking()` 顺序 = 快 → `-1` 未响应 → 未测速；`top_mirrors(6/8/12)` 分别供 API / 抓取 / 下载。

**检查（`Engine.collect()` → `scan_all(workers=6)`）**
- `find_git_repos()` / `find_program_dirs()` 按 roots + exclude + depth 收集目录；`sniff_program()` 匹配 `BUILTIN_PROGRAMS`（7 款内置精确规则）或从文件里 `sniff_repo()` 猜仓库。
- `_scan_git()`：`fetch` 后比落后提交数，**不论 behind 是否为 0 都写 `it.published`**（否则「按发布时间」排序会把已是最新的一大半挤到末尾）；`_git_changelog()` 收集提交日志。
- `_scan_release()`：`http_json()` 走镜像/直连回退拿 tag 与 assets，挑资产（下节），填 `download_url` / `asset_size` / `asset_digest` / `anchor`；`ver_gt()` 比较版本（数值分段）。
- 时间类排序用 `_date_ord()` 转 `YYYYMMDD` 数值、空日期垫底，特殊优先规则（`prior`）**降为同日次要键**，否则会整体打乱时间顺序。

**资产选择（优先级固定：用户正则 > 选中文件 > 自动规则）**
- `asset_candidates()`：只认 `.zip` / `.7z`，排除 `source` / `sha256` / `checksum` / `symbol`；Windows / `win` / `x64` / `x86_64` / `windows` 优先，体积降序。
- 多候选且无正则、无用户选择 → `it.status = "choose"`，检查结束后弹 `AssetDialog` 让用户点选，写入 `cfg.programs[].asset_pick`。**已是 `latest` 的程序不弹**。
- `pick_asset(assets, pattern, picked)`：有 `asset_regex` 就严格匹配（0 匹配直接失败），否则按上述自动规则。
- 跨版本命中：`_picked_pattern()` 把文件名里的版本号段替换成 `\d+(?:\.\d+)+`，所以 `v1.5.1` 选过的包，下一版 `v1.5.2` 照样认得。
- 右键「下载文件筛选…」写 `asset_regex`，对话框实时显示匹配数；填了就严格按正则，匹配不到明确报错，不偷偷选别的包。

**下载（`core.download`）**
1. 候选按测速顺序尝试；`on_source` **只在连接成功、确认可读后**触发（失败的候选不在界面上闪）。
2. `200` 且 `Content-Length != expect_size` → `_BadSource` 直接跳过（错误页镜像实测 557 字节）；`206` 校验 `Content-Range` 起点与总长。
3. 流式写 `.part`；`STALL_TIMEOUT = 20` 秒无任何数据才判断流，先**在同源用 `Range` 续传重试**（`DOWNLOAD_ATTEMPTS = 3`，只有 `progressed` 才重试），续不上才换候选。**完全不看速度**。
4. 下完用 `_verify_digest()` 校验：先 `_norm_digest()` 剥掉 `sha256:` / `SHA256:` / `sha256=` 前缀、只认 64 位 hex，认不出的格式**跳过校验**而不是判失败。
5. 失败原因写 `download.last_error`，界面显示「下载失败：<原因>」；内容类错误（`content_err`）优先于连接错误上报；进度映射 `0.02 + p*0.63 → 0.65` 留给解压。

**安装 / 回滚（`Engine._update_release`）**
`extract_archive()` → `locate_root()` 用 `anchor` 定位程序根目录（**解压后必须命中 anchor，否则中止且不动原目录**）→ `backup_dir()` 备份 → `clean_replace()`（clean）或 `copy_over()`（overwrite）→ `_stamp_update()` 写 records。
`retry_on_locked` 时用 `_wait_for_process_close()` / `_install_with_retry()` 每 3 秒重试，超时行按钮变「重试」，**不误标「已最新」**。任何异常回滚到备份，**绝不写 records、不标 latest**；撤销走 `undo_update()`，不要把 `_updater_undo.json` 拷回程序目录。

## 8. 关键不变量与已知坑（改动前必读）

**下载与镜像（返工最多，别回退）**
- GitHub API 的 `assets[].digest` 是 **`sha256:<hex>` 带前缀**，直接和 `hexdigest()` 比永远 `False` → 曾导致「每个源下满后判校验失败、删 `.part` 换源」，表现就是「进度到 60 多重置」。必须走 `_norm_digest()`。
- 不要恢复「速度低于 X KB/s 就换源」：`slow_switch` / `stall_policy` / `min_speed_kbps` 已彻底删除。断流判定只看「20 秒无写入」，且先同源续传。
- `progress_cb` **不要覆盖 `message`**（否则「下载中 · 线路」被刷掉）；`UpdateWorker` 用 `peak[item.key]` 记住最高进度，换源也不回退；纯进度刷新节流 0.12s。
- `app.py::_on_update_progress` 必须遍历 `(self.page_home.list, self.progs_list)`：**只有 `HomePage` 才包一层 `.list`，`ItemListView` 直接有 `rows`**。写错时 Qt 槽异常被吞，但 `_excepthook` 每个 chunk 落盘（实测 80 秒 2867 条 / 0.5MB）。异常钩子已按「异常类型 + 位置」60 秒合并计数。
- 镜像地址统一走 `normalize_mirror()` / `add_mirror()` / `remove_mirror()` / `reset_mirrors()`；手动代理**仅 `http://`**，不支持 socks5。
- 解压后 anchor 校验、失败回滚不写 records、取消显示「已取消」、预发布 tag 不顶掉稳定版——都是硬约束。

**Python / Qt 通用**
- **别拿模块名当局部变量**：`except Exception as re` 会让整个函数的 `re.sub(...)` 抛 `UnboundLocalError`（v1.6.1 让所有 Release 更新全废，v1.6.3 改名 `lock_err`）。可用 AST 静态检查守卫。
- `QLineEdit.end()` 在 PySide6 需显式 bool，按数字键可能抛异常 → 用 `setCursorPosition()`。
- Qt 槽里的异常被吞 → 装了 `_excepthook` 写 `crash.log`；界面正常不等于没抛异常。
- 无父级 `show()` 会弹顶层小窗；重建列表用 `hide()` + `removeWidget()` + `deleteLater()`，不要 `setParent(None)`。
- 无选择器的 `setStyleSheet` 会级联进 `QTipLabel` / 子按钮；内联样式走 `theme.scoped()` / `tint()` / `flat()`。`QListWidget` 在深色主题下需 `viewport().setAutoFillBackground(False)` + `QListWidget QWidget{background:transparent}`，否则是一块白底配浅字。
- 折行用 `widgets.WrapLabel`；`NumberStepper` 用透明 `QLineEdit` + `paintEvent` 画单位（输入时单位不消失）。
- 折行/短按钮别用「小 `setFixedHeight` + 大 padding」，会裁字。

**测试与截图**
- `ThemedCombo.set_value()` / `ToggleSwitch.setChecked()` 需 `emit=True`；离屏 `exec()` 要打桩；父窗口不 `show()` 时子 `isVisible()` 恒 `False`。
- UI 改动必须离屏完整构建 `MainWindow` 冒烟，`win._save_cfg` 打 no-op（否则测试写进真实配置）。
- 截图前把 `core.APP_ID` 改成带 `.shot` 的值防撞单实例；先 `THEME.set_mode("light")` + `refresh()` 再构造窗口；离屏中文变方块时用真实 `windows` 平台。

## 9. 构建与验证

```powershell
python -m PyInstaller AutomateUpdater.spec --noconfirm --clean
python app.py                 # 直接运行
python app.py --tray          # 后台静默启动（开机自启用这个）
```

- `.gitignore` 排除 `build/` `dist/` `dist_new/` `_test/` `config.json` `records.json` `crash.log` 与开发截图。
- 验证脚本放 `_test/`（不入库），命名惯例 `verify_au_v<版本>.py`；核心逻辑改动优先补纯 Python 断言，UI 改动再补离屏冒烟。
- 真机验证过的关键路径：`daily_bootstrap` 全流程（88 条候选中 44 条可用，约 20s）、138MB 资产下载 + 校验 + 进度不回退（约 18s）。
- 提交前确认：`config.json` / `records.json` 没被带上、没有真实凭证、版本号与 `core.APP_VERSION` 一致、`crash.log` 无新增异常。

## 10. 改动自查清单

1. 网络/下载改动是否仍遵守「不看速度、20 秒无写入、同源续传 3 次」？有没有把 `slow_switch` 类逻辑带回来？
2. 新增/修改的 `sha256` 校验是否走 `_norm_digest()`？
3. 新增需离线还原的 `Item` 字段是否加进 `SNAP_FIELDS`？
4. 进度回调有没有覆盖 `message`？换源时进度会不会回退？
5. 每日闸门是否仍统一走 `daily_state()` / `scan_pending()` / `mark_scan_done()`（取消时不写）？
6. 资产选择优先级是否仍是「用户正则 > 选中文件 > 自动规则」，且 `choose` 状态禁止更新？
7. 失败路径是否回滚、不写 records、不标 `latest`？
8. 解压后 anchor 校验、备份、占用重试是否都还在？
9. Qt 槽里新增代码会不会抛异常？`crash.log` 是否干净？
10. 新增配置键是否补进 `default_config()` 与 `_normalize()`，旧配置能否兼容？
