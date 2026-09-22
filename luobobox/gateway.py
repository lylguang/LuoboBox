"""网关进程管理。

三条铁律（都是这台机器上用血换来的结论）：
  1. 网关必须是**独立子进程**，不能用 runpy 在本进程里跑 —— 否则 UI 被 uvicorn 阻塞。
  2. 子进程必须用 CREATE_NO_WINDOW 启动（配合 pythonw.exe）—— 一旦有控制台窗口，
     窗口被关闭/会话清理会发 CTRL_CLOSE_EVENT，进程以 0xC000013A 静默死掉。
     这是"服务莫名退出、日志 0 字节"的真实原因。
  3. 停止时必须**先验明身份再动手** —— 端口占用者未必是我们的进程，
     盲目 taskkill 会误杀同端口的别的服务。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config, port_free
from .paths import gateway_log, pythonw_for

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_EXTERNAL = "external"   # 端口被占用，但不是萝卜盒拉起的
STATE_STOPPING = "stopping"
STATE_ERROR = "error"

STATE_LABEL = {
    STATE_STOPPED: "已停止",
    STATE_STARTING: "启动中",
    STATE_RUNNING: "运行中",
    STATE_EXTERNAL: "运行中（外部启动）",
    STATE_STOPPING: "停止中",
    STATE_ERROR: "异常",
}


@dataclass
class HealthSnapshot:
    ok: bool = False
    detail: str = ""
    models: int = 0
    credentials: list[dict[str, Any]] = field(default_factory=list)
    credits: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


def _http_json(
    url: str,
    key: str | None = None,
    timeout: float = 6.0,
    data: bytes | None = None,
    method: str | None = None,
) -> tuple[int, Any]:
    # 本机网关请求必须**绕过系统代理**：urllib 默认吃 http_proxy 环境变量，
    # 这台机器上代理常驻 127.0.0.1:8700 —— 本机请求绕道代理后，
    # 代理一异常/策略一变，本地健康检查就"无法连接"（真实踩坑）。
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("X-Api-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                return resp.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return exc.code, raw.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def listening_pids(port: int, *, fresh: bool = False) -> list[int]:
    """找出监听指定端口的 PID 列表。

    ★ 优先走 Windows 原生监听表（0.16ms，无子进程）。原来每次都拉一个
    `netstat -ano -p TCP` 再逐行解析，单次约 228ms —— 而 UI 的
    `gateway.pid` 属性每轮刷新都会调它，又是一处「卡」的来源。
    """
    from .winports import listening_pids as _native

    native = _native(int(port), fresh=fresh)
    if native is not None:
        return native

    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    pids: set[int] = set()
    needle = f":{port} "
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if not parts[1].endswith(needle) and f":{port}" not in parts[1]:
            continue
        try:
            pids.add(int(parts[4]))
        except ValueError:
            continue
    return sorted(pids)


def process_image(pid: int) -> str:
    """取进程映像名，用于校验"这个 PID 到底是不是 python"。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
        if not out or out.startswith("信息") or "No tasks" in out:
            return ""
        return out.split(",")[0].strip('"')
    except Exception:  # noqa: BLE001
        return ""


def taskkill(pid: int, tree: bool = True) -> bool:
    args = ["taskkill", "/PID", str(pid), "/F"]
    if tree:
        args.insert(1, "/T")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


