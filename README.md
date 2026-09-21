# 萝卜盒 · LuoboBox

**codebuddy2api 本地网关的 Windows 桌面控制台。** 把网关跑起来、开关公网入口、
把 Codex / Claude Code 接到网关、网关升级后自动补回必需的脱敏补丁 —— 一个托盘图标全搞定。

> 自用工具。不修改 codebuddy2api 的源码结构（脱敏词表补丁例外，且改前必先备份）。

**官网下载**：<https://luobox.aifeng.icu/>　·　**源码仓库**：<https://github.com/lylguang/LuoboBox>

---

## 为什么需要它

原来跑这个网关要记一长串事情：双击哪个 bat、改 Key 要同步三个文件、
升级之后必须手动重打 `desensitize.py` 补丁否则 Codex 立刻被上游拦、
Funnel 挂了要手动 `tailscale funnel --bg` 重挂、服务莫名退出要看日志找 `0xC000013A`……

萝卜盒把这些都收进一个托盘程序里。**核心不是"好看"，是"不会再忘"。**

---

## 功能

| 模块 | 做什么 |
|---|---|
| **托盘常驻** | 启动/停止/重启网关、开关公网入口、复制 Key、**复制接入包**、立即签到、检查更新（**发现新版本时托盘菜单与悬浮提示直接带角标**）、开机自启 |
| **状态面板** | 网关状态、端口、PID、可用模型数、响应延迟、脱敏补丁健康度、凭证池健康与积分 |
| **首屏状态环** | 96px 自绘状态环 + 大状态字 + 双主按钮，打开就知道「现在什么状态、下一步点哪」 |
| **命令面板** | `Ctrl+K` 一个输入框模糊匹配**任意页签与动作**，不用记功能藏在哪个标签页 |
| **全局快捷键** | `Ctrl+1..N` 切页签 · `Ctrl+K` 命令面板 · `Ctrl+R` 重启网关 · `Ctrl+Shift+C` 复制接入包 · `F5` 刷新 · `Ctrl+,` 设置 |
| **外观与主题** | 暗 / 亮双基调 + **4 套强调色**（萝卜红 / 青柠 / 电紫 / 湖蓝）+ **三档字号**（标准 / 大 / 特大），切换即时生效，不用重启；窗口大小与位置自动记忆 |
| **接入地址** | 本机 / 局域网 / Tailscale / 公网 四个地址一键复制，API Key 可显隐 |
| **客户端自动化** | 一键写入 Codex `config.toml` 与 Claude Code `settings.json`，改前自动备份，可一键还原 |
| **日志查看器** | 实时 tail 网关日志，关键字/正则过滤、**ERROR/WARN 分级着色**、自动滚动、清空、导出 |
| **管理台（原生）** | 统计 / 模型 / 凭证 / 日志 / 设置 五个子页签，**直接调网关 `/admin/*` REST**，与前端构建解耦 |
| **额度消耗** | 基于本地余额快照反推消耗，看累计 / 今日消耗与每账号明细，**柱状图 + 环形图**可视化（纯本地，不依赖上游） |
| **补丁守护** | 检测 `SENSITIVE_TERMS` 是否仍含 `OpenAI/Codex/ChatGPT/GPT-4/GPT-5`，缺失自动补回 |
| **网关更新器** | 检查上游 release → 全目录备份 → 覆盖（保留 `auth/`、`.env`、启动脚本）→ **自动重打补丁** |
| **应用自更新** | 检查本项目的 GitHub Release → 自动选包（安装版跑静默 Setup / 便携版原地覆盖）→ 退出并自动重启 |
| **下载通道回退** | 检查更新 / 下载安装包时按 `手动代理 → 系统代理 → 环境变量 → 直连` 依次试，每条先探活 0.8 秒，死的直接跳过；「网络诊断」逐条给出实测结果 |
| **数据目录迁移** | 把配置 / 日志 / 备份 / 下载中转整体搬到别的盘（缓解 C 盘压力），指针文件留在原位，失败不丢数据 |
| **环境自愈** | 每次启动自动修正失效的网关目录、Python 路径、被占用的端口 |
| **首次向导** | 4 步完成全部配置，含解释器自动探测 |

