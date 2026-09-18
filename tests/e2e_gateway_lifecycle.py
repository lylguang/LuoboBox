"""端到端：用萝卜盒自己的代码，真实拉起 / 体检 / 停止网关。

这是自检（--selftest）没有覆盖的部分：selftest 只验证"构建链路能跑通"，
不会真的起进程。这个脚本补上最后一块证据 —— 双击启动后点「启动网关」
到底能不能把服务拉起来、UI 能不能拿到数据、点「停止」能不能收干净。

安全约定：**只在内存里改端口**。结束时把配置文件按原始字节还原，
所以即使中途异常也不会污染用户配置（踩过这个坑：Config() 空构造 + save()
会把默认配置覆盖到真实文件上）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from luobobox.config import Config, pick_free_port, port_free  # noqa: E402
from luobobox.gateway import GatewayManager  # noqa: E402


def main() -> int:
    cfg = Config.load()
    cfg_path = cfg.path

    # 备份真实配置（内容 + 存在性），结束原样还原
    with tempfile.TemporaryDirectory() as td:
        stash = Path(td) / "config.json"
        existed = cfg_path.is_file()
        if existed:
            shutil.copy2(cfg_path, stash)

        original_port = int(cfg.get("gateway.port", 8788))
        target = pick_free_port(8899, 8999)
        print(f"[cfg] 配置文件={cfg_path}")
        print(f"[cfg] 当前端口={original_port}  测试端口={target}")
        print(f"[cfg] 解释器={cfg.get('gateway.python')}")

        if not port_free(target):
            print(f"FAIL 端口 {target} 不空闲")
            return 1

        mgr = GatewayManager(cfg, log=lambda m: print("   " + m))
        # 在内存里换端口：不 save()，改完用完即弃
        cfg.set("gateway.port", target)

        ok = True
        try:
            print("\n[1] validate + build_command")
            problems = [p for p in cfg.validate() if "端口" not in p]
            if problems:
                print("FAIL 配置校验未通过：" + "；".join(problems))
                return 1
            print("   validate -> 无问题")
            argv = mgr.build_command()
            print("   " + " ".join(argv))
            if not Path(argv[0]).is_file():
                print(f"FAIL 解释器不存在：{argv[0]}")
                return 1
            if not Path(argv[1]).is_file():
                print(f"FAIL 入口不存在：{argv[1]}")
                return 1
            print("PASS 命令与文件都在")

            print("\n[2] start()")
            t0 = time.time()
            started, detail = mgr.start(wait_seconds=40)
            dt = time.time() - t0
            print(f"   start -> {started} / {detail}  （{dt:.1f}s）")
            if not started:
                print("FAIL 启动失败")
                print("   —— 网关日志末尾 ——")
                for line in mgr.tail_log(30).splitlines()[-15:]:
                    print("   | " + line[:160])
                ok = False
            else:
                print(f"PASS 网关已启动 PID={mgr.pid} state={mgr.state_label}")

            print("\n[3] collect_health()")
            snap = mgr.collect_health()
            print(f"   ok={snap.ok} detail={snap.detail} latency={snap.latency_ms}ms")
            print(f"   models={snap.models} credentials={len(snap.credentials)}")
            if snap.credits:
                print(f"   credits keys={list(snap.credits.keys())[:10]}")
            if not snap.ok:
                print("FAIL 健康检查失败")
                ok = False
            else:
                print("PASS UI 需要的数据都拿到了")

            print("\n[4] 日志")
            log = mgr.tail_log(20)
            print(f"   日志 {len(log)} 字符，末尾 3 行：")
            for line in log.splitlines()[-3:]:
                print("     | " + line[:150])
            if not log.strip():
                print("WARN 日志为空")
                ok = False
            else:
                print("PASS 日志有内容")

        finally:
            print("\n[5] stop()")
            stopped, detail = mgr.stop(timeout=15)
            print(f"   stop -> {stopped} / {detail}")
            time.sleep(1.0)
            print(f"   停止后 state={mgr.state_label}")
            if not port_free(target):
                print(f"FAIL 端口 {target} 仍被占用")
                ok = False
            else:
                print("PASS 端口已释放，收干净了")

            # 原样还原配置文件（按字节），确保零污染
            try:
                if existed and stash.is_file():
                    shutil.copy2(stash, cfg_path)
                    print(f"   配置已还原（{cfg_path.stat().st_size} 字节）")
                elif not existed and cfg_path.is_file():
                    cfg_path.unlink()
                    print("   配置已删除（原本不存在）")
            except Exception as exc:  # noqa: BLE001
                print(f"   WARN 配置还原失败：{exc}")

        print("\n结果：" + ("全部通过" if ok else "存在失败项"))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
