# -*- coding: utf-8 -*-
"""检测微信是否正在使用某个数据目录 —— 也就是"这个微信号当前有没有登录"

原理：微信运行时会独占打开它正在使用的数据库文件。用 CreateFileW 以
dwShareMode=0（独占方式）尝试打开，打不开（共享冲突）就说明有进程正占用它。

这是判断"能不能从内存里提取到密钥"最直接的依据：没有登录的微信号，
它的库没被打开，密钥根本不在微信进程内存里 —— 用户遇到"0/20 salts 找到密钥"
基本都是这个原因（在设置里选的是另一个没登录的微信号）。
"""
import ctypes
import os

_GENERIC_READ = 0x80000000        # 只读
_OPEN_EXISTING = 3                # 文件必须已存在
_INVALID_HANDLE = (-1, 0xFFFFFFFFFFFFFFFF)   # 32/64 位下的 INVALID_HANDLE_VALUE
_ERR_FILE_NOT_FOUND = 2
_ERR_ACCESS_DENIED = 5
_ERR_SHARING_VIOLATION = 32       # 共享冲突 = 被别的进程独占打开
_ERR_LOCK_VIOLATION = 33


def _kernel32():
    """拿 kernel32 句柄（每次现取，避免模块导入时就依赖 Windows API）"""
    return ctypes.windll.kernel32


def is_locked(path):
    """文件是否被其他进程占用。True=占用, False=未被占用, None=无法判断"""
    try:
        h = _kernel32().CreateFileW(str(path), _GENERIC_READ, 0, None,
                                   _OPEN_EXISTING, 0, None)
    except Exception:
        return None
    if h in _INVALID_HANDLE:
        err = _kernel32().GetLastError()
        if err in (_ERR_SHARING_VIOLATION, _ERR_LOCK_VIOLATION):
            return True
        if err == _ERR_FILE_NOT_FOUND:
            return False
        if err == _ERR_ACCESS_DENIED:
            # 权限不足时既可能是被占用也可能是 ACL 限制，视为未知
            return None
        return None
    # 能打开 → 没人独占它 → 关闭句柄即可
    _kernel32().CloseHandle(h)
    return False


def account_in_use(db_dir):
    """该微信数据目录是否正被微信使用（= 已登录）。None 表示无法判断

    探测顺序：先看 session.db / contact.db（最先被微信打开的两个库），
    都没有就随便找一个 .db 当探针。
    """
    if not db_dir or not os.path.isdir(db_dir):
        return None
    probes = []
    for rel in ('session\\session.db', 'contact\\contact.db'):
        p = os.path.join(db_dir, rel)
        if os.path.exists(p):
            probes.append(p)
    if not probes:
        for root, dirs, files in os.walk(db_dir):
            for f in files:
                if f.endswith('.db'):
                    probes.append(os.path.join(root, f))
                    break
            if probes:
                break
    verdicts = [is_locked(p) for p in probes]
    # 任意一个被占用就算已登录；全部确认没占用才算未登录
    if any(v is True for v in verdicts):
        return True
    if verdicts and all(v is False for v in verdicts):
        return False
    return None


def xwechat_root(db_dir):
    """从 .../xwechat_files/<wxid>/db_storage 往上找到 xwechat_files 目录

    判定依据：某一层目录下存在 wxid_ 开头的兄弟目录。
    """
    p = os.path.abspath(db_dir or '')
    for _ in range(4):
        parent = os.path.dirname(p)
        if not parent or parent == p:
            break
        p = parent
        try:
            subs = os.listdir(p)
        except Exception:
            continue
        if any(d.startswith('wxid_') for d in subs):
            return p
    return None


def logged_in_accounts(db_dir):
    """当前正被微信占用的账号目录名列表（通常只有一个）"""
    root = xwechat_root(db_dir)
    if not root:
        return []
    out = []
    try:
        subs = os.listdir(root)
    except Exception:
        return []
    for acc in subs:
        if not acc.startswith('wxid_'):
            continue
        probe = os.path.join(root, acc, 'db_storage', 'session', 'session.db')
        if os.path.exists(probe) and is_locked(probe) is True:
            out.append(acc)
    return out


def account_name_of(db_dir):
    """从 db_storage 路径推断微信号目录名，如 wxid_xxx_c779"""
    p = os.path.abspath(db_dir or '').rstrip('\\/')
    name = os.path.basename(p)
    if name.lower() == 'db_storage':
        name = os.path.basename(os.path.dirname(p))
    return name


def describe(db_dir):
    """给界面用的状态说明，返回 (状态, 文案)

    状态：'active' 已登录 / 'inactive' 未登录 / 'unknown' 判断不了
    未登录时会附带"当前登录的是谁"，帮用户直接看出选错了账号。
    """
    in_use = account_in_use(db_dir)
    if in_use is True:
        return 'active', '微信正在使用该账号，可提取密钥'
    if in_use is None:
        return 'unknown', '无法判断该账号是否已登录'
    me = account_name_of(db_dir)
    others = [a for a in logged_in_accounts(db_dir) if a != me]
    if others:
        return 'inactive', ('该微信号未登录 —— 当前微信登录的是 %s。'
                            '密钥只存在于已登录账号的内存中。' % '、'.join(others))
    return 'inactive', '该微信号未登录 —— 微信没有打开它的数据库，密钥不在内存里'
