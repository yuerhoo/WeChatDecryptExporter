# -*- coding: utf-8 -*-
"""主窗口

界面自上而下四层：
    菜单栏   设置 / 关于（左对齐的两个无边框按钮）
    操作行   搜索框 + 刷新 + 导出 Excel
    四个 Tab 联系人 / 群聊 / 公众号 / 系统，Tab 行右侧是"仅显示有聊天记录 / 已选 N / 清空选择"
    列表     每个 Tab 一个 QTableView，勾选后点「导出 Excel」
数据为空时整块换成引导页（去设置）。

两条后台链路：
    Loader        启动/刷新时加载联系人、群昵称、会话时间（避免界面卡住）
    CountsWorker  逐个会话统计消息数（几万个会话，只能后台慢慢补）
另外启动时会静默跑一次增量解密（_auto_sync），把新消息同步进来。
"""
import json
import os
import re

from PyQt5.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication, QKeySequence
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                             QMessageBox, QProgressBar, QPushButton, QShortcut,
                             QSizePolicy, QStackedWidget, QTabWidget, QTableView,
                             QVBoxLayout, QWidget)

from core import accounts, contacts, messages, runner
from core.contacts import CAT_NAMES, CATEGORIES
from app_config import APP_NAME, Prefs
from . import icons
from .delegates import CheckDelegate
from .table_model import SessionTableModel
from .settings_dialog import SettingsDialog
from .export_dialog import ExportDialog
from .export_runner import ExportProgressDialog

# 窗口标题统一取 app_config.APP_NAME，和导出表格里的「导出工具」保持同一个值
APP_TITLE = APP_NAME

# 主区域三个页面（QStackedWidget 的下标，别写魔术数字）
PAGE_LIST, PAGE_EMPTY, PAGE_LOADING = 0, 1, 2

# 状态栏右下角的常驻提示（加载中 / 空数据时会临时改掉，填充完再还原）
DEFAULT_HINT = '勾选会话后点「导出 Excel」；双击任意一行可直接导出该会话（Ctrl+F 搜索）'


def _msg_sig(dec_dir):
    """消息库签名：变化最晚的那个消息库的 (mtime, 文件名)

    用来给消息数缓存（counts.json）做过期判断：签名变了说明有新消息，
    缓存的条数不再可信，需要重新统计。
    """
    try:
        d = os.path.join(dec_dir, 'message')
        return max((os.path.getmtime(os.path.join(d, f)), f)
                   for f in os.listdir(d) if f.endswith('.db'))
    except Exception:
        return None


class Loader(QThread):
    """后台加载联系人数据（4 万多个联系人 + 群昵称解析，约 1 秒）"""

    done = pyqtSignal(object)      # 传 contacts.Data；失败时传 None

    def __init__(self, dec_dir, counts_path):
        """dec_dir 解密库根目录；counts_path 消息数缓存文件（可不存在）"""
        super().__init__()
        self.dec_dir = dec_dir
        self.counts_path = counts_path
        self.err = ''

    def run(self):
        """线程体：load_data 是纯 SQLite 读，不碰任何界面对象

        失败不抛异常，而是把错误文本放进 self.err 并 emit(None)，
        由主线程决定是弹窗还是显示空状态页。
        """
        try:
            data = contacts.load_data(self.dec_dir)
            self._restore_counts(data)
        except Exception as e:
            self.err = str(e)
            self.done.emit(None)
            return
        self.done.emit(data)

    def _restore_counts(self, data):
        """把上次缓存的"会话消息数"贴回去，省掉重新统计的时间"""
        try:
            with open(self.counts_path, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            return
        if cache.get('sig') != _msg_sig(self.dec_dir):
            return                     # 库变过，缓存作废
        for u, cnt in (cache.get('counts') or {}).items():
            s = data.by_user.get(u)
            if s is not None:
                s.count = cnt


class CountsWorker(QThread):
    """后台统计消息数：一个会话一个信号，统计完立即刷新那一行"""

    one = pyqtSignal(str, int)         # username, 条数
    finishedAll = pyqtSignal()

    def __init__(self, dec_dir, sessions):
        """sessions 传全量进来，这里自己筛出"还没统计过 + 有聊天记录"的"""
        super().__init__()
        self.dec_dir = dec_dir
        # 只统计"还没统计过且确实有聊天记录"的会话
        self.sessions = [s for s in sessions if s.count is None and s.last_ts]
        self._stop = False

    def stop(self):
        """请求停止：不强制 terminate（会泄漏 sqlite 句柄），等当前会话统计完自然退出"""
        self._stop = True

    def run(self):
        """线程体：逐会话 COUNT(*)，每算完一个就发一条信号让对应行立刻刷新"""
        for s in self.sessions:
            if self._stop:             # 切 Tab / 关窗时尽快退出
                break
            try:
                n = messages.count_msgs(self.dec_dir, s.username)
            except Exception:
                continue
            s.count = n
            self.one.emit(s.username, n)
        self.finishedAll.emit()


class ClickLabel(QLabel):
    """可点击的标签（"已选 N"，点一下弹窗看清单）"""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, e):
        """用 release 而不是 press：鼠标按下后拖走再松手不应该触发"""
        self.clicked.emit()
        super().mouseReleaseEvent(e)


