# -*- coding: utf-8 -*-
"""导出层：把会话消息写进 Excel（两种输出模式）

两种模式（界面上的「输出方式」）：
    per_file      每个会话一个 xlsx —— 文件名 <序号>_<备注名/群名>_<时间后缀>.xlsx
    per_sheet     所有会话合并成一个 xlsx，每个会话一个工作表（没有汇总页）

每种模式生成的每一张工作表，版面都跟「聊天记录导出模板.xlsx」一致：

    第 1 行   A1 = 会话信息
    第 2 行   A2 微信ID   B2 <会话 username>   C2 昵称   D2 <会话显示名>
    第 3 行   A3 导出工具  B3 <程序名>          C3 导出时间 D3 YYYY-MM-DD HH:MM:SS
    第 4 行   空行
    第 5 行   表头：序号 / 时间 / 发送者身份 / 消息类型 / 内容 / 发送人ID
    第 6 行起 消息数据，序号从 1 开始，每个工作表各自重新编号

模板里没有任何合并单元格、底色、边框和冻结窗格，这里也一律不加：
一是保持和模板 1:1 一致，二是 write_only 模式本身不支持 merge_cells。

输出位置统一由 app_config.export_dir() 决定（<程序目录>/微信聊天记录导出目录），
调用方不能改路径，保证"所有导出文件都在同一个目录里"。

两个关键的工程细节：
  1) 用 openpyxl 的 write_only 模式流式写盘。十万级消息如果走普通模式，
     内存会吃掉几百 MB；write_only 是边写边落，内存占用恒定。
  2) 先写 <文件名>.tmp 再 os.replace 重命名，形成"原子落盘"：
     中途崩溃/断电不会留下一个看起来正常、实际缺行的 xlsx。
"""
import os
import re
import time
import datetime

from openpyxl import Workbook

from app_config import APP_NAME, export_dir
from . import messages

# 表头（模板第 5 行）。顺序不能改：序号 → 时间 → 发送者身份 → 消息类型 → 内容 → 发送人ID
HEADERS = ['序号', '时间', '发送者身份', '消息类型', '内容', '发送人ID']

# 消息类型在 Excel 里的中文写法（模板里写的是"文本消息"，带"消息"后缀）
# 键是 local_type 去掉高位标志后的真实类型码，与 messages.EXPORT_TYPES 一一对应；
# 42 是微信名片（模板没列，但库里确实有，补上比留"类型42"好看）
TYPE_LABELS = {1: '文本消息', 3: '图片消息', 34: '语音消息', 42: '名片消息',
               43: '视频消息', 47: '表情消息', 48: '位置消息',
               49: '文件消息/链接消息', 10000: '系统消息'}
# 认不出来的类型统一写这个，避免出现"类型42"这类半截标签
TYPE_FALLBACK = '其他消息'

# 列宽（字符数）。模板给的宽度是"序号 14.75 / 时间 30.25 / 内容 24"，
# 序号和时间明显过宽、内容过窄，这里按实际内容重新配：
#   序号 6（两位数够用）、时间 20（正好放下 19 个字的时间串）、
#   发送者身份 18、消息类型 12、内容 60、发送人ID 26
COL_WIDTHS = [6, 20, 18, 12, 60, 26]

# Excel 工作表名 / 文件名里不允许出现的字符
BAD_CHARS = re.compile(r'[\\/:*?"<>\[\]]')
# Excel 单元格不接受的控制字符（消息正文里偶尔混有 \x00-\x1f 之类）
ILLEGAL = re.compile(r'[\000-\010]|[\013-\014]|[\016-\037]')
# 单元格字符上限 32767，超过会被 openpyxl 拒绝；留点余量并补省略号
MAX_CELL = 32000


def cell(v):
    """清洗要写进单元格的值：去掉非法控制字符 + 截断超长内容

    微信消息正文经常包含不可见控制字符，openpyxl 遇到会直接抛
    IllegalCharacterError 并让整批导出失败，所以必须逐格过滤。
    """
    if isinstance(v, str):
        v = ILLEGAL.sub('', v)
        if len(v) > MAX_CELL:
            v = v[:MAX_CELL] + '…'
    return v


def safe_name(name, limit=60):
    """把会话名变成合法的文件名/工作表名：替换非法字符、去掉首尾点和空白"""
    n = BAD_CHARS.sub('_', name or '').strip().strip('.') or '未命名'
    return n[:limit]