class GatewayManager:
    """网关子进程的完整生命周期控制器。"""

    def __init__(self, config: Config, log: Callable[[str], None] | None = None):
        self.config = config
        self._log = log or (lambda _m: None)
        self._proc: subprocess.Popen | None = None
        self._log_handle = None
        self._state = STATE_STOPPED
        self._last_error = ""
        # 健康探测的短缓存。`state` 与 `state_label` 每轮刷新各问一次，
        # 而 `_refresh_state` + `_refresh_hero` 一轮就是两次 —— 不缓存的话
        # 网关卡住时一次刷新能干等 2×2.5s（本机向未监听端口发包要等满超时）。
        self._healthy_at = 0.0
        self._healthy_last = False

    # ------------------------------------------------------------ 基本属性

    @property
    def port(self) -> int:
        return int(self.config.get("gateway.port", 8788))

    @property
    def api_key(self) -> str:
        return str(self.config.get("gateway.api_key", ""))

    @property
    def state(self) -> str:
        """实时判定状态：先看自己的子进程，再看端口。"""
        if self._proc is not None and self._proc.poll() is None:
            if self._state in (STATE_STARTING, STATE_STOPPING):
                return self._state
            # ★ 端口还没监听就别发 HTTP —— 本机向未监听端口发请求要等满
            #   超时（实测 2.5s），网关启动那几十秒里 UI 会被这一句卡死。
            #   端口都没起，答案必然是 STARTING，直接用监听表（0.16ms）。
            if not self.is_listening():
                return STATE_STARTING
            return STATE_RUNNING if self._health_quick() else STATE_STARTING
        if not port_free(self.port):
            return STATE_EXTERNAL
        return STATE_STOPPED

    def _health_quick(self, ttl: float = 0.5) -> bool:
        """带短缓存的健康检查，给高频读取的 `state` 用。"""
        now = time.monotonic()
        if now - self._healthy_at < ttl:
            return self._healthy_last
        ok = self._is_healthy(quick=True)
        self._healthy_at = now
        self._healthy_last = ok
        return ok

    def forget_health_cache(self) -> None:
        self._healthy_at = 0.0
        self._healthy_last = False

    def _forget_port_caches(self) -> None:
        """把端口表快照与健康判断一起作废，让状态立刻反映刚刚的动作。"""
        from .winports import forget_port_cache

        forget_port_cache()
        self.forget_health_cache()

    @property
    def state_label(self) -> str:
        state = self.state
        if state == STATE_ERROR and self._last_error:
            return f"异常：{self._last_error}"
        return STATE_LABEL.get(state, state)

    @property
    def pid(self) -> int | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid
        pids = listening_pids(self.port)
        return pids[0] if pids else None

    @property
    def owned(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---- 启动失败时，把子进程的真实死因捞回来给用户看 ----
    #
    # 实测归因（2026-09-23，真 pythonw.exe + CREATE_NO_WINDOW，见 tests/selftest）：
    #   · 退出码 1 = 子进程抛了未捕获异常（或自己 sys.exit(1)）
    #   · 退出码 2 = argparse 参数被拒（把 --port 写成非数字就是 2）
    #   · 端口被占 / 解释器缺依赖 **都不会**退出，会正常起来
    # 于是"退出码 1"必然伴随一段 traceback —— 而那段 traceback 早就被
    # stdout 重定向进 gateway.log 了，只是以前一个字都不给用户看，
    # 失败提示只能报一句"进程提前退出（退出码 1）"，用户完全无从下手。

    EXIT_CODE_HINT = {
        1: "子进程启动时抛了未捕获异常（依赖缺失 / 配置项非法都会这样）",
        2: "启动参数被拒绝，通常是端口填成了非数字",
        3: "解释器路径或脚本参数不对",
    }

    @staticmethod
    def exit_code_hint(code: int | None) -> str:
        """退出码 → 人话。认不出来就返回空串，绝不瞎猜。"""
        if code is None:
            return ""
        special = GatewayManager.EXIT_CODE_HINT.get(code)
        if special:
            return special
        # Windows 上被控制台关闭（CTRL_CLOSE_EVENT）/ 任务管理器带走的进程
        # 会给出 0xC000013A 这类大正数；POSIX 语义下则是负数（信号）。
        if code < 0 or code >= 0xC0000000:
            return "子进程被系统或信号强制结束（控制台被关掉会这样）"
        return ""

    @staticmethod
    def failure_cause(text: str, max_len: int = 240) -> str:
        """从子进程输出里挑出"最像原因"的那一行。

        traceback 的收尾行就是异常本身（`RuntimeError: xxx`），优先挑最后
        一条；挑不到才退回最后一行非空内容 —— 只给一行，提示条要能读完。
        """
        lines = [ln.strip() for ln in (text or "").splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        for ln in reversed(lines):
            head = ln.split(":", 1)[0].strip()
            if (head.endswith(("Error", "Exception", "Warning"))
                    or "error:" in ln or "ERROR:" in ln
                    or (bool(head) and head.isupper())):
                return ln[:max_len]
        return lines[-1][:max_len]

    @staticmethod
    def new_output(path: Path, offset: int, limit: int = 24 * 1024) -> str:
        """只读 path 里 offset 之后新增的内容 —— 这次启动子进程吐的字。

        日志是**追加写**的，用字节偏移切一刀才能避开上次运行的残留；
        否则一句陈年旧错混进失败提示，比不提示还糟。
        """
        try:
            if not path.is_file():
                return ""
            size = path.stat().st_size
            if size <= offset:
                return ""
            start = max(offset, size - limit)
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
            return data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------ 启动

    def build_command(self) -> list[str]:
        gw_dir = Path(str(self.config.get("gateway.dir")))
        python = Path(str(self.config.get("gateway.python")))
        pw = pythonw_for(python)
        argv = [
            str(pw),
            str(gw_dir / "converter.py"),
            "serve",
            "--host", str(self.config.get("gateway.host", "0.0.0.0")),
            "--port", str(self.port),
        ]
        extra = self.config.get("gateway.extra_args", []) or []
        argv.extend(str(a) for a in extra)
        return argv

    def start(self, wait_seconds: int = 25) -> tuple[bool, str]:
        if self.owned:
            return True, "网关已在运行"
        # fresh=True：启停这种决定性的判断不吃 0.25s 的短缓存，要真值。
        if not port_free(self.port, fresh=True):
            # 端口被占 ≠ 一定是别人的。stop_on_exit 默认 false，退出萝卜盒时网关会
            # 作为独立子进程活下来 —— 于是"重启萝卜盒"必然撞上自己上一次拉起的网关。
            # 这时直接报"端口已被占用"会弹一个红色失败提示，其实服务好好的。
            # 先探一次健康检查：是我们的人就接管，别吓人。
            if self._is_healthy():
                self._state = STATE_EXTERNAL
                self._log(f"[gateway] 端口 {self.port} 上已有健康网关，接管它（不重复启动）")
                return True, f"网关已在端口 {self.port} 运行，已接管（未重复启动）"
            return False, (f"端口 {self.port} 已被占用（PID={listening_pids(self.port)}），"
                           "请先停止或改用其它端口")

        problems = self.config.validate()
        if problems:
            return False, "；".join(problems)

        gw_dir = Path(str(self.config.get("gateway.dir")))
        argv = self.build_command()
        env = os.environ.copy()
        env.update(self.config.runtime_env())
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PYTHONHOME", None)

        log_path = gateway_log()
        self._log(f"[gateway] start: {' '.join(argv)}")
        # 记下字节偏移：从这一刻起写进日志的，才是"这次启动"子进程的输出。
        # 失败时靠它把 traceback 捞回来给用户看（见下面那段失败分支）。
        try:
            start_offset = log_path.stat().st_size if log_path.is_file() else 0
        except OSError:
            start_offset = 0
        try:
            self._log_handle = open(log_path, "ab", buffering=0)
            self._log_handle.write(
                f"\n{'=' * 60}\n[luobobox] start "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} port={self.port}\n{'=' * 60}\n".encode("utf-8")
            )
            self._proc = subprocess.Popen(
                argv,
                cwd=str(gw_dir),
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{exc}"
            self._state = STATE_ERROR
            self._close_handle()
            return False, f"启动失败：{exc}"

        self._state = STATE_STARTING
        self._forget_port_caches()
        ok, detail = self.wait_healthy(timeout=wait_seconds)
        if ok:
            self._state = STATE_RUNNING
            self._last_error = ""
            return True, "网关已启动"
        # 起不来就看看子进程是不是已经死了，并把它的真实死因一起捞出来
        code = self._proc.poll() if self._proc else None
        self._state = STATE_ERROR
        # wait_healthy() 的 detail 里已经带了退出码，这里再拼一遍就成了
        # 「（退出码 1）（进程退出码 1）」—— 用户报过这个重复。只在 detail
        # 没提到退出码时才补。
        hint = ""
        if code is not None and f"退出码 {code}" not in detail:
            hint = f"（进程退出码 {code}）"
        self._last_error = f"{detail}{hint}"
        self._log(f"[gateway] 启动后健康检查失败：{detail}{hint}")

        child_out = self.new_output(log_path, start_offset)
        cause = self.failure_cause(child_out)
        advice = self.exit_code_hint(code)
        message = f"启动后未通过健康检查：{detail}{hint}"
        if cause:
            message += f"\n原因：{cause}"
        if advice:
            message += f"\n排查：{advice}"
        if child_out.strip():
            message += "\n完整输出见「日志」页"
            # 顺手把子进程那段原样写进自己的日志：以后只翻 luobobox.log 也能
            # 看到当时子进程说了什么，不必再回头猜 gateway.log 里有什么。
            self._log("[gateway] 子进程输出：\n" + child_out.strip()[-2000:])
        return False, message

    # ------------------------------------------------------------ 停止

    def stop(self, kill_external: bool = False, timeout: int = 12) -> tuple[bool, str]:
        self._state = STATE_STOPPING
        messages: list[str] = []

        if self._proc is not None and self._proc.poll() is None:
            pid = self._proc.pid
            self._log(f"[gateway] terminate pid={pid}")
            try:
                self._proc.terminate()
                self._proc.wait(timeout=timeout)
                messages.append(f"已停止（PID={pid}）")
            except Exception:  # noqa: BLE001
                taskkill(pid)
                messages.append(f"已强制结束（PID={pid}）")
            self._proc = None

        # 端口上可能还残留别的进程（例如计划任务拉起的旧实例）
        # fresh=True：这里要给用户报"还占着"的真实 PID，不能吃缓存。
        pids = listening_pids(self.port, fresh=True)
        if pids:
            if kill_external:
                for pid in pids:
                    image = process_image(pid)
                    if "python" not in image.lower():
                        messages.append(f"跳过非 Python 进程 PID={pid}（{image or '未知'}）")
                        continue
                    taskkill(pid)
                    messages.append(f"已结束占用端口的进程 PID={pid}")
            else:
                messages.append(f"端口 {self.port} 仍被 PID={pids} 占用，未处理")

        self._close_handle()
        self._state = STATE_STOPPED
        self._forget_port_caches()

        # 计划任务托管的实例会随登录自动回来，提示用户
        if scheduled_task_exists():
            messages.append("提示：系统里仍有计划任务 codebuddy2api 托管同一个网关，"
                            "建议在「设置」里移除，避免重复启动")
        self._log("[gateway] stop: " + "；".join(messages))
        return True, "；".join(messages) or "已停止"

    def restart(self, wait_seconds: int = 25) -> tuple[bool, str]:
        self.stop(kill_external=True)
        time.sleep(1.2)
        return self.start(wait_seconds=wait_seconds)

    # ------------------------------------------------------------ 健康检查

    def is_listening(self) -> bool:
        return not port_free(self.port)

    def _is_healthy(self, quick: bool = False) -> bool:
        code, _ = _http_json(self.config.base_url() + "/health", timeout=2.5 if quick else 5.0)
        return code == 200

    def wait_healthy(self, timeout: int = 25) -> tuple[bool, str]:
        deadline = time.time() + timeout
        last = "无响应"
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False, f"进程提前退出（退出码 {self._proc.returncode}）"
            code, body = _http_json(self.config.base_url() + "/health", timeout=2.0)
            if code == 200:
                return True, "ok"
            last = f"HTTP {code}" if code else str(body)[:120]
            time.sleep(0.5)
        return False, last

    def collect_health(self) -> HealthSnapshot:
        """一次性抓齐 UI 需要的所有状态。"""
        snap = HealthSnapshot()
        base = self.config.base_url()
        t0 = time.time()

        code, body = _http_json(base + "/health", timeout=5.0)
        snap.latency_ms = int((time.time() - t0) * 1000)
        if code != 200:
            snap.ok = False
            snap.detail = f"网关无响应（HTTP {code or '连接失败'}）"
            return snap
        snap.ok = True
        snap.detail = "ok"

        code, body = _http_json(base + "/v1/models", key=self.api_key, timeout=8.0)
        if code == 200 and isinstance(body, dict):
            snap.models = len(body.get("data") or [])
        elif code == 401:
            snap.detail = "API Key 校验失败（401）"

        code, body = _http_json(base + "/admin/credentials", key=self.api_key, timeout=8.0)
        if code == 200 and isinstance(body, dict):
            snap.credentials = body.get("credentials") or []

        code, body = _http_json(base + "/admin/credits", key=self.api_key, timeout=8.0)
        if code == 200 and isinstance(body, dict):
            snap.credits = body
        return snap

    # ------------------------------------------------------------ 日志

    def tail_log(self, lines: int = 500) -> str:
        path = gateway_log()
        if not path.is_file():
            return ""
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                block = min(size, max(lines * 220, 64 * 1024))
                fh.seek(size - block)
                data = fh.read()
            text = data.decode("utf-8", "replace")
            parts = text.splitlines()
            return "\n".join(parts[-lines:])
        except Exception as exc:  # noqa: BLE001
            return f"<读取日志失败：{exc}>"

    def clear_log(self) -> None:
        path = gateway_log()
        try:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            path.write_bytes(b"")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 内部

    def _close_handle(self) -> None:
        try:
            if self._log_handle is not None:
                self._log_handle.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._log_handle = None


# ---------------------------------------------------------------- 计划任务

TASK_NAME = "codebuddy2api"


# 计划任务查询缓存：{任务名: (查询时刻, 是否存在)}
_task_exists_cache: dict[str, tuple[float, bool]] = {}
_TASK_EXISTS_TTL_SEC = 20.0


def scheduled_task_exists(name: str = TASK_NAME) -> bool:
    """系统里有没有同名计划任务。

    ★ 带短 TTL 缓存：底层是一次 `schtasks.exe` 进程（冷启动几百毫秒到一两秒），
      而 UI 会在启动后自检里问它、诊断卡片又问它 —— 一次启动就是两三个进程。
      计划任务属于"装了/删了才变"的东西，缓存 20 秒完全够；
      删除任务后用 `forget_scheduled_task_cache()` 显式失效。
    """
    now = time.monotonic()
    hit = _task_exists_cache.get(name)
    if hit is not None and now - hit[0] < _TASK_EXISTS_TTL_SEC:
        return hit[1]

    exists = False
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", name],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        exists = r.returncode == 0
    except Exception:  # noqa: BLE001
        exists = False

    _task_exists_cache[name] = (now, exists)
    return exists


def forget_scheduled_task_cache(name: str = TASK_NAME) -> None:
    """删除/新建计划任务后调用，让下次查询重新走 schtasks。"""
    _task_exists_cache.pop(name, None)


def remove_scheduled_task(name: str = TASK_NAME) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["schtasks", "/delete", "/tn", name, "/f"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        ok = r.returncode == 0
        if ok:
            # 删掉了就让缓存失效，否则 20 秒内还会说"任务存在"
            forget_scheduled_task_cache(name)
        return ok, (r.stdout or r.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 防火墙

FIREWALL_RULE = "codebuddy2api Gateway (TCP {port})"


def firewall_rule(port: int) -> tuple[bool, str]:
    """查这条入站规则是否存在。

    为什么要查：网关绑 0.0.0.0，**公网直连能不能通完全取决于这条规则**。
    规则在 → 只有 192.168.0.0/24 与 100.64.0.0/10 能连；
    规则不在 → 端口对所有网段敞开（此时只靠 API Key 挡着）。
    """
    name = FIREWALL_RULE.format(port=port)
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"查询失败：{exc}"
    out = f"{r.stdout or ''}{r.stderr or ''}"
    if r.returncode != 0 or "没有与指定条件相符的规则" in out or "No rules match" in out:
        return False, "未找到规则 —— 端口对所有网段敞开"
    remote = ""
    for line in out.splitlines():
        low = line.lower()
        if "remoteip" in low or "远程 ip" in line:
            remote = line.split(":", 1)[-1].strip()
    return True, f"存在（远程 IP 限制：{remote or '未解析'}）"
