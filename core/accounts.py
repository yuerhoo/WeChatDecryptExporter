# -*- coding: utf-8 -*-
"""多微信账号管理

账号从哪来：不是让用户手填，而是直接扫描「微信数据目录」里真实存在的 wxid_* 文件夹。
    用户选的目录是 <xwechat_files>（也可能是更深的 db_storage，normalize_root 会自动归位）
    每个账号的库路径由程序拼接：  <xwechat_files>/<wxid>/db_storage

每个账号在程序自己的数据区里有一个独立文件夹（互不干扰，可单独删除）：
    <WeChatData>/accounts/<wxid>/
        account.json    账号配置（wxid / 文件夹名 / 显示名称 / 微信数据目录 / 创建时间）
        keys.json       密钥缓存（find_all_keys 的产物，按库相对路径索引）
        manifest.json   增量解密清单（mtime + size + salt，用来跳过没变的库）
        counts.json     会话消息数缓存（加速列表展示）
        sync.log        启动时自动增量同步的日志
        keys.log        提权提取密钥的日志
        decrypt.log     手动解密时的日志
        db/             解密后的明文数据库
"""
import json
import os
import re
import shutil
import time

from app_config import ACCOUNTS_ROOT, DATA_ROOT, Prefs

# 文件夹名安全化：只留字母数字和 _ - @ .，其余替换成下划线
_SAFE = re.compile(r'[^0-9A-Za-z_\-@.]')
# QSettings 里存的两个键
ROOT_KEY = 'wechat/root'            # 微信数据目录（xwechat_files）
CURRENT_KEY = 'account/current'     # 当前选中的账号 id（= wxid 文件夹名）


# ---------- 微信数据目录 ----------

def get_root():
    """读用户选定的微信数据目录（未设置时返回空串）"""
    return Prefs().get(ROOT_KEY, '') or ''


def set_root(path):
    """保存微信数据目录（保存前先归一化到 xwechat_files 这一级）"""
    Prefs().set(ROOT_KEY, normalize_root(path))


def normalize_root(path):
    """把用户随手选的任意层级路径统一归到 xwechat_files

    用户可能选到：
        .../xwechat_files               → 原样返回
        .../xwechat_files/wxid_xxx      → 上跳一级
        .../xwechat_files/wxid_xxx/db_storage → 上跳两级
    判定依据是"父目录里确实存在 wxid_* 兄弟目录"，避免误伤名字里带 wxid 的普通文件夹。
    """
    p = os.path.normpath((path or '').strip().strip('"'))
    if not p or not os.path.isdir(p):
        return p
    b = os.path.basename(p).lower()
    if b == 'db_storage':
        p = os.path.dirname(p)
        b = os.path.basename(p).lower()
    if b.startswith('wxid'):
        parent = os.path.dirname(p)
        # 只有确认父目录里确实是一堆 wxid_* 时才上升，否则保持原样
        try:
            subs = os.listdir(parent)
        except OSError:
            subs = []
        if sum(1 for s in subs if s.lower().startswith('wxid')) >= 1:
            p = parent
    return p


def scan_wxids(root):
    """列出 root 下所有"像账号"的文件夹：wxid_ 开头且内部有 db_storage"""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        d = os.path.join(root, n)
        if (os.path.isdir(d) and n.lower().startswith('wxid')
                and os.path.isdir(os.path.join(d, 'db_storage'))):
            out.append(n)
    return out


def db_dir_of(root, wxid):
    """账号的微信原始库路径：<xwechat_files>/<wxid>/db_storage"""
    return os.path.join(root, wxid, 'db_storage')


def guess_wxid(db_dir):
    """从任意层级路径反推微信号（db_storage 的上一级）"""
    p = os.path.normpath(db_dir or '')
    if os.path.basename(p).lower() == 'db_storage':
        p = os.path.dirname(p)
    return os.path.basename(p)


# ---------- 账号目录下的各类路径 ----------

def _folder_of(acc):
    """取账号的文件夹名（兼容 dict / 纯字符串两种入参）"""
    if isinstance(acc, dict):
        return acc.get('folder') or acc.get('wxid') or acc.get('id') or 'acc'
    return _SAFE.sub('_', str(acc)) or 'acc'


def acc_dir(acc):
    """账号数据目录：<WeChatData>/accounts/<文件夹名>"""
    return os.path.join(ACCOUNTS_ROOT, _folder_of(acc))


def account_json(acc):
    """账号主配置文件：记录微信号、数据目录、当前选中的库根等"""
    return os.path.join(acc_dir(acc), 'account.json')


def keys_file(acc):
    """密钥缓存文件"""
    return os.path.join(acc_dir(acc), 'keys.json')


