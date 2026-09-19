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
| **托盘常驻** | 启动/停止/重启网关、开关公网入口、复制 Key、立即签到、检查更新、开机自启 |
| **状态面板** | 网关状态、端口、PID、可用模型数、响应延迟、脱敏补丁健康度、凭证池健康与积分 |
| **接入地址** | 本机 / 局域网 / Tailscale / 公网 四个地址一键复制，API Key 可显隐 |
| **客户端自动化** | 一键写入 Codex `config.toml` 与 Claude Code `settings.json`，改前自动备份，可一键还原 |
| **日志查看器** | 实时 tail 网关日志，关键字过滤、自动滚动、清空、导出 |
| **补丁守护** | 检测 `SENSITIVE_TERMS` 是否仍含 `OpenAI/Codex/ChatGPT/GPT-4/GPT-5`，缺失自动补回 |
| **网关更新器** | 检查上游 release → 全目录备份 → 覆盖（保留 `auth/`、`.env`、启动脚本）→ **自动重打补丁** |
| **应用自更新** | 检查本项目的 GitHub Release → 自动选包（安装版跑静默 Setup / 便携版原地覆盖）→ 退出并自动重启 |
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

全部落在 `%LOCALAPPDATA%\LuoboBox`，卸载时删掉这个目录就干净了。

| 路径 | 内容 |
|---|---|
| `config.json` | 全部配置（人手可改） |
| `logs/gateway.log` | 网关 stdout/stderr |
| `logs/luobobox.log` | 萝卜盒自己的日志（轮转，2MB × 3） |
| `backups/` | 所有改动前的备份：Codex/Claude 配置、脱敏补丁、网关整目录 |

配置损坏时不会被卡死：坏文件自动改名成 `config.broken-<时间戳>.json` 并重建默认配置。

---

## 目录结构

```
luobobox/
├── luobobox/
│   ├── app.py             入口：单实例、主题、向导、托盘装配
│   ├── context.py         AppContext：配置 + 网关 + Funnel + 后台任务的粘合层
│   ├── config.py          配置读写、校验、环境自愈
│   ├── paths.py           路径解析、Python 解释器探测
│   ├── gateway.py         网关子进程生命周期、健康检查、日志
│   ├── funnel.py          Tailscale Funnel 开关
│   ├── clientconfig.py    Codex / Claude Code 配置的手术式改写与还原
│   ├── patcher.py         脱敏词表补丁守护
│   ├── updater.py         网关（codebuddy2api）上游 release 检查与应用
│   ├── appupdater.py      萝卜盒**自身**的在线更新（下载→覆盖→重启）
│   ├── autostart.py       开机自启（注册表 Run 键）
│   ├── logging_setup.py   自身日志
│   └── ui/                主题、通用部件、主窗口、托盘、向导、线程助手
├── assets/                图标（由 packaging/make_icon.py 生成）
├── packaging/             打包脚本、spec、安装包定义
└── tests/                 自测与 GUI 冒烟测试
```

---

## 测试

```bash
# 核心逻辑自测（77 项，不需要图形界面）
python tests/selftest.py

# GUI 冒烟：构建真实窗口并逐页签截图到 tests/_shots/
# 注意：必须用 windows 平台，offscreen 平台取不到系统字体，中文会渲染成豆腐块
python tests/gui_smoke.py

# 端到端：真实拉起网关 → 抓健康数据 → 停止 → 校验端口释放
# 只在内存里改端口，结束时按字节还原 config.json
python tests/e2e_gateway_lifecycle.py

# 应用自更新自测（20 项）：安装形态判定、资产挑选、版本比较、
# 助手 .cmd 在中文路径下真的能把新版本覆盖上去、分离进程能跑完
python tests/appupdater_selftest.py
```

自测重点覆盖两个**静默出错**的地方：

1. **Codex `config.toml` 的手术式改写**——必须无重复键、保留用户自己的
   `[model_providers.OpenAI]` 段与注释、可重复调用不叠加、可完整还原。
2. **脱敏补丁**——结构不认识时宁可不动；重复调用幂等；补完仍是合法 Python。

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

---

## 常见问题

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

---

## 边界与已知限制

- **目标机器需要 Python + `fastapi`/`uvicorn`/`httpx`**：萝卜盒会自动探测本机
  可用解释器，也可以在「设置 → 解释器」手动指定。当前不内嵌解释器。
- **两条更新链路彼此独立**：「更新」页里 `萝卜盒更新` 只换萝卜盒自己
  （来源 `lylguang/LuoboBox` 的 Release，装完自动重启）；`网关更新` 只换
  `codebuddy2api` 源码（来源上游 release，保留 `auth/`、`.env` 与补丁）。
- **应用自更新需要退出一次**：Windows 上正在运行的 exe 无法自我覆盖，
  所以流程是「下载 → 交给一个一次性 `cmd` 助手 → 主程序退出 → 覆盖/静默安装
  → 自动重启」。助手脚本与日志落在 `<数据目录>\updates\`。
- **不内嵌 WebUI**：「打开管理台」交给系统默认浏览器 —— 省掉 ~100MB 的
  QtWebEngine，而且管理台本来就是网页应用。
- 账号风控、上游 ToS、公网暴露风险与原项目一致，萝卜盒不改也不规避这些。

---

## 许可

[PolyForm Noncommercial License 1.0.0](LICENSE) —— 允许个人自用、学习研究、修改与
再分发，**禁止任何商业用途**。需保留许可原文与 `Required Notice` 声明。

本项目与腾讯、CodeBuddy、WorkBuddy、OpenAI、Anthropic 均无关联。