---

## 快速开始

### 源码运行（开发/调试）

```bash
# 依赖
pip install PySide6

# 运行（有控制台窗口，便于看报错）
python -m luobobox

# 或者无窗口运行（调试输出写到 debug.err.log）
set LUOBOBOX_DEBUG=1
pythonw run_luobobox.pyw
```

### 打包发行

```bash
python packaging/build.py --clean --zip --installer
```

| 参数 | 作用 |
|---|---|
| `--clean` | 报告旧产物（不做删除，三个产物都是原地覆盖） |
| `--purge` | 彻底清空 `build/` 与 `dist/` 再打包 |
| `--zip` | 额外生成便携版 zip |
| `--installer` | 额外生成安装包（需 Inno Setup） |

产物：

| 路径 | 说明 |
|---|---|
| `dist/LuoboBox/` | onedir 发行目录，双击 `LuoboBox.exe` 即可 |
| `dist/LuoboBox-<版本>-portable.zip` | 便携包（纯 ASCII 名，中文会被 Release 吞掉） |
| `dist/LuoboBox-Setup-<版本>.exe` | 安装包 |

> `packaging/luobobox.iss` 与 `packaging/version_info.txt` 由 `build.py` 每次构建重写
> （前者正文里含构建机的绝对路径），因此未入库。
> 图标已随仓库提供，也可用 `packaging/make_icon.py` 重新生成 —— 纯 Pillow 手绘，不依赖外部素材。

推 tag 时 GitHub Actions 会自动出包并创建 Release（见 `.github/workflows/build-release.yml`）：

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Inno Setup 没装的话：

```bash
winget install -e --id JRSoftware.InnoSetup
```

> 注意 winget 默认装到**用户级**目录（`%LOCALAPPDATA%\Programs\Inno Setup 6`），
> 打包脚本已经把这条路径纳入探测。

### 验证打包产物

```bash
dist\LuoboBox\LuoboBox.exe --selftest
```

`--selftest` 会把整条启动链路跑一遍（QApplication → 配置 → 环境自愈 → 主窗口 →
托盘 → 补丁体检 → 客户端配置）但**不进入事件循环**，然后立刻退出。
结果同时打印到控制台并写入 `<数据目录>\selftest.log`。

为什么需要它：GUI 程序从自动化/CI 环境里跑事件循环会被直接终止，
没法验证"双击到底能不能用"；这个模式把所有会出错的构建步骤都执行到。

---

## 数据与备份

默认全部落在 `%LOCALAPPDATA%\LuoboBox`，卸载时删掉这个目录就干净了。

| 路径 | 内容 |
|---|---|
| `config.json` | 全部配置（人手可改） |
| `logs/gateway.log` | 网关 stdout/stderr |
| `logs/luobobox.log` | 萝卜盒自己的日志（轮转，2MB × 3） |
| `backups/` | 所有改动前的备份：Codex/Claude 配置、脱敏补丁、网关整目录 |
| `updates/` | 更新中转：下载的安装包、暂存解压、助手脚本与日志 |

配置损坏时不会被卡死：坏文件自动改名成 `config.broken-<时间戳>.json` 并重建默认配置。

### 不想放在 C 盘？可以整体搬走

网关整目录备份一份就可能几百 MB（`web/node_modules` 有两万多个小文件），
系统盘吃紧时到「设置 → 数据与磁盘 → 迁移数据目录到其他盘」即可整体搬到 D 盘。

- 迁移顺序是 **先复制 → 再写指针 → 最后删旧**：任何一步失败都不会出现"两边都没有"，
  最差只是多占一份磁盘，重试即可。
- 指针文件 `datadir.txt` **永远留在原位置**，内容是一行绝对路径 ——
  程序下次启动才知道数据搬去了哪。想撤回就删掉这个文件（文件不会自动搬回）。
