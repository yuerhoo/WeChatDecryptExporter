# -*- coding: utf-8 -*-
"""会话/联系人数据层

一次加载三份数据，组成主界面列表与导出时需要的全部基础信息：
    contact.db  的 contact 表   → 所有联系人（备注、昵称、微信号、拼音首字母）
    contact.db  的 chat_room 表 → 群成员的群昵称（protobuf 存于 ext_buffer）
    session.db  的 SessionTable → 每个会话的最后消息时间（用于判断"有没有聊天记录"）

分类规则（对应界面四个 Tab）：
    @chatroom 结尾          → 群聊
    gh_ 开头                → 公众号
    命中 SYSTEM_ACCOUNTS    → 系统号（文件传输助手、微信团队等）
    其余                    → 普通联系人
"""
import os
import re
import sqlite3

# 微信官方账号白名单（filehelper=文件传输助手 等）。自定义微信号也可能长得像系统号，
# 所以只能用白名单判定，不能靠前缀猜。
SYSTEM_ACCOUNTS = {
    'filehelper', 'weixin', 'fmessage', 'medianote', 'floatbottle', 'qqmail',
    'tmessage', 'notifymessage', 'qmessage', 'exmail_tool', 'weixinreminder',
    'officialaccounts', 'brandsessionholder', 'notification_messages',
    'qqsync', 'voip', 'qqfriend', 'newsapp', 'weibo', 'weixinlifeserviceaccount',
    'wxitil', 'opencustomerservicemsg', 'userexperience_alarm',
}

# Tab 顺序（与界面顺序一致）
CATEGORIES = ('contact', 'group', 'gh', 'system')
CAT_NAMES = {'contact': '联系人', 'group': '群聊', 'gh': '公众号', 'system': '系统'}


class Sess(object):
    """一个会话（= 一个联系人 / 群 / 公众号 / 系统号）

    用 __slots__ 省内存：4 万多个联系人时，普通对象光 __dict__ 就要几十 MB。
    """
    __slots__ = ('username', 'display', 'remark', 'nick', 'alias', 'category',
                 'last_ts', 'count', 'pinyin', 'pinyin_full')

    def __init__(self, username, remark, nick, alias, pinyin, pinyin_full):
        """参数全部来自 contact.db 的一行（字段名见 load_data 里的 SELECT）

        pinyin / pinyin_full 是排序用的：首字母缩写（wsm）和全拼（wangshiming），
        备注的拼音优先于昵称的拼音，由调用方决定传哪一组进来。
        """
        self.username = username                  # 微信号 / 群ID（导出时的"会话ID"）
        self.remark = remark or ''                # 备注名
        self.nick = nick or ''                    # 昵称
        self.alias = alias or ''                  # 微信号（alias，可搜索）
        # 展示名优先级：备注 > 昵称 > 原始 id
        self.display = remark or nick or username
        self.category = classify(username)
        self.last_ts = 0                          # 最后消息时间（0 = 没聊过）
        self.count = None                         # 消息条数，None = 尚未统计
        self.pinyin = pinyin or ''                # 首字母缩写（wsm）
        self.pinyin_full = pinyin_full or ''      # 全拼（wangshiming）


def classify(username):
    """按规则把 username 归到四个 Tab 之一"""
    if not username:
        return 'contact'
    if username.endswith('@chatroom'):
        return 'group'
    if username.startswith('gh_'):
        return 'gh'
    if username in SYSTEM_ACCOUNTS:
        return 'system'
    return 'contact'


class Data(object):
    """一次加载的全部数据，由后台线程构造后整包抛给主线程（Qt 信号传递）"""

    def __init__(self):
        """只建空容器，真正的填充在 load_data() 里逐个库、逐张表读出来"""
        self.sessions = []        # [Sess] 所有联系人（含从没聊过的）
        self.by_user = {}         # username -> Sess
        self.contacts_map = {}    # username -> (备注, 昵称)，导出时解析发送人用
        self.group_nicks = {}     # 群ID -> {成员wxid: 群昵称}
        self.session_ts = {}      # username -> 最后消息时间（秒）

    def with_chat(self):
        """只保留有聊天记录的会话"""
        return [s for s in self.sessions if s.last_ts]

    def of(self, category):
        """取某个 Tab 的全部会话"""
        return [s for s in self.sessions if s.category == category]