def unique_name(used, name):
    """工作表名去重（Excel 要求同一工作簿内工作表名唯一），重名追加 _2、_3…"""
    if name not in used:
        used.add(name)
        return name
    i = 2
    while '%s_%d' % (name, i) in used:
        i += 1
    name = '%s_%d' % (name, i)
    used.add(name)
    return name


def fmt_ts(ts):
    """epoch 秒 → 'YYYY-MM-DD HH:MM:SS'（导出表格里的时间列）"""
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


class ExportOpts(object):
    """一次导出任务的全部参数（由导出弹窗组装，传给后台线程）"""

    def __init__(self):
        """默认值 = 「每个会话单独一个文件 + 只导文本 + 不限时间」

        弹窗里所有控件都会覆盖一遍这些字段，默认值只在被代码直接调用
        （比如调试脚本）时生效。
        """
        self.mode = 'per_file'      # per_file / per_sheet
        self.t0 = None              # 起始时间（epoch 秒），None = 不限
        self.t1 = None              # 结束时间（epoch 秒），None = 不限
        self.types = {1}            # 消息类型集合；None = 全部类型
        self.keyword = ''           # 内容关键词过滤（主界面没用到，保留能力）

    def range_label(self):
        """时间范围在文件名里的写法：20261007_20261105；选了「全部」就写「全部」"""
        f = lambda ts: datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d')
        if self.t0 and self.t1:
            return '%s_%s' % (f(self.t0), f(self.t1))
        return '全部'


def _save_wb(wb, path):
    """原子落盘：先存 .tmp，成功后再替换目标文件

    这样即使导出过程被取消/崩溃，也不会留下半截的 xlsx；
    同名文件存在时 os.replace 会直接覆盖（正是我们想要的行为）。
    """
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    wb.save(tmp)
    os.replace(tmp, path)


def _setup_sheet(ws):
    """给一张新工作表配好模板规定的列宽（只配列宽，不加样式和冻结）

    在 write_only 模式下 column_dimensions 必须在写入任何单元格之前设置，
    否则 openpyxl 会忽略掉（此时行已经开始流式写出去了）。
    """
    for i, w in enumerate(COL_WIDTHS):
        ws.column_dimensions[chr(ord('A') + i)].width = w


def _write_session(ws, sess, rows, export_time):
    """把「一张表 = 一个会话」按模板版面写满

    参数：
        ws           目标工作表（write_only 模式创建出来的）
        sess         core.contacts.Sess，提供 username 和 display
        rows         messages.scan_chat 的返回值
        export_time  本次导出的统一时间串（同一次导出里所有表都用同一个值）

    写出的行结构见模块顶部注释。序号是 Python 侧自增的，
    每个工作表从 1 重新开始，和模板示例一致。
    """
    # —— 第 1~4 行：会话信息区（B2/C2/D2 的落点必须和模板完全对齐）——
    ws.append(['会话信息'])
    ws.append(['微信ID', cell(sess.username), '昵称', cell(sess.display)])
    ws.append(['导出工具', APP_NAME, '导出时间', export_time])
    ws.append([None])          # 第 4 行留空，隔开信息区和数据表

    # —— 第 5 行：表头 ——
    ws.append(HEADERS)

    # —— 第 6 行起：数据 ——
    for i, r in enumerate(rows, 1):
        # scan_chat 的行元组：(sort_seq, create_time, 发送人wxid, 发送人显示名,
        #                      类型码, 类型名, 内容)
        ws.append([
            i,                                  # 序号：本表内自增
            fmt_ts(r[1]),                       # 时间
            cell(r[3]),                         # 发送者身份（群昵称 > 备注 > 昵称 > wxid）
            TYPE_LABELS.get(r[4], TYPE_FALLBACK),  # 消息类型（如"文本消息"）
            cell(r[6]),                         # 内容（已去控制字符、超长截断）
            cell(r[2]),                         # 发送人ID（wxid）
        ])