- 备份会排除 `node_modules` / `.git` / `.venv` / 各类缓存（可再生的东西不进备份），
  并且**只保留最近 2 份**。刚做出来的那一份永远不删 —— 名字排序在系统时间被回拨时
  可能把它排到末尾，删掉它等于这次升级没有退路。

---

## 目录结构

```
luobobox/
├── luobobox/
│   ├── app.py             入口：单实例、主题、向导、托盘装配
│   ├── context.py         AppContext：配置 + 网关 + Funnel + 后台任务的粘合层
│   ├── config.py          配置读写、校验、环境自愈
│   ├── paths.py           路径解析、数据目录迁移、Python 解释器探测
│   ├── net.py             带代理回退的 HTTP 层（探活 + 逐通道失败原因）
│   ├── gateway.py         网关子进程生命周期、健康检查、日志
│   ├── funnel.py          Tailscale Funnel 开关
│   ├── clientconfig.py    Codex / Claude Code 配置的手术式改写与还原
│   ├── patcher.py         脱敏词表补丁守护
│   ├── updater.py         网关（codebuddy2api）上游 release 检查与应用
│   ├── appupdater.py      萝卜盒**自身**的在线更新（下载→覆盖→重启）
│   ├── webui.py           内置 WebUI（预构建 dist）的按需补齐
│   ├── autostart.py       开机自启（注册表 Run 键）
│   ├── usage.py           额度消耗统计（本地余额快照反推，纯本地）
│   ├── usage_view.py      「额度消耗」页签
│   ├── logging_setup.py   自身日志
│   └── ui/
│       ├── theme.py       主题引擎：暗 / 亮 × 4 强调色 × 三档字号，QSS 全量重建 + 热切换
│       ├── widgets.py     通用部件（Pill / Toast / EmptyState / 状态点与状态环 / 日志分级高亮）
│       ├── charts.py      纯 QPainter 图表（柱状图 / 环形图，不引第三方图表库）
│       ├── motion.py      动效语言（淡入上移 / 滑入 / 高亮闪烁，无头环境自动降级）
│       ├── palette.py     命令面板（Ctrl+K）的模糊匹配与动作清单
│       ├── main_window.py 主窗口：页签注册表、导航分组、快捷键、各业务页
│       ├── admin.py       原生「管理台」页签（直连网关 /admin/* REST）
│       ├── tray.py        系统托盘：状态图标、分组菜单、新版本角标
│       ├── wizard.py      首次运行 4 步向导
│       └── workers.py     后台线程助手（耗时操作一律走它，UI 不阻塞）
├── assets/                图标（由 packaging/make_icon.py 生成）+ 内置 WebUI（assets/webui/）
├── packaging/             打包脚本、spec、安装包定义
└── tests/                 自测与 GUI 冒烟测试
```

---

## 测试