def ro_conn(path):
    """只读打开 sqlite：mode=ro 不写盘；immutable=1 告诉 SQLite 文件不会被改动，
    可以跳过加锁与 WAL 检查（微信的库时不时被进程占用，这样打开最稳）"""
    p = os.path.abspath(path).replace('\\', '/')
    return sqlite3.connect('file:/' + p + '?mode=ro&immutable=1', uri=True)


# ---------- protobuf 微型解析 ----------
# 微信把群成员信息序列化成 protobuf 存在 chat_room.ext_buffer 里，结构大致是：
#   顶层 field 1（可重复）= 一个成员条目，条目内部：
#       field 1 = 成员 wxid
#       field 2 = 群昵称（真正要的字段）
#       field 4 = 邀请人 wxid（不用）
# 只实现 varint / length-delimited / 定长 4、8 字节这几种 wire type，够用且不引依赖。

def _varint(b, i):
    """读一个 base-128 变长整数，返回 (值, 新下标)"""
    v, s = 0, 0
    while True:
        x = b[i]
        i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def _fields(b):
    """把一段 protobuf 拆成 [(字段号, 值)]，调试用（主流程走下面的专用解析）"""
    out, i = [], 0
    n = len(b)
    while i < n:
        try:
            key, i = _varint(b, i)
        except IndexError:
            break
        fno, wt = key >> 3, key & 7
        if wt == 0:
            try:
                v, i = _varint(b, i)
            except IndexError:
                break
            out.append((fno, v))
        elif wt == 2:
            try:
                ln, i = _varint(b, i)
            except IndexError:
                break
            out.append((fno, b[i:i + ln]))
            i += ln
        elif wt == 5:
            out.append((fno, b[i:i + 4])); i += 4
        elif wt == 1:
            out.append((fno, b[i:i + 8])); i += 8
        else:
            break
    return out


def _entry(buf):
    """解析单个群成员条目，返回 (wxid, 群昵称)

    注意：群昵称在 field 2，field 4 是邀请人 —— 早期版本误取 field 4 会把
    邀请人当成群昵称，这里以 field 2 为准。
    """
    j, wxid, disp = 0, None, None
    while j < len(buf):
        try:
            tag, j = _varint(buf, j)
        except IndexError:
            break
        f, wt = tag >> 3, tag & 7
        if wt == 2:
            try:
                ln, j = _varint(buf, j)
            except IndexError:
                break
            val = buf[j:j + ln]
            j += ln
            if f == 1:
                wxid = val.decode('utf-8', 'replace')
            elif f == 2:
                disp = val.decode('utf-8', 'replace')
        elif wt == 0:
            try:
                _, j = _varint(buf, j)
            except IndexError:
                break
        elif wt == 5:
            j += 4
        else:
            break
    return wxid, disp


def parse_chatroom_ext(buf):
    """解析 chat_room.ext_buffer，返回 {成员wxid: 群昵称}

    只包含"确实设置过群昵称"的成员；没设群昵称的人不会出现在结果里，
    导出时自然会退回到备注/昵称。解析失败一律返回空 dict，不影响主流程。
    """
    nicks = {}
    try:
        i = 0
        n = len(buf)
        while i < n:
            try:
                tag, i = _varint(buf, i)
            except IndexError:
                break
            f, wt = tag >> 3, tag & 7
            if wt == 2:
                try:
                    ln, i = _varint(buf, i)
                except IndexError:
                    break
                val = buf[i:i + ln]
                i += ln
                if f == 1:                       # 顶层 field1 = 一个成员条目
                    wxid, disp = _entry(val)
                    if wxid and disp:
                        nicks[wxid] = disp
            elif wt == 0:
                try:
                    _, i = _varint(buf, i)
                except IndexError:
                    break
            elif wt == 5:
                i += 4
            else:
                break
    except Exception:
        pass
    return nicks


# ---------- 主加载 ----------