class MainWindow(QMainWindow):
    """主窗口：四个 Tab 的会话列表 + 顶栏账号徽章 + 底部状态栏

    数据流：Loader/CountsWorker 在后台把数据准备好 → 信号回主线程 →
    填充 table_model → QTableView 显示。主线程永远不做耗时 IO。
    """

    def __init__(self):
        """界面全部代码构建（不用 .ui 文件），顺序：顶栏 → 操作行 → Tab 列表 → 状态栏

        构造函数里不做任何数据库读取，只决定"现在该显示哪一页"，
        真正的加载在 showEvent/refresh 里发起后台线程。
        """
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1080, 700)
        self.prefs = Prefs()
        self.data = None               # contacts.Data，加载完成前为 None
        self.models = {}               # category -> SessionTableModel
        self.views = {}                # category -> QTableView
        self._loader = None
        self._counts = None
        self.acc = None                # 当前账号 dict
        self._restore_geom()

        # 启动时的后台增量同步（只在新消息存在且密钥有效时才跑）
        self._sync_proc = None
        self._sync_pos = 0
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._poll_sync)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---------- 顶栏：应用名 + 当前账号徽章 + 设置 / 关于 ----------
        topbar = QWidget()
        topbar.setObjectName('topbar')
        th = QHBoxLayout(topbar)
        th.setContentsMargins(18, 12, 14, 12)
        th.setSpacing(10)
        lb_app = QLabel(APP_TITLE)
        lb_app.setObjectName('h1')
        th.addWidget(lb_app)
        self.lb_acc = QLabel('')                 # 当前账号徽章（跟着账号切换变化）
        self.lb_acc.setObjectName('badge')
        th.addWidget(self.lb_acc)
        th.addStretch(1)
        b_set = QPushButton('设置')
        b_set.setObjectName('menubtn')
        b_set.setIcon(icons.icon('settings', '#4B5563'))
        b_set.setIconSize(QSize(16, 16))
        b_set.setCursor(Qt.PointingHandCursor)
        b_set.setToolTip('选择微信数据目录、切换微信号、提取密钥并解密（Ctrl+,）')
        b_set.clicked.connect(self._open_settings)
        th.addWidget(b_set)
        b_about = QPushButton('关于')
        b_about.setObjectName('menubtn')
        b_about.setIcon(icons.icon('info', '#4B5563'))
        b_about.setIconSize(QSize(16, 16))
        b_about.setCursor(Qt.PointingHandCursor)
        b_about.setToolTip('版本信息与数据安全说明')
        b_about.clicked.connect(self._show_about)
        th.addWidget(b_about)
        v.addWidget(topbar)

        # ---------- 操作行：搜索 / 刷新 / 导出 ----------
        act = QWidget()
        act.setObjectName('toolbar')
        ah = QHBoxLayout(act)
        ah.setContentsMargins(18, 10, 18, 10)
        ah.setSpacing(10)
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName('search')
        self.ed_search.setPlaceholderText('搜索群名、备注、微信号或拼音首字母…')
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMinimumHeight(34)
        # 放大镜放在输入框内部左侧（addAction 的图标位置由 Qt 决定，不需要额外布局）
        self.ed_search.addAction(icons.icon('search', '#98A2B0'),
                                 QLineEdit.LeadingPosition)
        self.ed_search.setToolTip('即时过滤当前分类；支持拼音首字母，例如输入 hh 可匹配「虎虎」\n'
                                  '快捷键：Ctrl+F')
        self.ed_search.textChanged.connect(self._on_search)
        ah.addWidget(self.ed_search, 1)
        b_ref = QPushButton('刷新')
        b_ref.setIcon(icons.icon('refresh', '#4B5563'))
        b_ref.setIconSize(QSize(16, 16))
        b_ref.setMinimumHeight(34)
        b_ref.setToolTip('重新读取已解密的数据库（有新消息时请先到「设置」里增量同步）\n'
                         '快捷键：F5')
        b_ref.clicked.connect(self.reload)
        ah.addWidget(b_ref)
        self.b_export = QPushButton('导出 Excel')
        self.b_export.setObjectName('primary')
        self.b_export.setIcon(icons.icon('export', '#FFFFFF'))
        self.b_export.setIconSize(QSize(16, 16))
        self.b_export.setMinimumHeight(34)
        self.b_export.setCursor(Qt.PointingHandCursor)
        self.b_export.setToolTip('导出勾选的会话到「微信聊天记录导出目录」\n'
                                 '小技巧：双击任意一行可直接导出那一个会话')
        self.b_export.clicked.connect(self._open_export)
        ah.addWidget(self.b_export)
        v.addWidget(act)

        # ---------- 主区：有数据(四个 Tab) / 无数据(空状态页) ----------
        self.main_stack = QStackedWidget()

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(lambda _i: self._refill())
        for cat in CATEGORIES:
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(0, 0, 0, 0)
            view = QTableView()
            view.setSelectionBehavior(QTableView.SelectRows)      # 整行选中
            view.setSelectionMode(QTableView.ExtendedSelection)   # 支持多选
            view.setEditTriggers(QTableView.NoEditTriggers)       # 只读
            view.setShowGrid(False)
            view.setAlternatingRowColors(True)
            view.setMouseTracking(True)                           # 勾选框需要 hover 反馈
            view.setWordWrap(False)
            view.verticalHeader().setVisible(False)
            view.verticalHeader().setDefaultSectionSize(36)       # 行高（勾选框留出点击余量）
            model = SessionTableModel(self)
            model.checkedChanged.connect(self._sel_changed)
            # 顺序很重要：先 setModel 再配表头，否则表头没有 model 会崩
            view.setModel(model)
            # 第 0 列改用自绘勾选框（系统默认的太小、且 QSS 管不到）
            view.setItemDelegateForColumn(0, CheckDelegate(view))
            hh = view.horizontalHeader()
            hh.setHighlightSections(False)
            # 列宽策略（用户约定）：微信号/消息数/最近消息 固定，
            # 只有名称列随窗口伸缩 —— 拉伸/缩小窗口时只有名称列变化
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(1, hh.Stretch)                # 名称列 = 唯一的动态列
            hh.resizeSection(0, 40)                               # 勾选列
            hh.resizeSection(2, 200)                              # 微信号 / 群 ID（固定）
            hh.resizeSection(3, 80)                               # 消息数（固定）
            hh.resizeSection(4, 120)                              # 最近消息（固定）
            # 点勾选列表头 = 全选/取消全选当前列表（表头本身没有文字，靠 tooltip 说明）
            hh.sectionClicked.connect(
                lambda sec, m=model: m.toggle_all() if sec == 0 else None)
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, v=view: self._menu(v, pos))
            view.doubleClicked.connect(lambda idx, v=view: self._dbl(v, idx))
            self.models[cat] = model
            self.views[cat] = view
            pv.addWidget(view)
            self.tabs.addTab(page, CAT_NAMES[cat])
        self.main_stack.addWidget(self.tabs)

        # ---------- 空状态页：一张居中卡片 + 三步引导 ----------
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(40, 28, 40, 36)
        ev.addStretch(1)
        card = QFrame()
        card.setObjectName('card')
        card.setMaximumWidth(560)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(38, 32, 38, 30)
        cv.setSpacing(10)

        ic = QLabel()
        ic.setPixmap(icons.pixmap('chat', '#AFC2D8', 76))
        ic.setAlignment(Qt.AlignCenter)
        cv.addWidget(ic)

        lb1 = QLabel('还没有可用的微信数据')
        lb1.setObjectName('h1')
        lb1.setAlignment(Qt.AlignCenter)
        cv.addWidget(lb1)
        lb2 = QLabel('跟着三步走，就能把指定会话导出成 Excel')
        lb2.setObjectName('sub')
        lb2.setAlignment(Qt.AlignCenter)
        cv.addWidget(lb2)
        cv.addSpacing(12)

        # 三步说明：左边圆形序号，右边标题 + 一行解释
        steps = QVBoxLayout()
        steps.setSpacing(13)
        for no, title, desc in (
                ('1', '选择微信数据目录', '点「浏览…」选到 xwechat_files 这一级，程序会自动找出里面的微信号'),
                ('2', '挑一个微信号', '在「当前账号」下拉里选择要导出的号；每个号的数据各自独立存放'),
                ('3', '提取密钥并解密', '首次会弹一次管理员授权；之后有新消息只做增量同步，很快')):
            row = QHBoxLayout()
            row.setSpacing(11)
            n = QLabel(no)
            n.setObjectName('stepNo')
            n.setAlignment(Qt.AlignCenter)
            row.addWidget(n, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(title)
            t.setObjectName('stepTitle')
            d = QLabel(desc)
            d.setObjectName('stepDesc')
            d.setWordWrap(True)
            col.addWidget(t)
            col.addWidget(d)
            row.addLayout(col, 1)
            steps.addLayout(row)
        cv.addLayout(steps)

        cv.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        bgo = QPushButton('去设置')
        bgo.setObjectName('primary')
        bgo.setIcon(icons.icon('settings', '#FFFFFF'))
        bgo.setIconSize(QSize(16, 16))
        bgo.setCursor(Qt.PointingHandCursor)
        bgo.setToolTip('打开设置：选择微信数据目录 → 选账号 → 提取密钥并解密')
        bgo.clicked.connect(self._open_settings)
        row.addWidget(bgo)
        row.addStretch(1)
        cv.addLayout(row)

        ev.addWidget(card, 0, Qt.AlignHCenter)
        ev.addStretch(1)
        self.main_stack.addWidget(empty)

        # ---------- 加载页：读解密库时给个明确的"正在干活"反馈 ----------
        loading = QWidget()
        lv = QVBoxLayout(loading)
        lv.setContentsMargins(40, 40, 40, 40)
        lv.addStretch(1)
        lt = QLabel('正在读取解密数据…')
        lt.setObjectName('h2')
        lt.setAlignment(Qt.AlignCenter)
        lv.addWidget(lt)
        ls = QLabel('会话多的时候需要几秒，请稍候')
        ls.setObjectName('sub')
        ls.setAlignment(Qt.AlignCenter)
        lv.addWidget(ls)
        lv.addSpacing(10)
        brow = QHBoxLayout()
        brow.addStretch(1)
        self.bar_load = QProgressBar()
        self.bar_load.setRange(0, 0)          # 不确定进度：滚动条动画
        self.bar_load.setFixedWidth(240)
        self.bar_load.setTextVisible(False)
        brow.addWidget(self.bar_load)
        brow.addStretch(1)
        lv.addLayout(brow)
        lv.addStretch(1)
        self.main_stack.addWidget(loading)

        v.addWidget(self.main_stack, 1)

        # Tab 行右侧的控件（仅显示有聊天记录 / 已选 N / 全选 / 清空选择）
        # 注意：默认 QWidget 的尺寸策略会横向扩张、把 QTabBar 挤窄，
        #       导致 Tab 上的数字被截掉 —— 必须限制为 Maximum（最多占 sizeHint 宽）
        corner = QWidget()
        corner.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        ch = QHBoxLayout(corner)
        ch.setContentsMargins(8, 0, 12, 0)
        ch.setSpacing(6)
        self.chk_chat = QCheckBox('仅显示有聊天记录')
        self.chk_chat.setToolTip('取消勾选可看到全部联系人与群，包括从未聊过的')
        self.chk_chat.setChecked(self.prefs.get('only_with_chat', True))
        self.chk_chat.stateChanged.connect(lambda _s: self._refill())
        ch.addWidget(self.chk_chat)
        ch.addSpacing(4)
        self.lb_sel = ClickLabel('已选 0')
        self.lb_sel.setObjectName('badge')
        self.lb_sel.setCursor(Qt.PointingHandCursor)
        self.lb_sel.setToolTip('点击查看已勾选的会话清单')
        self.lb_sel.clicked.connect(self._show_selected)
        ch.addWidget(self.lb_sel)
        self.b_check_all = QPushButton('全选')
        self.b_check_all.setObjectName('quiet')
        self.b_check_all.setCursor(Qt.PointingHandCursor)
        self.b_check_all.setToolTip('勾选当前列表里所有会话（只影响搜索过滤后看到的行）\n'
                                    '也可以直接点列表最左侧的表头')
        self.b_check_all.clicked.connect(self._check_all)
        ch.addWidget(self.b_check_all)
        self.b_clear = QPushButton('清空选择')
        self.b_clear.setObjectName('quiet')
        self.b_clear.setCursor(Qt.PointingHandCursor)
        self.b_clear.setToolTip('取消勾选所有会话（不影响已经导出的文件）')
        self.b_clear.clicked.connect(self._clear_sel)
        ch.addWidget(self.b_clear)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)

        # ---------- 快捷键：Ctrl+F 定位搜索、F5 刷新、Ctrl+, 设置 ----------
        for keys, slot in ((QKeySequence('Ctrl+F'), self._focus_search),
                           (QKeySequence('F5'), self.reload),
                           (QKeySequence('Ctrl+,'), self._open_settings)):
            sc = QShortcut(keys, self)
            sc.activated.connect(slot)

        # ---------- 底部状态栏：常驻信息 + 操作提示 ----------
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.lb_status = QLabel('')
        self.lb_status.setObjectName('tip')
        sb.addWidget(self.lb_status)
        self.lb_hint = QLabel(DEFAULT_HINT)
        self.lb_hint.setObjectName('tip')
        sb.addPermanentWidget(self.lb_hint)

        self.reload()
        self._sel_changed(0)

    # ---------- 启动/刷新 ----------
    def _dec_dir(self):
        """当前账号的解密库目录（没账号时返回空串）"""
        return (self.acc or {}).get('dec_dir') or ''

    def reload(self):
        """重新解析账号并异步加载数据（设置改动、刷新按钮、同步完成后都会调用）"""
        self.acc = accounts.get_current()
        if self.acc and self.acc.get('name'):
            self.setWindowTitle('%s · %s' % (APP_TITLE, self.acc['name']))
            self.lb_acc.setText('当前账号：%s' % self.acc['name'])
            self.lb_acc.setVisible(True)
        elif self.acc:
            self.setWindowTitle(APP_TITLE)
            self.lb_acc.setText('当前账号：%s' % self.acc['id'])
            self.lb_acc.setVisible(True)
        else:
            self.setWindowTitle(APP_TITLE)
            self.lb_acc.setVisible(False)
        self.counts_path = accounts.counts_file(self.acc) if self.acc else ''
        dec = self._dec_dir()
        # 用 contact.db 是否存在判断"这个账号是否已经解密过"
        ok = bool(dec) and os.path.exists(os.path.join(dec, 'contact', 'contact.db'))
        if not ok:
            self.data = None
            self.main_stack.setCurrentIndex(PAGE_EMPTY)
            self.lb_status.setText('账号：%s　·　还没有解密数据'
                                   % ((self.acc or {}).get('name') or '未选择'))
            self.lb_hint.setText('到「设置」里点「提取密钥并解密」，列表就会出现在这里')
            return
        if self._loader is not None and self._loader.isRunning():
            return                                   # 上一轮还没加载完，别重复开线程
        self.b_export.setEnabled(False)
        self.main_stack.setCurrentIndex(PAGE_LOADING)
        self.lb_status.setText('正在加载数据…')
        self.lb_hint.setText('首次读取几万个会话需要几秒，之后会走缓存')
        self._loader = Loader(dec, self.counts_path)
        self._loader.done.connect(self._loaded)
        self._loader.start()

    def _loaded(self, data):
        """Loader 回调（主线程）：data 为 None 表示加载失败"""
        self.b_export.setEnabled(True)
        if data is None:
            err = self._loader.err or '未知错误'
            self.lb_status.setText('数据加载失败')
            self.main_stack.setCurrentIndex(PAGE_EMPTY)
            QMessageBox.warning(
                self, '加载失败',
                '读取解密数据时出错：\n%s\n\n'
                '常见原因：数据库还在解密中、文件被占用，或该账号的数据已损坏。\n'
                '可以到「设置」里点「提取密钥并解密」重新同步一次。' % err)
            return
        self.data = data
        self._refill()
        self._auto_sync()

    def _cat(self):
        """当前 Tab 对应的分类 key"""
        return CATEGORIES[self.tabs.currentIndex()]

    def _refill(self):
        """按当前 Tab + 搜索词 + "仅显示有聊天记录" 重新灌数据到表格"""
        if self.data is None:
            self.main_stack.setCurrentIndex(PAGE_EMPTY)
            return
        self.main_stack.setCurrentIndex(PAGE_LIST)
        # Tab 标题带上数量。
        # 文本末尾故意多留一个空格：Qt 计算 Tab 宽度 = 文本度量宽 + 内边距，分毫不差；
        # 高分屏缩放（125%/150%）下字形实际渲染会比度量宽出 1~2 设备像素，
        # 没有这个空格缓冲，最后一位数字会被切掉半个（真机 125% 缩放实测出现过）
        for i, c in enumerate(CATEGORIES):
            sess = self.data.of(c)
            if self.chk_chat.isChecked():
                sess = [s for s in sess if s.last_ts]
            self.tabs.setTabText(i, '%s %s ' % (CAT_NAMES[c], format(len(sess), ',')))
        cat = self._cat()
        sess = self.data.of(cat)
        if self.chk_chat.isChecked():
            sess = [s for s in sess if s.last_ts]
        sess.sort(key=lambda s: s.last_ts or 0, reverse=True)   # 最近聊的排前面
        self.models[cat].set_sessions(sess)
        self.models[cat].set_filter(self.ed_search.text())
        self.b_check_all.setEnabled(bool(sess))
        self.lb_hint.setText(DEFAULT_HINT)
        self._update_status()
        self._start_counts()

    def _update_status(self):
        """刷新底部状态栏：账号 · 显示数/总数 · 已选数（搜索过滤后能一眼看出差别）"""
        if self.data is None:
            return
        cat = self._cat()
        m = self.models.get(cat)
        total = len(m._all) if m is not None else 0
        shown = m.rowCount() if m is not None else 0
        who = (self.acc or {}).get('name') or (self.acc or {}).get('id') or '未选择账号'
        if shown == 0 and self.ed_search.text().strip():
            # 搜不到东西时，说清楚是"没匹配"而不是"没有数据"
            self.lb_status.setText(
                '%s　·　%s 没有匹配「%s」的会话（共 %s 个）'
                % (who, CAT_NAMES.get(cat, cat), self.ed_search.text().strip(),
                   format(total, ',')))
            return
        self.lb_status.setText(
            '%s　·　%s 显示 %s / 共 %s　·　已选 %d 个'
            % (who, CAT_NAMES.get(cat, cat), format(shown, ','),
               format(total, ','), len(self._checked())))

    def _start_counts(self):
        """启动（或重启）消息数统计线程"""
        if self._counts is not None:
            self._counts.stop()
            self._counts.wait(2000)     # 等上一个线程收尾，避免两个线程写同一批对象
        model = self.models[self._cat()]
        self._counts = CountsWorker(self._dec_dir(), model._all)
        self._counts.one.connect(model.update_count)
        self._counts.finishedAll.connect(self._save_counts)
        self._counts.start()

    def _save_counts(self):
        """统计结束后把消息数写进缓存（带消息库签名，库变了缓存自动作废）"""
        if self.data is None or not self.counts_path:
            return
        try:
            os.makedirs(os.path.dirname(self.counts_path), exist_ok=True)
            with open(self.counts_path, 'w', encoding='utf-8') as f:
                json.dump({'sig': _msg_sig(self._dec_dir()),
                           'counts': {u: s.count for u, s in self.data.by_user.items()
                                      if s.count is not None}}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- 启动时后台快速同步（密钥有效时自动增量解密新消息） ----------
    def _auto_sync(self):
        """静默跑一次增量解密：密钥有效才做，失败也不打扰用户"""
        if self._sync_proc is not None and self._sync_proc.poll() is None:
            return                      # 已有同步在跑
        acc = self.acc
        if not acc or not acc.get('id'):
            return
        db, dec = acc.get('db_dir', ''), self._dec_dir()
        if not db or not dec or not os.path.isdir(db):
            return
        keys = accounts.keys_file(acc['id'])
        try:
            valid, stale, _ = contacts.key_status(db, keys)
        except Exception:
            return
        if valid == 0 or stale > 0:     # 密钥没提取过或已失效 → 静默跳过，等用户走设置
            return
        try:
            self._sync_proc, _f = runner.run_plain(
                runner.BOOT_DECRYPT,
                [db, dec, keys, accounts.manifest_file(acc['id'])],
                accounts.sync_log(acc['id']))
        except Exception:
            return
        self._sync_pos = 0
        self._sync_timer.start(800)

    def _poll_sync(self):
        """轮询同步日志；真的解出了新库才 reload（否则每次启动都白刷一遍列表）"""
        log = accounts.sync_log(self.acc['id']) if self.acc else ''
        lines, self._sync_pos, rc = runner.read_new_lines(log, self._sync_pos)
        if rc is None:
            return
        self._sync_timer.stop()
        text = '\n'.join(lines)
        m = re.search(r'完成: (\d+) 解密', text)
        # 有库被重新解密（同步到了新消息）→ 重载数据刷新列表
        if rc == 0 and m and int(m.group(1)) > 0:
            self.reload()

    # ---------- 搜索/选择 ----------
    def _on_search(self, text):
        """搜索框实时过滤当前 Tab（只过滤显示，不改勾选状态）"""
        self.models[self._cat()].set_filter(text)

    def _focus_search(self):
        """Ctrl+F：把光标送进搜索框并选中已有内容（直接输入即可覆盖）"""
        self.ed_search.setFocus()
        self.ed_search.selectAll()

    def _check_all(self):
        """「全选」按钮：勾上当前列表里所有会话"""
        self.models[self._cat()].toggle_all(True)

    def _sel_changed(self, n=0):
        """勾选数量变化 → 更新"已选 N"徽章、清空按钮与底部状态栏

        这里统计的是四个 Tab 的合计：跨分类勾选时，用户期望的是"一共选了多少"，
        而不是"当前 Tab 选了多少"。
        """
        try:
            total = len(self._checked())
        except Exception:
            total = n
        self.lb_sel.setText('已选 %d' % total)
        # 有勾选时徽章换成主色，一眼能看出"当前有选择"
        self.lb_sel.setObjectName('badgeActive' if total else 'badge')
        self.lb_sel.style().unpolish(self.lb_sel)
        self.lb_sel.style().polish(self.lb_sel)
        self.b_clear.setEnabled(total > 0)
        if self.data is not None:
            self._update_status()

    def _checked(self):
        """四个 Tab 里所有已勾选的会话（跨 Tab 导出）"""
        return [s for m in self.models.values() for s in m.checked_sessions()]

    def _clear_sel(self):
        """清空四个 Tab 的全部勾选（不只是当前 Tab），并刷新"已选 N"徽章"""
        for m in self.models.values():
            m.clear_checked()

    def _show_selected(self):
        """点"已选 N"：弹窗列出已勾选的会话（最多显示 30 个）"""
        sel = self._checked()
        if not sel:
            QMessageBox.information(
                self, '已选会话',
                '当前没有勾选任何会话。\n\n'
                '在列表最左侧勾选就能加入导出清单；勾选数量会显示在标签页右上角。')
            return
        lines = ['%d. %s  （%s）' % (i, s.display, CAT_NAMES.get(s.category, s.category))
                 for i, s in enumerate(sel[:30], 1)]
        more = '\n… 还有 %d 个未列出' % (len(sel) - 30) if len(sel) > 30 else ''
        QMessageBox.information(
            self, '已选 %d 个会话' % len(sel),
            '\n'.join(lines) + more + '\n\n如需调整，直接回列表取消/重新勾选即可。')

    # ---------- 右键/双击 ----------
    def _menu(self, view, pos):
        """右键菜单：只导出此会话 / 复制会话 ID"""
        idx = view.indexAt(pos)
        if not idx.isValid():
            return
        view.selectRow(idx.row())
        s = self.models[self._cat()].session_at(idx.row())
        m = QMenu(self)
        a1 = QAction('只导出此会话', self)
        a1.triggered.connect(lambda _c, ss=s: self._do_export([ss]))
        a2 = QAction('复制会话 ID', self)
        a2.triggered.connect(lambda _c, ss=s: QGuiApplication.clipboard().setText(ss.username))
        m.addAction(a1)
        m.addAction(a2)
        m.exec_(view.viewport().mapToGlobal(pos))

    def _dbl(self, view, idx):
        """双击非勾选列 = 导出这个会话"""
        if idx.column() != 0:
            s = self.models[self._cat()].session_at(idx.row())
            self._do_export([s])

    # ---------- 弹窗 ----------
    def _show_about(self):
        """关于：版本、能力边界与数据安全说明"""
        from PyQt5.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle('关于')
        box.setText('%s\n版本 1.0.0' % APP_TITLE)
        box.setInformativeText(
            '从本机微信客户端解密聊天数据库，按会话与时间范围导出为 Excel。\n\n'
            '· 支持多个微信号：每个账号的密钥与解密数据各自独立\n'
            '· 支持微信同时登录多个号：自动定位到当前账号所在的微信进程\n'
            '· 增量同步：只解密有变化的库，日常几十秒完成\n'
            '· 导出集中保存到「微信聊天记录导出目录」，同名文件自动覆盖\n\n'
            '所有数据只在本机处理，程序不联网、不上传。\n\n'
            'Python 3.12 · PyQt5 5.15 · openpyxl\n'
            '解密引擎：SQLCipher v3 / v4')
        box.setIcon(QMessageBox.Information)
        box.addButton('关闭', QMessageBox.YesRole)
        box.exec_()

    def _open_settings(self):
        """打开设置窗（模态）。里面的保存/重新检查/解密都会触发 dataChanged → reload"""
        dlg = SettingsDialog(self.prefs, self)
        dlg.dataChanged.connect(self.reload)     # 账号/目录/解密结果变化 → 重载
        dlg.exec_()

    def _open_export(self):
        """顶部「导出 Excel」：必须有勾选"""
        sel = self._checked()
        if not sel:
            QMessageBox.information(
                self, '还没有选择会话',
                '请先在列表里勾选要导出的会话，再点「导出 Excel」。\n\n'
                '小技巧：双击任意一行可以直接导出那一个会话；'
                '右键某一行还能「只导出此会话」。')
            return
        self._do_export(sel)

    def _do_export(self, sessions):
        """先弹选项窗，确认后再开进度窗（导出目录固定，无需再选路径）"""
        if self.data is None:
            return
        dlg = ExportDialog(sessions, self.data, self._dec_dir(), self.prefs, self)
        self._pending = sessions
        dlg.startExport.connect(self._run_export)
        dlg.exec_()

    def _run_export(self, opts):
        """选项确认后的实际执行：进度窗内部起线程跑导出"""
        ExportProgressDialog(self._pending, opts, self.data,
                             self._dec_dir(), self).exec_()

    # ---------- 窗口状态记忆 ----------
    def _restore_geom(self):
        """恢复上次的窗口位置大小；没有记录就居中偏左上、占屏幕 3/4"""
        g = self.prefs.get('win/geometry', None)
        if g:
            try:
                self.restoreGeometry(bytes.fromhex(str(g)))
                return
            except Exception:
                pass
        qr = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(qr.width() // 8, qr.height() // 8,
                         qr.width() * 3 // 4, qr.height() * 3 // 4)

    def closeEvent(self, e):
        """关窗前：停后台线程、存消息数缓存、记住窗口状态与勾选项"""
        if self._counts is not None:
            self._counts.stop()
        self._save_counts()
        self._sync_timer.stop()
        self.prefs.set('win/geometry', bytes(self.saveGeometry()).hex())
        self.prefs.set('only_with_chat', self.chk_chat.isChecked())
        super().closeEvent(e)