```bash
# 核心逻辑自测（81 项）
# 注意：从 1.0.5 起这一段会 import context.py（依赖 PySide6），
# 所以要用装了 PySide6 的解释器跑，纯系统 Python 会在 [6b] 段报 ModuleNotFoundError
python tests/selftest.py

# GUI 布局 / 交互回归自测（141 项，无头可跑）
# 覆盖这一轮最容易悄悄坏掉的东西：页签注册表（绝不允许再出现
# tabs.setCurrentIndex(<字面量>)）、导航分组、快捷键表与 tab_keys() 是否同步、
# 主题引擎产出的样式表、空态、窗口几何记忆、动效降级、托盘新版本角标
python tests/gui_layout_selftest.py

# GUI 冒烟：构建真实窗口并逐页签截图到 tests/_shots/
# 注意：必须用 windows 平台，offscreen 平台取不到系统字体，中文会渲染成豆腐块
python tests/gui_smoke.py

# 端到端：真实拉起网关 → 抓健康数据 → 停止 → 校验端口释放
# 只在内存里改端口，结束时按字节还原 config.json
python tests/e2e_gateway_lifecycle.py

# 应用自更新自测（20 项）：安装形态判定、资产挑选、版本比较、
# 助手 .cmd 在中文路径下真的能把新版本覆盖上去、分离进程能跑完
python tests/appupdater_selftest.py

# 网关更新保产物自测（19 项）：升级时必须保住 web/dist 与 web/node_modules，
# 否则网页版管理台会因缺构建产物而 503「WebUI 尚未构建」
python tests/updater_preserve_selftest.py

# 内置 WebUI 自测（34 项）：dist 缺失时补齐、幂等、绝不覆盖用户自建产物、
# 版本戳过时刷新、没内置/目录不存在时不炸、apply_release 端到端补上 dist
python tests/webui_selftest.py

# 网络回退 / 数据目录迁移 / 静默安装落盘位置自测（65 项）：
# 死代理必须被跳过而不是干等 21 秒、全失败要逐条说清原因、本机地址强制直连、
# 迁移顺序「先复制→再写指针→最后删旧」、备份排除 node_modules 且刚做的那份不被删、
# 助手脚本必须带 /DIR（否则 Inno 的 {autopf} 会把程序装回 C 盘）
python tests/net_selftest.py

# 发版后校验（可选，需联网，会下载 ~50MB）：
# 真下 GitHub Release 的便携包，覆盖一份 dist/LuoboBox 老安装，
# 断言 exe 的 ProductVersion 真的换了代、包内文件无一缺失且字节一致
python tests/e2e_release_asset.py          # 校验 latest
python tests/e2e_release_asset.py v1.0.3   # 校验指定 tag
```

自测重点覆盖三个**静默出错**的地方：

1. **Codex `config.toml` 的手术式改写**——必须无重复键、保留用户自己的
   `[model_providers.OpenAI]` 段与注释、可重复调用不叠加、可完整还原。
2. **脱敏补丁**——结构不认识时宁可不动；重复调用幂等；补完仍是合法 Python。
3. **页签跳转一律走 key**（`goto_tab("update")`），全项目禁止出现
   `tabs.setCurrentIndex(<字面量>)`。写死索引的代码不会报错，只会在**插入新页签后
   静默跳到错误的页**（历史上托盘的「检查更新」就是这样跳到了「日志」页）；
   同理 `Ctrl+1..N` 必须在 `add_tab` 时按 `tab_keys()` 重绑，否则后加的页签永远没有快捷键。

还有一条针对**真实文件**的体检（`test_real_gateway`）：拿本机实际的
`codebuddy2api/app/desensitize.py` 跑一遍。fake 文件能过、真文件却过不了的
盲区就是这样被抓出来的（`SENSITIVE_TERMS` 列表前有 docstring 时会漏匹配）。

---

## 设计决策（都来自踩过的坑）

