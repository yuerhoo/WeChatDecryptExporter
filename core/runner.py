# -*- coding: utf-8 -*-
"""子进程引导：把"提取密钥"和"批量解密"放到独立进程里跑

为什么必须开子进程：
    · 提取密钥要读微信进程内存，需要管理员权限 → 必须用 ShellExecuteW('runas')
      弹 UAC，而 UAC 提权只能对新进程生效，不能给自己提权；
    · 解密几十个库要几十秒到几分钟，放主进程会让界面卡住；
    · 崩溃隔离：解密模块崩了不会带崩整个 GUI。

打包后的坑：PyInstaller 环境下 sys.executable 是程序自己，不能再 `python xxx.py`。
    所以冻结模式下改成 `程序.exe --bootstrap-keys ...`，
    由 main.py 在启动最前面识别参数并分派到 _bootstrap_*.py。

日志协议：子进程把 stdout 重定向到 log 文件，最后写一行
    [BOOTSTRAP-DONE] <rc>
GUI 侧用 QTimer 轮询这个文件（read_new_lines），读到该标记就知道子进程结束了。
"""
import ctypes
import os
import subprocess
import sys

from app_config import FROZEN

# 程序根目录（打包后 = exe 所在目录；源码运行 = wechat_exporter 的上一级仓库根）
ROOT = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 仓库根：源码运行时才有独立的仓库根（find_all_keys.py / decrypt_db.py 在这里）
PROJECT_ROOT = ROOT if FROZEN else os.path.dirname(ROOT)
# 随包资源目录（_bootstrap_*.py 被打进包内，源码运行时就是当前目录）
BUNDLE_DIR = getattr(sys, '_MEIPASS', ROOT)

BOOT_KEYS = os.path.join(BUNDLE_DIR, '_bootstrap_keys.py')
BOOT_DECRYPT = os.path.join(BUNDLE_DIR, '_bootstrap_decrypt.py')
# Windows 下不弹控制台窗口
CREATE_NO_WINDOW = 0x08000000

# 子进程结束标记（后面跟退出码）
DONE_MARK = '[BOOTSTRAP-DONE]'


def _quote(s):
    """给命令行参数加引号（路径里有空格时必须）"""
    return '"%s"' % s


def _cmd(script, args):
    """构造子进程命令

    源码运行：python.exe <脚本> <参数...>
    打包运行：程序.exe --bootstrap-keys/--bootstrap-decrypt <参数...>
    """
    if FROZEN:
        mode = ('--bootstrap-keys' if os.path.basename(script).startswith('_bootstrap_keys')
                else '--bootstrap-decrypt')
        return [sys.executable, mode] + list(args)
    return [sys.executable, script] + list(args)


def run_elevated(script, args, log_path):
    """以管理员权限启动子进程（会弹 UAC），返回是否成功启动

    用 ShellExecuteW 而不是 subprocess：只有 shell32 的 runas 动词能触发提权。
    注意 lpFile 不能带引号（带了会找不到文件），参数串自己拼引号。
    """
    cmd = _cmd(script, args) + [log_path]
    params = ' '.join(_quote(x) for x in cmd[1:])
    try:
        # 先清空/创建日志文件，避免读到上一次的旧内容
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'w', encoding='utf-8'):
            pass
    except Exception:
        pass
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, 'runas', cmd[0], params, None, 0)
    # ShellExecuteW 返回值 > 32 才算成功（<=32 是错误码，比如 5=用户拒绝 UAC）
    return ret > 32


def run_plain(script, args, log_path):
    """以普通权限启动子进程，stdout/stderr 直接写日志文件

    返回 (Popen, 文件对象)。文件对象要一直拿着，否则被 GC 关闭后子进程写不进去。
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, 'w', encoding='utf-8', buffering=1)
    cmd = _cmd(script, args) + [log_path]
    p = subprocess.Popen(
        cmd, stdout=logf, stderr=subprocess.STDOUT,
        cwd=ROOT, creationflags=CREATE_NO_WINDOW)
    return p, logf


def read_new_lines(log_path, last_pos):
    """增量读日志，返回 (新行列表, 新读取位置, 结束标记里的退出码)

    rc 为 None 表示还没读到 [BOOTSTRAP-DONE]，也就是子进程还在跑。
    用二进制 + seek 的方式读，避免文本模式下的编码/换行混在一起导致位置算错。
    """
    lines, rc = [], None
    try:
        with open(log_path, 'rb') as f:
            f.seek(last_pos)
            chunk = f.read().decode('utf-8', 'replace')
            last_pos = f.tell()
        for line in chunk.splitlines():
            if line.startswith(DONE_MARK):
                try:
                    rc = int(line[len(DONE_MARK):].strip() or '0')
                except ValueError:
                    rc = 0
            else:
                lines.append(line.rstrip())
    except Exception:
        pass
    return lines, last_pos, rc