def manifest_file(acc):
    """增量解密清单"""
    return os.path.join(acc_dir(acc), 'manifest.json')


def counts_file(acc):
    """会话消息数缓存"""
    return os.path.join(acc_dir(acc), 'counts.json')


def sync_log(acc):
    """增量同步日志：每个库里哪些表被更新过，都记在这里"""
    return os.path.join(acc_dir(acc), 'sync.log')


def keys_log(acc):
    """密钥提取过程的完整输出（含 UAC 提升子进程的 stdout）"""
    return os.path.join(acc_dir(acc), 'keys.log')


def decrypt_log(acc):
    """解密过程的完整输出，失败时靠它定位是哪个库、哪个 salt 出错"""
    return os.path.join(acc_dir(acc), 'decrypt.log')


def dec_dir_of(acc):
    """解密输出目录：<账号目录>/db（结构复制自微信的 db_storage）"""
    return os.path.join(acc_dir(acc), 'db')


def has_data(acc):
    """是否已经解密过（用 contact.db 是否存在作为标志）"""
    return os.path.exists(os.path.join(dec_dir_of(acc), 'contact', 'contact.db'))


# ---------- 读写 ----------

def _read_name(folder):
    """从 account.json 里读用户自定义的显示名称（没有则返回空串）"""
    try:
        with open(os.path.join(ACCOUNTS_ROOT, folder, 'account.json'),
                  encoding='utf-8') as f:
            return (json.load(f).get('name') or '').strip()
    except Exception:
        return ''


def load_accounts(root=None):
    """扫描微信数据目录，返回 (账号列表, 当前账号 id)

    账号 dict 的字段：
        id / wxid    微信号文件夹名（如 wxid_xxx_c779）
        folder       程序数据区里的文件夹名（安全化后的 id）
        name         显示名称（用户可改，默认等于 id）
        db_dir       微信原始库目录
        root         微信数据目录（xwechat_files）
        dec_dir      解密输出目录
    当前账号取自 QSettings；若该账号已不存在（文件夹被删了）则退回第一个。
    """
    root = normalize_root(root if root is not None else get_root())
    accs = []
    for wxid in scan_wxids(root):
        acc = {'id': wxid, 'wxid': wxid, 'folder': _SAFE.sub('_', wxid),
               'name': _read_name(_SAFE.sub('_', wxid)) or wxid,
               'db_dir': db_dir_of(root, wxid), 'root': root}
        acc['dec_dir'] = dec_dir_of(acc)
        accs.append(acc)
    cur = Prefs().get(CURRENT_KEY, '') or ''
    if not any(a['id'] == cur for a in accs):
        cur = accs[0]['id'] if accs else ''
    return accs, cur


def ensure(acc):
    """为账号创建目录骨架并写 account.json（已存在则保留原名与创建时间）

    调用时机：用户在设置里第一次选中某个微信号，或点保存时。
    """
    d = acc_dir(acc)
    os.makedirs(os.path.join(d, 'db'), exist_ok=True)
    cfg = {'wxid': acc['id'], 'folder': _folder_of(acc),
           'name': acc.get('name') or acc['id'], 'db_dir': acc.get('db_dir', '')}
    try:
        # 已有配置：继承 created，且没传 name 时沿用旧名称（避免改名成 wxid）
        with open(account_json(acc), encoding='utf-8') as f:
            old = json.load(f)
        cfg['created'] = old.get('created') or time.strftime('%Y-%m-%d %H:%M:%S')
        if not acc.get('name'):
            cfg['name'] = old.get('name') or acc['id']
    except Exception:
        cfg['created'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(account_json(acc), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return acc


def save_accounts(accs, current=None):
    """保存全部账号（就是逐个写 account.json）并记录当前账号"""
    os.makedirs(ACCOUNTS_ROOT, exist_ok=True)
    for a in accs:
        ensure(a)
    if current:
        Prefs().set(CURRENT_KEY, current)


def get_current(root=None):
    """取当前账号 dict；没有任何账号时返回 None"""
    accs, cur = load_accounts(root)
    for a in accs:
        if a['id'] == cur:
            return a
    return None


def set_current(acc_id):
    """切换当前账号（只记 id，不碰文件）"""
    Prefs().set(CURRENT_KEY, acc_id)


def remove_account(acc, delete_files=True):
    """删除账号在程序数据区的整个目录（配置 + 密钥 + 解密库）

    微信原始数据不受影响。路径做了前缀校验，防止误删到 accounts 外面去。
    """
    d = os.path.abspath(acc_dir(acc))
    root = os.path.abspath(ACCOUNTS_ROOT)
    if delete_files and d.startswith(root + os.sep) and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