| 决策 | 原因 |
|---|---|
| 网关永远是独立子进程 | UI 不能被 uvicorn 阻塞 |
| 子进程用 `CREATE_NO_WINDOW` + `pythonw.exe` | 有控制台窗口时，窗口被关会发 `CTRL_CLOSE_EVENT`，进程以 `0xC000013A` 静默死掉 |
| 停止前先验明进程身份 | 端口占用者未必是自己的进程，盲目 `taskkill` 会误杀同端口的别的服务 |
| Codex 用静态 `http_headers` 而非 `env_key` | Windows 改环境变量后已运行的 Explorer 不刷新，从开始菜单启动的 Codex 会 401 |
| `model_context_window = 200000` | 上游实测 ~200K 可过、~256K 报 `code:11115`；写 100 万会让 Codex 以为还能塞 |
| 关闭 Funnel 只按端口 `off`，绝不 `reset` | `reset` 会连同机 443 上其他服务的公网入口一起清掉 |
| 背景只在顶层容器设，子控件一律透明 | Qt 的 `QWidget` 选择器会命中 `QLabel`，每个标签都会在卡片上刷一层深色横条 |
| 备份文件名冲突时追加 `-2` | 只精确到秒时，同一秒内两次 apply 会把第一份"改动前"备份盖掉 |
| 只打包桌面壳，不打包网关 | `converter.py` 运行时动态导入多，塞进 PyInstaller 容易炸；网关还要能独立覆盖升级 |
| 打包脚本用 Python 而不是 .ps1/.bat | 避免中文路径下的编码坑 |
| 打包**不加** PyInstaller 的 `--clean` | 它成批删缓存文件（实测 60 个即触发批量删除保护），在受管控机器上会被直接拦下；`--noconfirm` 对增量构建够用 |
| 打包走"暂存目录 + `os.rename` 换位" | 直接覆盖 `dist/` 同样会触发批量删除保护；改名不删文件，可绕过且更安全 |
| 校验 Python 路径时先查字符串是否为空 | `Path("")` 等于 `Path(".")`，而 `Path(".").exists()` 是 `True`，空路径会被误判为"校验通过"，最后在拼命令时抛 `ValueError` |
| 无托盘时降级而不是退出 | 远程会话/精简系统可能没有托盘区域，直接弹框退出等于打不开 |
| 代理候选顺序是**手动 → 系统 → 环境变量 → 直连** | 环境变量里的 `HTTP(S)_PROXY` 可能是外层 shell **注入**的（指向一个临时端口，进程起来时它已经死了）。用户自己配的和系统代理才是可信的 |
| 每个候选先做 0.8 秒 TCP 探活，直连先探 3 秒 | 不探活的话，一个死代理会让 `urllib` 干等约 21 秒，最后报 `WinError 10060 连接方没有正确答复` —— 这个提示看着像"GitHub 挂了"，其实是"代理连不上"，用户完全无从下手 |
| 全部失败时逐条列出每条通道的失败原因 | 只说"下载失败"等于没说。要能一眼看出该改哪个开关 |
| 本机地址（`127.x` / `localhost`）强制直连 | 系统代理设置里通常会排除它们，但**不排除时会把本机请求绕出去**，表现为"网关明明在跑却连不上" |
| 静默安装显式带 `/DIR="<当前程序目录>"` | `.iss` 的 `DefaultDirName={autopf}\LuoboBox`，而 `PrivilegesRequired=lowest` 会把 `{autopf}` 解析成 `%LOCALAPPDATA%\Programs` —— 也就是 **C 盘**。不带 `/DIR` 就有可能在 C 盘另装一份，留下两处互不相干的安装 |
| 静默安装**不**带 `/TASKS=` | Inno 的 `UsePreviousTasks` 默认 `yes`，升级时会自动沿用上次勾选的开机自启 / 桌面快捷方式；而首次静默安装时强行打开机自启反而是错的（向导里那两个任务本来就不勾） |
| 网关备份排除 `node_modules` / `.git` / `.venv` / 缓存 | 备份是整目录 `copytree`，而 `web/node_modules` 有两万多个小文件 / 700MB 上下 —— 实测一次升级就写出 718MB 到系统盘，C 盘只剩 2GB 时两三次就被挤爆，用户看到的却是"更新出错"这种跟磁盘无关的提示 |
| 备份只保留 2 份，且**刚做出来的那份加保护位** | 修剪按目录名排序（`gateway-YYYYmmdd-HHMMSS`），而"先备份再修剪"的顺序下，一旦名字排序把新备份排到末尾就会被自己删掉 —— 等于这次升级没有退路 |
| 数据目录用**指针文件**而不是拆成多个设置项 | 配置 / 日志 / 备份 / 下载中转必须在一起，拆开只会让"备份在新盘、配置在旧盘"这种半迁移状态出现。一个 `datadir.txt` 是唯一真相来源，可读、可手改、可撤回 |
| 页签跳转一律用 **key**，禁止写死索引 | 写死的索引不会报错，只会静默跳到错的页。寄存器 `_tab_index` 让「加一页 / 删一页」不再需要满仓库找数字 |
| `Ctrl+1..N` 在 `add_tab` 时**重绑**，而不是 `__init__` 里绑一次 | 页签是 `app.py` 在窗口 `_build()` 之后才追加的（额度页），绑定时它还不存在 —— 表现是快捷键"少一个"，而且以后每加一页都会漏 |
| QSS 里的字号一律写 `pt`，不写 `px` | `font-size: Npx` 造出的 `QFont` 只有 pixelSize、`pointSize()` 是 `-1`；Qt 内部 polish 菜单时算 `pointSize() - 1`，于是刷屏 `QFont::setPointSize: Point size <= 0 (-1)`。给 `QPainter` 的 `QFont` 才用 px |
| QSS 里不写 `/* */` 注释 | Qt 的样式表不支持注释，写进去会让**整条规则被静默丢弃**（症状是"改了样式却不生效"）。要注释写在 Python 侧 |
| 主题色用**函数**取，不用模块级 dict | `dict` 会在 import 那一刻把 `theme.OK` 的**值**拷进去，等于把颜色冻死 —— 换到浅色主题后，表格里的「异常」还是深色主题的浅粉红，白底上几乎看不见 |
| 换肤只重建样式表 + 通知订阅者，不重建窗口 | 重建窗口会丢当前页签 / 滚动位置 / 输入内容。订阅者（`theme.on_change`）各自刷新自绘控件的颜色即可 |
| 托盘的新版本角标**自己缓存**文案 | 让消费方去读生产方属性会踩时序：托盘 `refresh()` 早于/错开窗口属性更新时角标就丢了。回调进来的值就地存下来，构造时再从生产方读一次（覆盖"更新在托盘创建之前就已发现"） |
| 主题 / 图表 / 动效全部自己实现，不引第三方 | 多一个依赖就多一个打包体积与升级风险；`QPainter` 画柱状图和环形图足够，还能直接吃主题变量 |