def load_data(dec_dir, only_load_nicks_for=None):
    """加载一个账号的全部基础数据

    only_load_nicks_for: 只解析这些群的群昵称（None = 全部解析）。
    联系人表几万行很快，但群昵称要逐群解 protobuf，数据量大时可以按需裁剪。
    """
    d = Data()
    cpath = os.path.join(dec_dir, 'contact', 'contact.db')
    if not os.path.exists(cpath):
        raise FileNotFoundError('找不到 %s' % cpath)

    # 1) 联系人：备注 / 昵称 / 微信号 / 拼音（备注的拼音优先于昵称的拼音）
    c = ro_conn(cpath)
    for username, alias, remark, nick, py, qp, rpy, rqp in c.execute(
            'SELECT username, alias, remark, nick_name, pin_yin_initial, quan_pin, '
            'remark_pin_yin_initial, remark_quan_pin FROM contact'):
        if not username:
            continue
        s = Sess(username, remark, nick, alias,
                 (rpy or py or ''), (rqp or qp or ''))
        d.sessions.append(s)
        d.by_user[username] = s
        d.contacts_map[username] = (remark or '', nick or '')
    c.close()

    # 2) 群昵称：每个群的 ext_buffer 单独解析，单群失败不影响其他群
    try:
        c = ro_conn(cpath)
        for username, buf in c.execute('SELECT username, ext_buffer FROM chat_room'):
            if buf and (only_load_nicks_for is None or username in only_load_nicks_for):
                d.group_nicks[username] = parse_chatroom_ext(buf)
        c.close()
    except Exception:
        pass

    # 3) 会话最后消息时间：SessionTable 里有 last_timestamp 就说明聊过
    spath = os.path.join(dec_dir, 'session', 'session.db')
    if os.path.exists(spath):
        try:
            c = ro_conn(spath)
            for username, ts, sort_ts in c.execute(
                    'SELECT username, last_timestamp, sort_timestamp FROM SessionTable'):
                if ts and ts > 1000000000000:      # 毫秒 → 秒（微信 4.0 存的是毫秒）
                    ts //= 1000
                d.session_ts[username] = ts or 0
            c.close()
        except Exception:
            pass
        # 把时间戳回填到 Sess 上
        for s in d.sessions:
            s.last_ts = d.session_ts.get(s.username, 0)

    return d


def key_status(db_dir, keys_file):
    """评估密钥缓存的健康度，返回 (valid, stale, total_db)

    valid     缓存里的密钥仍与库文件前 16 字节 salt 对得上（可以直接解密）
    stale     缓存里有这条记录，但库的 salt 变了（微信重建过库，密钥失效，需重新提取）
    total_db  目录里 .db 文件总数（没有密钥的新库不算 stale，多为空库）

    原理：SQLCipher 的库文件前 16 字节是随机 salt，微信重建库时会换 salt。
    """
    import json
    entries = {}
    if os.path.exists(keys_file):
        try:
            with open(keys_file, encoding='utf-8') as f:
                entries = json.load(f)
        except Exception:
            entries = {}
    valid, stale, total_db = 0, 0, 0
    if not os.path.isdir(db_dir):
        return 0, 0, 0
    for root, _dirs, files in os.walk(db_dir):
        for fn in files:
            # -wal / -shm 是 SQLite 的附属文件，不是独立数据库
            if not fn.endswith('.db') or fn.endswith('-wal') or fn.endswith('-shm'):
                continue
            p = os.path.join(root, fn)
            total_db += 1
            # keys.json 的键是"相对 db_dir 的反斜杠路径"，必须保持一致才能查到
            info = entries.get(os.path.relpath(p, db_dir).replace('/', '\\'))
            if not info:
                continue
            try:
                with open(p, 'rb') as f:
                    salt = f.read(16).hex()
            except Exception:
                continue
            if info.get('salt') == salt:
                valid += 1
            else:
                stale += 1
    return valid, stale, total_db


def check_keys(db_dir, keys_file):
    """按 salt 集合比对，返回 (匹配数, 总数, 缺失相对路径列表)

    与 key_status 的区别：这里不看具体哪个文件对应哪把密钥，只看"这个 salt 有没有密钥"。
    用于排查"哪些库还没解出来"。
    """
    import json
    if not os.path.isdir(db_dir):
        return 0, 0, []
    salts = {}
    if os.path.exists(keys_file):
        try:
            with open(keys_file, encoding='utf-8') as f:
                for info in json.load(f).values():
                    salts[info.get('salt', '')] = True
        except Exception:
            pass
    ok, total, missing = 0, 0, []
    for root, _dirs, files in os.walk(db_dir):
        for fn in files:
            if not fn.endswith('.db') or fn.endswith('-wal') or fn.endswith('-shm'):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, 'rb') as f:
                    salt = f.read(16).hex()
            except Exception:
                continue
            total += 1
            if salt in salts:
                ok += 1
            else:
                missing.append(os.path.relpath(p, db_dir))
    return ok, total, missing
