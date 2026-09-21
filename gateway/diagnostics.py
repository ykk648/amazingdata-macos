"""进程级诊断：把「无声退出」变成可读日志。

网关历史上出现过进程在请求中途静默消失（containerd 只记录 shim disconnected、
Docker 侧退出码 0、uvicorn 没有任何 shutdown 行），导致客户端看到
"Remote end closed connection without response"。这里把所有能留下痕迹的出口都接管：

* ``faulthandler`` 在致命信号（SEGV/BUS/ABRT/…）时打印全部线程栈；
* 信号处理器在链式调用原有处理器（uvicorn 的优雅退出）之前先记录信号名与栈；
* ``threading.excepthook`` / ``sys.unraisablehook`` 记录线程内未处理异常；
* ``atexit`` 记录解释器退出，便于区分「自己退出」与「被杀」。
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import signal
import sys
import threading
from types import FrameType
from typing import Any

_TERM_SIGNALS = ("SIGTERM", "SIGINT", "SIGQUIT", "SIGHUP", "SIGUSR1", "SIGUSR2")


def _signal_number(name: str) -> int | None:
    value = getattr(signal, name, None)
    return value if isinstance(value, int) else None


def install_crash_diagnostics(log: logging.Logger) -> None:
    """让致命信号和解释器退出都留下日志。"""
    try:
        faulthandler.enable()
    except (OSError, RuntimeError) as exc:  # pragma: no cover - 环境相关
        log.warning("faulthandler.enable failed: %s", exc)

    # 致命信号（SEGV/BUS/ABRT/…）由 faulthandler.enable() 覆盖；再对它们调用
    # register() 只会被拒绝（"signal 11 cannot be registered, use enable() instead"）。

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            log.critical("thread %s called SystemExit(%r)", args.thread, args.exc_value)
        else:
            log.critical(
                "unhandled exception in thread %s", args.thread,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

    threading.excepthook = _thread_hook

    def _unraisable_hook(unraisable: Any) -> None:
        log.error(
            "unraisable exception in %r: %s",
            getattr(unraisable, "object", None),
            getattr(unraisable, "exc_value", None),
        )

    sys.unraisablehook = _unraisable_hook

    def _on_exit() -> None:
        log.critical("interpreter exiting normally (pid=%s)", os.getpid())

    atexit.register(_on_exit)


def install_signal_logging(log: logging.Logger) -> None:
    """在 uvicorn 的处理器外面再包一层，先记录信号再交给原处理器。"""

    for name in _TERM_SIGNALS:
        number = _signal_number(name)
        if number is None:
            continue
        previous = signal.getsignal(number)

        def _handler(
            signum: int,
            frame: FrameType | None,
            _name: str = name,
            _previous: Any = previous,
        ) -> None:
            log.critical("received %s (%s)", _name, signum)
            try:
                stacks = sys._current_frames()
                for thread_id, stack in stacks.items():
                    log.critical(
                        "stack of thread %s:\n%s",
                        thread_id,
                        "".join(__import__("traceback").format_stack(stack)),
                    )
            except Exception as exc:  # pragma: no cover - 日志自身不能抛
                log.warning("failed to dump stacks: %s", exc)
            if callable(_previous):
                _previous(signum, frame)
            elif _previous == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(number, _handler)
        except (OSError, ValueError) as exc:  # pragma: no cover
            log.warning("cannot install handler for %s: %s", name, exc)