---

## 常见问题

**Q：能换主题吗？字号太小 / 太大怎么办？**
到「设置 → 外观」：暗 / 亮两种基调，4 套强调色（萝卜红 / 青柠 / 电紫 / 湖蓝），
三档字号（标准 / 大 / 特大）。改完**立刻生效，不用重启**，选择会记住。
（**不跟随系统**：Qt 的 `QPalette` 机制管不到卡片描边、圆角、语义色这些样式表细节，
两套机制混用只会打架，最后变成"亮色下有些地方还是黑的"。）

**Q：有快捷键吗？**
`Ctrl+K` 命令面板（模糊搜任意功能，不用记菜单在哪）、`Ctrl+1..N` 按顺序切页签、
`Ctrl+R` 重启网关、`Ctrl+Shift+C` 复制接入包、`F5` 刷新状态、`Ctrl+,` 进设置。

**Q：托盘图标不见了？**
Windows 会把新图标折叠进「隐藏的图标」。托盘设置里把它拖出来即可。

**Q：启动报「端口已被占用」？**
系统里可能还留着旧的计划任务 `codebuddy2api` 在托管同一个网关。
到「设置 → 迁移与诊断」点「移除旧计划任务」，或直接在设置里换个端口。

**Q：启动报「未指定 Python 解释器」？**
说明自动探测没找到带 `fastapi`/`uvicorn`/`httpx` 的解释器。
到「设置 → 解释器」点「探测」，或手动指定一个装过这三个包的 `python.exe`。

**Q：Codex 报 `stream closed before response.completed`？**
99% 是脱敏补丁被冲掉了。到「概览」看「脱敏补丁」那行，
点「一键重打补丁」然后重启网关。

**Q：改了 Key 之后 Codex 用不了？**
Key 轮换后要重新执行一次「接入 Codex」（它会重写 `config.toml` 里的
`http_headers`）。萝卜盒会检测到不一致并在状态里提示。

**Q：关闭窗口后程序去哪了？**
默认最小化到托盘（设置里可关）。网关继续提供服务。

**Q：更新时报 `WinError 10060 由于连接方在一段时间后没有正确答复…`？**
这是**代理**连不上，不是 GitHub 挂了。到「更新 → 下载通道」点「保存并诊断」，
会逐条列出每个候选通道（手动 / 系统 / 环境变量 / 直连）是否可达、实测结果是什么。
萝卜盒会自动跳过探活失败的通道，所以通常不用管；如果四条全挂，
按诊断结果检查代理软件是否在跑，或在「手动代理」里填一个能用的地址。