def export_sessions(sessions, opts, data, dec_dir, progress=None, cancelled=None):
    """导出多个会话

    参数：
        sessions   选中的会话列表（core.contacts.Sess）
        opts       ExportOpts
        data       联系人/群昵称数据（core.contacts.Data），给发送人解析用
        dec_dir    解密后的数据库根目录
        progress   回调 progress(done, total, 当前名, 本会话已写行数, 本会话总行数)
        cancelled  回调，返回 True 表示用户点了取消

    返回 dict：
        ok/skipped/failed  成功/无消息跳过/异常失败的会话数
        total              写入的总消息条数
        files              生成的文件绝对路径列表
        errors             失败原因文本
        folder             导出目录（进度窗用来"打开文件夹"）
        elapsed            耗时（秒）
        cancelled          是否被取消
    """
    t_start = time.time()
    kw = opts.keyword.strip().lower() or None
    n = len(sessions)
    res = {'ok': 0, 'skipped': 0, 'failed': 0, 'total': 0,
           'files': [], 'errors': [], 'folder': '', 'elapsed': 0,
           'cancelled': False}

    # 输出目录固定为「微信聊天记录导出目录」（会顺带确保目录存在）
    suffix = opts.range_label()
    out_dir = export_dir()
    # 兜底：旧配置里可能还存着已经删掉的 single_sheet，一律按"每会话一个文件"处理
    mode = opts.mode if opts.mode in ('per_file', 'per_sheet') else 'per_file'
    per_file = mode == 'per_file'
    res['folder'] = out_dir

    # 本次导出统一的时间戳：写进每张表的「导出时间」，同一次导出不会出现两种时间
    export_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    wb = None            # per_sheet 模式共用的工作簿（所有会话写完后一次性落盘）
    sheet_names = set()  # 已用过的工作表名，用于去重

    for idx, sess in enumerate(sessions):
        # 每个会话开始前检查一次取消，保证点击「取消」能及时生效
        if cancelled is not None and cancelled():
            res['cancelled'] = True
            break
        if progress:
            # cur=0 表示"开始处理这个会话"，进度窗据此显示当前会话名
            progress(idx, n, '%02d_%s' % (idx + 1, safe_name(sess.display, 40)), 0, 0)

        try:
            # 真正去读消息表（跨 message_x.db 分库扫描 + 时间/类型/关键词过滤）
            rows = messages.scan_chat(dec_dir, sess, data,
                                      t0=opts.t0, t1=opts.t1,
                                      types=opts.types or None, keyword=kw)
        except Exception as e:
            # 单个会话读失败不影响其他会话，记下来最后统一提示
            res['failed'] += 1
            res['errors'].append('%s: %s' % (sess.display, e))
            continue
        if rows is None or (cancelled is not None and cancelled()):
            res['cancelled'] = True
            break
        if not rows:
            # 该会话在选定范围内没有消息（比如只选文本但全是图片）→ 跳过，不生成空文件
            res['skipped'] += 1
            continue
        res['total'] += len(rows)

        if per_file:
            # —— 模式一：每个会话一个独立工作簿（里面只有一张表）——
            w = Workbook(write_only=True)
            s = w.create_sheet('聊天记录')
            _setup_sheet(s)
            _write_session(s, sess, rows, export_time)
            # 文件名：序号_名称_时间范围.xlsx（同名直接覆盖）
            path = os.path.join(out_dir, '%02d_%s_%s.xlsx' % (
                idx + 1, safe_name(sess.display, 40), suffix))
            _save_wb(w, path)
            res['files'].append(path)
        else:
            # —— 模式二：一个文件多个工作表，每个会话一张（无汇总页）——
            if wb is None:
                wb = Workbook(write_only=True)
            # 表名用会话名，超过 28 字截断，给 _2 之类的去重后缀留出余量
            sh = wb.create_sheet(unique_name(sheet_names, safe_name(sess.display, 28)))
            _setup_sheet(sh)
            _write_session(sh, sess, rows, export_time)
        res['ok'] += 1

        if progress:
            # cur>0 表示这个会话已写完，进度窗把「✓ 名称 N 条」打进日志
            progress(idx + 1, n, sess.display, len(rows), len(rows))

    # per_sheet 模式：所有会话都写完后一次性落盘；被取消则不生成（避免残缺文件）
    if not per_file and wb is not None and res['ok'] and not res['cancelled']:
        fname = '聊天记录_%s.xlsx' % suffix
        _save_wb(wb, os.path.join(out_dir, fname))
        res['files'].append(os.path.join(out_dir, fname))

    res['elapsed'] = time.time() - t_start
    return res