**Q：能不能不装在 C 盘？**
升级**永远不会**动安装位置：安装版走静默安装包时会显式带上
`/DIR="<当前程序目录>"`，便携版则原地覆盖。只有你手动双击 Setup 从零安装时，
向导默认给出的才是 `%LOCALAPPDATA%\Programs\LuoboBox`（C 盘）——
这一步可以自己改成 `D:\LuoboBox`。
至于真正占地方的**数据目录**（配置 / 日志 / 备份 / 下载中转），
到「设置 → 数据与磁盘 → 迁移数据目录到其他盘」整体搬走即可。

---

## 边界与已知限制

- **目标机器需要 Python + `fastapi`/`uvicorn`/`httpx`**：萝卜盒会自动探测本机
  可用解释器，也可以在「设置 → 解释器」手动指定。当前不内嵌解释器。
- **两条更新链路彼此独立**：「更新」页里 `萝卜盒更新` 只换萝卜盒自己
  （来源 `lylguang/LuoboBox` 的 Release，装完自动重启）；`网关更新` 只换
  `codebuddy2api` 源码（来源上游 release，保留 `auth/`、`.env` 与补丁）。
- **应用自更新需要退出一次**：Windows 上正在运行的 exe 无法自我覆盖，
  所以流程是「下载 → 交给一个一次性 `cmd` 助手 → 主程序退出 → 覆盖/静默安装
  → 自动重启」。助手脚本与日志落在 `<数据目录>\updates\`（可用
  `updater.work_dir` 改到别的盘）。静默安装一定会带 `/DIR="<当前程序目录>"`，
  所以升级不会把程序搬到别处、更不会另装一份到 C 盘。
- **数据目录默认在系统盘**：`%LOCALAPPDATA%\LuoboBox`。网关整目录备份一份可能
  几百 MB，C 盘吃紧时用「设置 → 数据与磁盘」整体迁到别的盘（迁移是
  先复制 → 再写指针 → 最后删旧，中途失败不丢数据）。
- **管理台有两条通道，都不内嵌浏览器内核**：
  - **原生「管理台」页签**（推荐）：直接调网关 `/admin/*` REST，**不依赖 `web/dist`**，
    所以网关怎么升级都不会坏；
  - **网页版**：网关自带的 `/dashboard/`，需要 `codebuddy2api/web/dist` 构建产物。
    上游 Release 不含 `dist`（`web/.gitignore` 把它忽略了），而覆盖动作是
    「整目录 rmtree + copytree」——所以**每次升级都会把它冲掉**。
    两道防线：
    1. `updater.PRESERVE_SUBPATHS`：升级时把已有的 `web/dist`、`web/node_modules`
       搬到同盘暂存、覆盖完再搬回（防「被删掉」）；
    2. **内置 WebUI**（`luobobox/webui.py` + `assets/webui/`）：萝卜盒自己带一份
       预构建 `dist`，在「网关升级后 / 萝卜盒启动时 / 点网页版管理台时」三个时机
       按需补齐（防「从没构建过」和「上游改了 web/src 导致 dist 过时」）。
       只在 `dist` 缺失、或带萝卜盒的版本戳且已过期时才写；**用户自行构建的
       `dist`（无版本戳）绝不覆盖**。
  两者都省掉了 ~100MB 的 QtWebEngine。

  维护内置 WebUI：在网关的 `web/` 下 `node_modules\.bin\vp.CMD build`，
  然后 `python packaging/sync_webui.py`（把产物同步进 `assets/webui/` 并入库）。
  `build.py` 会在 `assets/webui/` 缺失时直接报错 —— 免得打出「点开就 503」的包。
- 账号风控、上游 ToS、公网暴露风险与原项目一致，萝卜盒不改也不规避这些。

---

## 许可

[PolyForm Noncommercial License 1.0.0](LICENSE) —— 允许个人自用、学习研究、修改与
再分发，**禁止任何商业用途**。需保留许可原文与 `Required Notice` 声明。

本项目与腾讯、CodeBuddy、WorkBuddy、OpenAI、Anthropic 均无关联。
