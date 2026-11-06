# -*- coding: utf-8 -*-
"""设置弹窗：数据路径 → 微信账号（下拉）→ 密钥与解密 → 运行日志

界面结构（自上而下）：
    ┌ 设置 / 一句话说明 ────────────────────────────────┐
    │ 数据路径        微信数据目录 + 浏览 / 重新扫描            │
    │ 微信账号        当前账号下拉 + 登录状态徽章               │
    │                 账号名称 + 数据目录说明                  │
    │                 [删除此账号数据]        [保存]           │  ← 保存按钮就在账号卡片里
    │ 密钥与解密      密钥健康度 + 检查时间 + 待同步提示          │
    │                 [提取密钥并解密] [重新检查] + 进度条        │
    │ 运行日志        子进程输出（提权提取 / 解密进度）            │
    └ 底部：左侧说明文字                      [关闭] ────────┘

──────────────────────────────────────────────────────────────────────
「保存」按钮到底保存了什么？（只有这三样，且**不会关闭窗口**）
    1) 微信数据目录     写 QSettings 的 wechat/root
                        （保存前会 normalize_root，统一归到 xwechat_files 这一级）
    2) 每个账号的名称   写各自账号目录下的 account.json（name 字段）
                        ——通过 accounts.save_accounts() → ensure() 落盘；
                        顺带给列表里每个微信号建出目录骨架（account.json + db/）
    3) 当前选中的账号   写 QSettings 的 account/current（存 wxid 文件夹名）

不归「保存」管、会立即生效的动作：
    · 下拉切换账号        立刻 ensure() 建出该账号的数据目录（不用点保存）
    · 删除此账号数据      立刻删除 WeChatData\\accounts\\<wxid> 整个目录（不可撤销）
    · 提取密钥并解密      立刻往该账号目录写 keys.json / manifest.json / db/

也不归「保存」管的其他配置（各有各的时机）：
    · 导出选项（时间范围/消息类型/输出方式）  在导出弹窗点「开始导出」时存
    · 窗口大小位置、仅显示有聊天记录           在主窗口关闭时存
    · 解密后的数据放哪                        固定为 WeChatData\\accounts\\<wxid>\\db，不可改

「关闭」= 直接关窗，不写任何配置（但上面那些"选中即生效"的动作不会回滚）。

解密/提取进行中不允许关窗：见 _guard_close()，会拦下 closeEvent / Esc / 关闭按钮。
──────────────────────────────────────────────────────────────────────
"""
import json
import os
import time

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout)

from core import accounts, contacts, detect, runner
from app_config import ACCOUNTS_ROOT
from . import icons


class SettingsDialog(QDialog):
    """设置窗：管理微信账号、密钥提取、解密与数据目录

    三块内容（自上而下）：微信账号（下拉 + 名称 + 保存/删除）、
    密钥与解密（检查状态 / 重新检查 / 提取密钥并解密 + 日志）、数据目录。

    生命周期里有两个必须守住的规则：
      1) 提取密钥 / 解密进行中不允许关窗（子进程还在跑，关掉会留下半截数据）；
      2) 「保存」只写数据不关窗，是否通知主窗口重载由快照比对决定。
    """

    dataChanged = pyqtSignal()          # 账号/密钥/解密结果变化后通知主窗口重载列表

    def __init__(self, prefs, parent=None):
        """先把账号与快照读进来，再建界面，最后延迟检查密钥状态

        顺序不能换：_build_ui/_fill_fields 都要用 self.accs / self.cur。
        """
        super().__init__(parent)
        self.prefs = prefs
        self.setWindowTitle('设置')
        self.setModal(True)
        self.setMinimumSize(760, 730)

        self._pos = 0                   # 日志轮询位置（读到哪了）
        self._log_path = ''
        self._timer = QTimer(self)      # 轮询子进程日志的定时器
        self._timer.timeout.connect(self._poll_log)
        self._stage = None              # None / 'keys'（提权提取中） / 'decrypt'（解密中）
        self._checked_at = ''           # 上次「重新检查」的时间，给用户可见的反馈

        # 打开弹窗时的快照，用来判断点「保存」要不要通知主窗口重载
        self.accs, cur_id = accounts.load_accounts()
        self._orig_root = accounts.get_root()
        self._orig_cur = cur_id
        self._orig_names = {a['id']: a.get('name', '') for a in self.accs}
        self.cur = next((a for a in self.accs if a['id'] == cur_id), None)

        self._build_ui()
        self._fill_combo()
        self._fill_fields()
        # 延迟 60ms 检查密钥状态：等窗口先画出来，检测文件占用要读几十个文件
        QTimer.singleShot(60, self._recheck)

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _build_ui(self):
        """纯代码搭界面：标题区 → 账号卡片 → 密钥/解密卡片 → 数据目录卡片 → 底部按钮"""
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(6)

        # ---------- 标题区 ----------
        head = QVBoxLayout()
        head.setSpacing(2)
        lb_title = QLabel('设置')
        lb_title.setObjectName('h1')
        head.addWidget(lb_title)
        lb_sub = QLabel('配置微信数据目录与微信号。每个微信号的密钥、解密数据相互独立，互不影响。')
        lb_sub.setObjectName('sub')
        head.addWidget(lb_sub)
        v.addLayout(head)
        v.addSpacing(4)

        # ---------- 数据路径 ----------
        gb_path = QGroupBox('数据路径')
        gp = QGridLayout(gb_path)
        gp.setHorizontalSpacing(10)
        gp.setVerticalSpacing(8)
        gp.addWidget(QLabel('微信数据目录'), 0, 0)
        self.ed_root = QLineEdit(self._orig_root)
        self.ed_root.setPlaceholderText('例如 D:\\xwechat_files（微信：设置 → 文件管理 里的目录）')
        self.ed_root.setToolTip('选到 xwechat_files 这一级即可，程序自动拼接 <wxid>\\db_storage')
        self.ed_root.textChanged.connect(self._on_text_edited)
        self.ed_root.editingFinished.connect(self._rescan)     # 手输完回车也能重扫
        gp.addWidget(self.ed_root, 0, 1)
        self.b_browse = QPushButton('浏览…')
        self.b_browse.setIcon(icons.icon('folder', '#4B5563'))
        self.b_browse.setIconSize(QSize(16, 16))
        self.b_browse.setToolTip('选择微信的 xwechat_files 目录')
        self.b_browse.clicked.connect(self._pick_root)
        gp.addWidget(self.b_browse, 0, 2)
        self.b_rescan = QPushButton('重新扫描')
        self.b_rescan.setIcon(icons.icon('refresh', '#4B5563'))
        self.b_rescan.setIconSize(QSize(16, 16))
        self.b_rescan.setToolTip('按当前目录重新查找 wxid_ 开头的微信号文件夹')
        self.b_rescan.clicked.connect(self._rescan)
        gp.addWidget(self.b_rescan, 0, 3)
        self.lb_root = QLabel('')
        self.lb_root.setObjectName('tip')
        self.lb_root.setWordWrap(True)
        gp.addWidget(self.lb_root, 1, 0, 1, 4)
        gp.setColumnStretch(1, 1)
        v.addWidget(gb_path)

        # ---------- 微信账号（保存按钮放在这张卡片里） ----------
        gb_acc = QGroupBox('微信账号')
        ga = QGridLayout(gb_acc)
        ga.setHorizontalSpacing(10)
        ga.setVerticalSpacing(8)
        ga.addWidget(QLabel('当前账号'), 0, 0)
        self.cb_acc = QComboBox()
        self.cb_acc.setMinimumWidth(340)
        self.cb_acc.setToolTip('切换后立即就绪该账号的数据目录；每个微信号的数据互相独立')
        self.cb_acc.currentIndexChanged.connect(self._switch_account)
        ga.addWidget(self.cb_acc, 0, 1)
        self.lb_login = QLabel('')            # 已登录 / 未登录 徽章
        ga.addWidget(self.lb_login, 0, 2)
        ga.setColumnStretch(1, 1)

        ga.addWidget(QLabel('账号名称'), 1, 0)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText('留空则与 wxid 相同，例如：工作号')
        self.ed_name.setToolTip('仅用于界面显示和导出文件名，不影响微信本身')
        self.ed_name.textChanged.connect(self._on_name_edited)
        ga.addWidget(self.ed_name, 1, 1, 1, 2)

        self.lb_acc_dir = QLabel('')
        self.lb_acc_dir.setObjectName('tip')
        self.lb_acc_dir.setWordWrap(True)
        ga.addWidget(self.lb_acc_dir, 2, 0, 1, 3)

        # 底部操作行：左边危险操作 + 打开目录，右边「保存」
        row_save = QHBoxLayout()
        row_save.setSpacing(10)
        self.b_del = QPushButton('删除此账号数据')
        self.b_del.setObjectName('danger')
        self.b_del.setIcon(icons.icon('trash', '#C0392B'))
        self.b_del.setIconSize(QSize(16, 16))
        self.b_del.setToolTip('删除本程序为该账号生成的配置、密钥与解密库；微信原始数据不受影响')
        self.b_del.clicked.connect(self._del_account)
        row_save.addWidget(self.b_del)
        self.b_open_dir = QPushButton('打开数据目录')
        self.b_open_dir.setObjectName('quiet')
        self.b_open_dir.setIcon(icons.icon('folder', '#185FA5'))
        self.b_open_dir.setIconSize(QSize(16, 16))
        self.b_open_dir.setCursor(Qt.PointingHandCursor)
        self.b_open_dir.setToolTip('在资源管理器里打开该账号的解密数据目录')
        self.b_open_dir.clicked.connect(self._open_acc_dir)
        row_save.addWidget(self.b_open_dir)
        row_save.addStretch(1)
        self.lb_saved = QLabel('')            # "✓ 已保存 13:56:20" 的成功反馈
        self.lb_saved.setObjectName('tipGood')
        row_save.addWidget(self.lb_saved)
        self.b_save = QPushButton('保存')
        self.b_save.setObjectName('primary')
        self.b_save.setIcon(icons.icon('ok', '#FFFFFF'))
        self.b_save.setIconSize(QSize(16, 16))
        self.b_save.setCursor(Qt.PointingHandCursor)
        self.b_save.setToolTip('保存微信数据目录、账号名称与当前账号（保存后窗口不会关闭）')
        self.b_save.clicked.connect(self._save)
        row_save.addWidget(self.b_save)
        ga.addLayout(row_save, 3, 0, 1, 3)
        v.addWidget(gb_acc)

        # ---------- 密钥与解密 ----------
        gb_key = QGroupBox('密钥与解密')
        gv = QVBoxLayout(gb_key)
        gv.setSpacing(8)
        self.lb_status = QLabel('尚未添加账号')
        self.lb_status.setWordWrap(True)
        gv.addWidget(self.lb_status)
        self.lb_missing = QLabel('')
        self.lb_missing.setObjectName('tip')
        self.lb_missing.setWordWrap(True)
        gv.addWidget(self.lb_missing)

        row_run = QHBoxLayout()
        row_run.setSpacing(10)
        self.btn_run = QPushButton('提取密钥并解密')
        self.btn_run.setObjectName('primary')
        self.btn_run.setIcon(icons.icon('key', '#FFFFFF'))
        self.btn_run.setIconSize(QSize(16, 16))
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setToolTip('首次需要管理员授权（UAC）；密钥有效时只做增量同步，不会重复弹窗')
        self.btn_run.clicked.connect(self._run_all)
        row_run.addWidget(self.btn_run)
        self.b_recheck = QPushButton('重新检查')
        self.b_recheck.setIcon(icons.icon('refresh', '#4B5563'))
        self.b_recheck.setIconSize(QSize(16, 16))
        self.b_recheck.setToolTip('重新比对密钥与数据库 salt，并检查是否有新消息待同步')
        self.b_recheck.clicked.connect(self._recheck)
        row_run.addWidget(self.b_recheck)
        row_run.addStretch(1)
        self.lb_uac = QLabel('密钥有效时会跳过提权，只同步新增消息')
        self.lb_uac.setObjectName('tip')
        row_run.addWidget(self.lb_uac)
        gv.addLayout(row_run)

        self.lb_phase = QLabel('')            # 当前阶段文字（提取密钥中 / 解密中）
        self.lb_phase.setObjectName('tipWarn')
        self.lb_phase.setVisible(False)
        gv.addWidget(self.lb_phase)

        self.bar = QProgressBar()             # 解密进度（读子进程的 PROGRESS 行）
        self.bar.setVisible(False)
        gv.addWidget(self.bar)
        v.addWidget(gb_key)

        # ---------- 运行日志 ----------
        gb_log = QGroupBox('运行日志')
        gv2 = QVBoxLayout(gb_log)
        gv2.setSpacing(8)
        log_head = QHBoxLayout()
        log_head.setSpacing(6)
        lb_log_tip = QLabel('提取密钥、解密的实时输出；出问题时先看这里的第一行报错')
        lb_log_tip.setObjectName('tip')
        log_head.addWidget(lb_log_tip)
        log_head.addStretch(1)
        b_copy = QPushButton('复制')
        b_copy.setObjectName('quiet')
        b_copy.setCursor(Qt.PointingHandCursor)
        b_copy.setToolTip('把日志复制到剪贴板（反馈问题时方便贴出来）')
        b_copy.clicked.connect(self._copy_log)
        log_head.addWidget(b_copy)
        b_clear_log = QPushButton('清空')
        b_clear_log.setObjectName('quiet')
        b_clear_log.setCursor(Qt.PointingHandCursor)
        b_clear_log.setToolTip('清空这里的显示内容（不影响磁盘上的日志文件）')
        b_clear_log.clicked.connect(lambda: self.log.clear())
        log_head.addWidget(b_clear_log)
        gv2.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setPlaceholderText('这里会显示提取密钥、解密数据库的实时输出。\n'
                                    '第一次使用先点上面的「提取密钥并解密」。')
        gv2.addWidget(self.log)
        v.addWidget(gb_log, 1)

        # ---------- 底部：说明 + 关闭 ----------
        foot = QHBoxLayout()
        lb_foot = QLabel('所有数据只保存在本机，程序不联网、不上传。')
        lb_foot.setObjectName('tip')
        foot.addWidget(lb_foot)
        foot.addStretch(1)
        self.b_close = QPushButton('关闭')
        self.b_close.setToolTip('关闭设置窗口（不保存本次未点「保存」的改动）')
        self.b_close.clicked.connect(self.reject)
        foot.addWidget(self.b_close)
        v.addLayout(foot)

    # ==================================================================
    # 路径 / 账号列表
    # ==================================================================
    def _on_text_edited(self):
        """路径输入框有改动：清掉"已保存"提示，避免用户以为已经生效"""
        self.lb_saved.setText('')

    def _on_name_edited(self, text):
        """账号名称实时同步到下拉项与内存对象（不必等保存）"""
        self.lb_saved.setText('')
        if self.cur is None or self.cur not in self.accs:
            return
        self.cur['name'] = text.strip() or self.cur['id']
        self.cb_acc.blockSignals(True)
        self.cb_acc.setItemText(self.accs.index(self.cur), self._label(self.cur))
        self.cb_acc.blockSignals(False)

    def _rescan(self):
        """按当前输入的微信数据目录重新扫描 wxid 文件夹

        注意：扫描结果只更新内存里的 self.accs，点「保存」才写盘；
        本次已经在界面上改过但还没保存的名称会先带过来，避免重扫把名字冲掉。
        """
        self._collect_fields()
        names = {a['id']: a.get('name', '') for a in self.accs}
        root = accounts.normalize_root(self.ed_root.text())
        self.ed_root.blockSignals(True)
        self.ed_root.setText(root)                # 回写归一化后的路径，用户能看到实际用的是哪级
        self.ed_root.blockSignals(False)
        keep = self.cur['id'] if self.cur else ''
        self.accs, cur_id = accounts.load_accounts(root)
        for a in self.accs:
            kept = names.get(a['id'], '')
            if kept:
                a['name'] = kept
        if not any(a['id'] == keep for a in self.accs):
            keep = cur_id
        self.cur = next((a for a in self.accs if a['id'] == keep), None)
        self._fill_combo()
        self._fill_fields()
        self._recheck()

    def _pick_root(self):
        """选微信数据目录（xwechat_files 那一级），选完立刻重新扫描账号"""
        d = QFileDialog.getExistingDirectory(
            self, '选择微信数据目录（xwechat_files，里面是 wxid_ 开头的文件夹）',
            self.ed_root.text() or '')
        if d:
            self.ed_root.setText(d)
            self._rescan()

    def _fill_combo(self):
        """把账号列表填进下拉框，显示为 wxid(名称)"""
        self.cb_acc.blockSignals(True)            # 填充过程会触发 currentIndexChanged，先屏蔽
        self.cb_acc.clear()
        for a in self.accs:
            self.cb_acc.addItem('%s(%s)' % (a['id'], a.get('name') or a['id']))
        if self.cur is not None and self.cur in self.accs:
            self.cb_acc.setCurrentIndex(self.accs.index(self.cur))
        self.cb_acc.blockSignals(False)

    def _label(self, acc):
        """下拉项的显示文本：wxid(名称)"""
        return '%s(%s)' % (acc['id'], acc.get('name') or acc['id'])

    def _fill_fields(self):
        """把当前账号的信息刷到各控件上（名称、目录、登录状态）"""
        has = self.cur is not None

        # 路径说明
        n = len(self.accs)
        if not self.ed_root.text().strip():
            self.lb_root.setText('还没有选择微信数据目录。点「浏览…」选微信的 xwechat_files 文件夹即可。')
        elif n == 0:
            self.lb_root.setText('这个目录下没有 wxid_ 开头的文件夹。请确认选到 xwechat_files 这一级'
                                 '（里面应该能直接看到 wxid_xxx 目录，再往下才是 db_storage）。')
        else:
            self.lb_root.setText('已找到 %d 个微信号 · 每个账号的库路径自动拼接为 <wxid>\\db_storage'
                                 % n)

        if not has:
            self.ed_name.blockSignals(True)
            self.ed_name.clear()
            self.ed_name.blockSignals(False)
            self.lb_login.setText('')
            self.lb_login.setVisible(False)
            self.lb_acc_dir.setText('账号数据会保存到：%s（首次选中账号时自动创建）'
                                    % ACCOUNTS_ROOT)
            self.lb_status.setText('尚未添加账号')
            self.lb_missing.setText('')
            self.lb_phase.setVisible(False)
            self.bar.setVisible(False)
            self._refresh_enabled()
            return

        self.ed_name.blockSignals(True)
        self.ed_name.setText(self.cur.get('name', ''))
        self.ed_name.blockSignals(False)
        self.lb_acc_dir.setText('账号数据保存到：%s（自动创建，不可修改）'
                                % accounts.acc_dir(self.cur))
        self.lb_login.setVisible(True)
        self._refresh_login()
        self._check_keys()
        self._refresh_enabled()

    def _open_acc_dir(self):
        """在资源管理器里打开该账号的数据目录（不存在就先建出来）"""
        if self.cur is None:
            return
        d = accounts.acc_dir(self.cur)
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            QMessageBox.warning(self, '打开失败', '没能打开目录：\n%s\n\n%s' % (d, e))

    def _copy_log(self):
        """把日志内容复制到剪贴板（打包成 exe 后用户没法直接从控制台复制）"""
        text = self.log.toPlainText().strip()
        if not text:
            self.lb_saved.setText('日志还是空的')
            return
        QApplication.clipboard().setText(text)
        self.lb_saved.setText('✓ 日志已复制')

    def _refresh_login(self):
        """刷新"已登录/未登录"徽章

        判定依据：微信运行时独占打开自己的数据库文件，所以"库文件被占用"就等于"这个号在登录"。
        徽章颜色直接告诉用户能不能提到密钥。
        """
        if self.cur is None:
            self.lb_login.setText('')
            return
        state = detect.account_in_use(self.cur.get('db_dir', ''))
        if state is True:
            txt, css = '已登录', 'badgeGood'
        elif state is False:
            txt, css = '未登录', 'badgeBad'
        else:
            txt, css = '状态未知', 'badgeGray'
        self.lb_login.setText(txt)
        # 用对象名切换配色（样式集中在 theme.py，改色不用回来改代码）
        self.lb_login.setObjectName(css)
        self.lb_login.style().unpolish(self.lb_login)
        self.lb_login.style().polish(self.lb_login)
        self.lb_login.setToolTip('微信正在使用该账号的数据库 → 密钥在内存里，可以提取'
                                 if state is True else
                                 '微信没有打开该账号的数据库 → 密钥不在内存里，提取不到')

    def _collect_fields(self):
        """把界面上正在编辑的名称收回内存对象（保存/重扫/切换账号前调用）"""
        if self.cur is None:
            return
        if self.cur in self.accs:
            self.cb_acc.blockSignals(True)
            self.cb_acc.setItemText(self.accs.index(self.cur), self._label(self.cur))
            self.cb_acc.blockSignals(False)
        self.cur['name'] = self.ed_name.text().strip() or self.cur['id']

    def _switch_account(self, idx):
        """切换账号：立即建目录（不等保存），并刷新登录状态与密钥状态

        这里的 ensure() 会在 WeChatData\\accounts\\<wxid>\\ 下创建 account.json 与 db/，
        属于"选中即生效"的动作，不受「关闭」影响。
        """
        self._collect_fields()
        if 0 <= idx < len(self.accs):
            self.cur = self.accs[idx]
            accounts.ensure(self.cur)
        self._fill_fields()
        self._recheck()

    def _del_account(self):
        """删除该账号在程序数据区的全部内容（配置 + 密钥 + 解密库）"""
        if self.cur is None or self._stage is not None:
            return
        if QMessageBox.question(
                self, '删除账号数据',
                '确定删除「%s」在本程序内的全部数据吗？\n\n'
                '目录：%s\n\n'
                '内容包括：账号配置、密钥缓存、已解密的数据库、消息数缓存。\n'
                '微信原始聊天记录不受影响，但删掉后需要重新提取密钥并解密（约几分钟）。'
                % (self._label(self.cur), accounts.acc_dir(self.cur)),
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel
        ) != QMessageBox.Yes:
            return
        accounts.remove_account(self.cur)
        self.accs = [a for a in self.accs if a['id'] != self.cur['id']]
        self.cur = self.accs[0] if self.accs else None
        self._fill_combo()
        self._fill_fields()
        self._recheck()
        self.dataChanged.emit()      # 通知主窗口：当前账号可能已经没了，要重载
        self.log.appendPlainText('已删除该账号在本程序中的数据（微信原始数据不受影响）。')

    # ==================================================================
    # 密钥状态检查（「重新检查」的落点）
    # ==================================================================
    def _keys_file(self):
        """当前账号的 keys.json 路径；没有账号时返回空串（调用方据此跳过检查）"""
        return accounts.keys_file(self.cur) if self.cur else ''

    def _pending_sync(self):
        """有多少个库自上次解密后发生了变化（mtime / size / salt 任一不同）

        用来回答用户"我现在还需要点解密吗"，比只显示"密钥有效"更有用。
        """
        if self.cur is None:
            return 0
        db = self.cur.get('db_dir', '')
        try:
            with open(accounts.manifest_file(self.cur), encoding='utf-8') as f:
                manifest = json.load(f)
        except Exception:
            return 0
        n = 0
        for rel, ent in manifest.items():
            p = os.path.join(db, rel)
            try:
                st = os.stat(p)
                with open(p, 'rb') as fh:
                    salt = fh.read(16).hex()
            except Exception:
                continue
            if (ent.get('mtime') != st.st_mtime or ent.get('size') != st.st_size
                    or ent.get('salt') != salt):
                n += 1
        return n

    def _check_keys(self):
        """检查密钥缓存与库文件 salt 是否还对得上，把结果画到状态标签上

        四种情况：
            stale > 0   有库被微信重建过 → 密钥失效，必须重新提取
            valid > 0   有可用密钥 → 可以直接解密（增量同步不用提权）
            都为 0      还没提取过
            total=0     目录里没有库（路径选错 / 微信还没登录过）
        再加一层"这个微信号有没有登录"的判断，告诉用户为什么提不到、下一步该做什么。
        """
        if self.cur is None:
            self.lb_status.setText('尚未添加账号')
            self.lb_missing.setText('')
            return
        db, keys = self.cur.get('db_dir', ''), self._keys_file()
        if not os.path.isdir(db):
            self.lb_status.setText('<b><font color="#C0392B">● 微信数据目录无效</font></b>')
            self.lb_missing.setText('找不到目录：%s\n请回到「数据路径」重新选择微信的 '
                                    'xwechat_files 目录。' % db)
            return
        valid, stale, total_db = contacts.key_status(db, keys)
        if total_db == 0:
            self.lb_status.setText('<b><font color="#B26A00">● 目录里没有数据库文件</font></b>')
            self.lb_missing.setText('该账号下没有 .db 文件。通常是这个微信号还没在这台电脑上'
                                    '登录过、或数据目录被清理过。')
            return

        extra = []
        # 上次同步时间（manifest 的写入时间就是上次解密时间）
        man = accounts.manifest_file(self.cur)
        if os.path.exists(man):
            extra.append('上次同步 %s' % time.strftime(
                '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(man))))

        if stale > 0:
            head = ('<b><font color="#C0392B">● 密钥已失效</font></b>　'
                    '%d 个库被微信重建过，需要重新提取密钥' % stale)
        elif valid > 0:
            head = ('<b><font color="#17805B">● 密钥有效（已缓存）</font></b>　'
                    '%d 个库可直接解密' % valid)
            if total_db > valid:
                extra.append('另有 %d 个库没有密钥（多为用不到的空库，不影响聊天记录）'
                             % (total_db - valid))
        else:
            head = '<b><font color="#6B7280">● 尚未提取密钥</font></b>'

        # 未登录时，把"为什么提不到"和"该怎么做"写在最前面
        need_extract = stale > 0 or valid == 0
        state, msg = detect.describe(db)
        if need_extract and state == 'inactive':
            head += '　<font color="#C0392B">（该微信号未登录）</font>'
            extra.insert(0, msg + ' 请先在微信里登录这个微信号，再点「提取密钥并解密」。')
        elif need_extract and state == 'unknown':
            extra.insert(0, msg + ' 如果提取失败，请确认微信已登录并保持运行。')
        elif need_extract:
            # 已登录但还没提取过 / 密钥失效：直接告诉用户下一步该点哪里
            extra.insert(0, '该微信号已登录，点「提取密钥并解密」即可开始'
                            '（首次会弹一次管理员授权，之后都走增量同步）。')
        elif state == 'inactive':
            extra.insert(0, '该账号当前未登录，但密钥已缓存，仍可直接增量解密。')

        # 有新消息没同步 → 提示可以直接增量同步
        if need_extract is False:
            pending = self._pending_sync()
            if pending > 0:
                extra.append('有 %d 个库在解密后有新消息，点「提取密钥并解密」可增量同步'
                             '（密钥有效，不会弹 UAC）' % pending)

        if self._checked_at:
            extra.append('检查时间 %s' % self._checked_at)

        self.lb_status.setText(head)
        self.lb_missing.setText(' · '.join(extra))

    def _plain_status(self):
        """把当前状态转成一行纯文本，写进运行日志（方便回溯）"""
        if self.cur is None:
            return '尚未添加账号'
        db = self.cur.get('db_dir', '')
        if not os.path.isdir(db):
            return '微信数据目录无效'
        valid, stale, total = contacts.key_status(db, self._keys_file())
        return ('库 %d 个 · 密钥有效 %d · 失效 %d · 无密钥 %d'
                % (total, valid, stale, max(total - valid - stale, 0)))

    def _recheck(self):
        """「重新检查」：给用户看得见的反馈，而不是静默重算一遍

        做四件事：
            1) 按钮文字变「检查中…」，让点击有即时响应
            2) 重新判定登录状态（可能刚在微信里换了号）
            3) 重算密钥健康度 + 待同步库数
            4) 把检查时间与结果同时写进状态行和运行日志
        """
        if self._stage is not None:            # 解密中不打断
            return
        if self.cur is None:
            self._check_keys()
            self._checked_at = time.strftime('%H:%M:%S')
            self.lb_saved.setText('')
            self.log.appendPlainText('[%s] 尚未添加账号，先选择微信数据目录。'
                                     % self._checked_at)
            return
        self.b_recheck.setEnabled(False)
        self.b_recheck.setText('检查中…')
        QApplication.processEvents()           # 让按钮文字立刻刷新出来
        try:
            self._checked_at = time.strftime('%H:%M:%S')
            self._refresh_login()
            self._check_keys()
            self.log.appendPlainText('[检查 %s] %s' % (self._checked_at, self._plain_status()))
        finally:
            self.b_recheck.setText('重新检查')
            self._refresh_enabled()

    # ==================================================================
    # 保存
    # ==================================================================
    def _save(self):
        """「保存」：只写 微信数据目录 / 各账号名称 / 当前账号 这三样，保存后不关窗

        流程：校验目录 → 写 QSettings → 逐账号写 account.json → 反馈 → 必要时重扫。
        """
        if self._stage is not None:
            QMessageBox.information(self, '正在处理',
                                    '正在提取密钥或解密，请等当前任务完成后再保存。')
            return
        self._collect_fields()
        root = accounts.normalize_root(self.ed_root.text())
        if root and not os.path.isdir(root):
            QMessageBox.warning(self, '目录不存在',
                                '保存失败：这个目录不存在。\n\n%s\n\n'
                                '请点「浏览…」重新选择微信的 xwechat_files 目录。' % root)
            self.ed_root.setFocus()
            return
        old_root = self._orig_root

        accounts.set_root(root)
        cur_id = self.cur['id'] if self.cur else ''
        accounts.save_accounts(self.accs, cur_id)

        changed = ((root != self._orig_root) or (cur_id != self._orig_cur)
                   or any(a.get('name') != self._orig_names.get(a['id'])
                          for a in self.accs))
        # 更新快照，避免下次保存被判定成"又变了"
        self._orig_root, self._orig_cur = root, cur_id
        self._orig_names = {a['id']: a.get('name', '') for a in self.accs}

        stamp = time.strftime('%H:%M:%S')
        self.lb_saved.setText('✓ 已保存 %s' % stamp)
        self.log.appendPlainText('[保存 %s] 数据目录=%s · 账号=%s'
                                 % (stamp, root or '（未设置）',
                                    self._label(self.cur) if self.cur else '（未选择）'))

        # 目录换了但没点「重新扫描」→ 顺手扫一遍，避免界面上还是老账号列表
        if changed and root != old_root:
            self._rescan()
        if changed:
            self.dataChanged.emit()

    # ==================================================================
    # 提取密钥并解密
    # ==================================================================
    def _run_all(self):
        """两段式流程：需要就先提权提取密钥，然后普通权限增量解密

        第一段（可能弹 UAC）：调 find_all_keys 扫微信进程内存，把每把密钥
            连同库文件的 salt 一起写进 keys.json；
        第二段（不弹 UAC）：调 decrypt_db 逐个库解密到 <账号目录>/db，
            没变化的库靠 manifest.json 跳过。
        期间界面锁定、不允许关窗，见 _guard_close()。
        """
        if self._stage is not None:            # 防连点
            return
        if self.cur is None:
            QMessageBox.information(self, '先选账号',
                                    '还没有可用的微信号。\n\n'
                                    '请在「数据路径」里选微信的 xwechat_files 目录，'
                                    '再在「当前账号」下拉里选择要处理的微信号。')
            return
        db = self.cur.get('db_dir', '')
        if not os.path.isdir(db):
            QMessageBox.warning(self, '数据目录无效',
                                '找不到这个账号的数据库目录：\n%s\n\n'
                                '请确认微信数据目录是否选对，或该微信号是否已在本机登录过。' % db)
            return
        self._collect_fields()
        # 点这个按钮也顺带把路径与账号目录准备好（即使不点保存）
        accounts.set_root(accounts.normalize_root(self.ed_root.text()))
        accounts.ensure(self.cur)
        dec = self.cur['dec_dir']
        os.makedirs(dec, exist_ok=True)

        valid, stale, _ = contacts.key_status(db, self._keys_file())
        if valid == 0 or stale > 0:
            # 提前拦一次：未登录时提不到密钥，先给出明确提示，别白弹一次 UAC
            state, msg = detect.describe(db)
            if state == 'inactive':
                QMessageBox.warning(
                    self, '该微信号未登录',
                    msg + '\n\n密钥只在微信登录后才会加载进内存。请先在微信里登录这个微信号，'
                           '再回来点「提取密钥并解密」。')
                return

        self.log.clear()
        self._pos = 0
        self._stage = 'keys'                   # 先占位，_refresh_enabled 据此锁定界面
        self._set_busy(True, '正在提取密钥（可能弹出管理员授权窗口，请点「是」）…')
        self._refresh_enabled()

        if valid > 0 and stale == 0:
            # 密钥还有效 → 跳过提权，直接增量解密
            self.log.appendPlainText('密钥已缓存且有效，跳过提取（无需管理员权限），直接增量解密。')
            self._start_decrypt()
            return

        self.log.appendPlainText('== 第 1 步 / 共 2 步：以管理员权限提取密钥（需微信正在运行）==')
        self.log.appendPlainText('提示：多开微信时程序会自动找到本账号所在的那个微信进程。')
        self._log_path = accounts.keys_log(self.cur)
        ok = runner.run_elevated(runner.BOOT_KEYS,
                                 [db, self._keys_file()], self._log_path)
        if not ok:
            self.log.appendPlainText('[失败] 管理员授权被取消，或提权被系统拦截。'
                                     '可以再点一次；若一直失败，请以管理员身份运行本程序。')
            self._finish_stage()
            return
        self._timer.start(600)       # 提权进程是异步的，靠轮询日志判断结束

    def _poll_log(self):
        """轮询子进程日志（QTimer 驱动）"""
        if not self._log_path:
            self._timer.stop()
            return
        lines, self._pos, rc = runner.read_new_lines(self._log_path, self._pos)
        for ln in lines:
            if self._stage == 'decrypt':
                self._append_decrypt_line(ln)
            else:
                self.log.appendPlainText(ln)
        if rc is None:
            return                   # 还没结束，下一轮继续
        self._timer.stop()
        if self._stage == 'keys':
            if rc == 0:
                self.log.appendPlainText('== 第 2 步 / 共 2 步：增量解密到账号目录 ==')
                self._start_decrypt()
            else:
                self.log.appendPlainText('[失败] 密钥提取没有成功。请确认：'
                                         '① 微信正在运行；② 已登录的就是当前选中的这个微信号；'
                                         '③ 本程序有管理员权限。')
                self._finish_stage()
        elif self._stage == 'decrypt':
            self.log.appendPlainText('解密完成。' if rc == 0 else '解密过程出错（退出码 %d），'
                                     '详情见上面的日志。' % rc)
            self._finish_stage()
            if rc == 0:
                self._checked_at = time.strftime('%H:%M:%S')
                self._check_keys()
                self.dataChanged.emit()   # 解出新数据 → 主窗口重载列表

    def _append_decrypt_line(self, ln):
        """解密日志里的 'PROGRESS 3/20 解密 message_2.db' 解析成进度条"""
        if ln.startswith('PROGRESS '):
            head, _, rest = ln.partition(' ')
            frac, _, name = rest.partition(' ')
            try:
                cur, total = frac.split('/')
                self.bar.setRange(0, int(total))
                self.bar.setValue(int(cur))
                self.bar.setFormat('%v/%m 个库 · %p%')
                self.lb_phase.setText('正在解密第 %s 个，共 %s 个库…' % (cur, total))
            except Exception:
                pass
            self.log.appendPlainText('  · %s' % name)   # 只显示库名，不刷 PROGRESS 前缀
        else:
            self.log.appendPlainText(ln)

    def _start_decrypt(self):
        """启动解密子进程（普通权限），日志走 decrypt.log"""
        self._log_path = accounts.decrypt_log(self.cur)
        self._proc, _logf = runner.run_plain(
            runner.BOOT_DECRYPT,
            [self.cur.get('db_dir', ''), self.cur['dec_dir'],
             self._keys_file(), accounts.manifest_file(self.cur)], self._log_path)
        self._stage = 'decrypt'
        self._pos = 0
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.bar.setFormat('准备中…')
        self.lb_phase.setVisible(True)
        self.lb_phase.setText('正在解密聊天数据库，请保持窗口打开，完成后会自动刷新列表。')
        self._timer.start(500)

    def _set_busy(self, busy, hint=''):
        """切换"忙碌"外观：显示进度条 / 阶段文字"""
        self.bar.setVisible(busy)
        if busy:
            self.bar.setRange(0, 0)
            self.bar.setFormat('处理中…')
        self.lb_phase.setVisible(busy)
        self.lb_phase.setText(hint or '')

    def _finish_stage(self):
        """两段流程结束后的收尾：停进度条、恢复按钮"""
        self._stage = None
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.bar.setVisible(False)
        self.lb_phase.setVisible(False)
        self.lb_phase.setText('')
        self._refresh_enabled()

    # ==================================================================
    # 忙碌锁定 / 关窗保护
    # ==================================================================
    def _refresh_enabled(self):
        """按"是否忙碌 / 是否有账号"统一刷新所有控件的可用性"""
        busy = self._stage is not None
        has = self.cur is not None
        self.ed_root.setEnabled(not busy)
        self.b_browse.setEnabled(not busy)
        self.b_rescan.setEnabled(not busy)
        self.cb_acc.setEnabled(bool(self.accs) and not busy)
        self.ed_name.setEnabled(has and not busy)
        self.b_del.setEnabled(has and not busy)
        self.b_open_dir.setEnabled(has)
        self.b_save.setEnabled(not busy)
        self.btn_run.setEnabled(has and not busy)
        self.b_recheck.setEnabled(has and not busy and not self._timer.isActive())
        self.b_close.setEnabled(not busy)
        self.btn_run.setText('处理中…' if busy else '提取密钥并解密')

    def _guard_close(self):
        """解密/提取进行中拦下关闭动作，返回 True 表示可以关

        需求：解密过程中窗口不能被关掉 —— 手工关窗会杀掉子进程、留下半套数据，
        下次还得从头再来。所以这里统一拦 closeEvent / Esc / 「关闭」按钮。
        """
        if self._stage is None:
            return True
        if self._stage == 'keys':
            txt = ('正在从微信进程内存里提取密钥。\n\n此时关闭窗口会中断提取，'
                   '密钥缓存可能不完整，需要重新提取。\n请稍等几秒，完成后再关闭。')
        else:
            done, total = self.bar.value(), self.bar.maximum()
            prog = ('%d/%d' % (done, total)) if total > 0 else '进行中'
            txt = ('正在解密聊天数据库（%s）。\n\n解密中断不会损坏已解密的部分，'
                   '但需要重新打开窗口继续；建议等全部完成（通常几十秒到几分钟）。'
                   % prog)
        QMessageBox.information(self, '请等待完成', txt)
        return False

    def closeEvent(self, e):
        """关窗（右上角 ×）：忙碌时拒绝关闭，并停掉轮询定时器"""
        if not self._guard_close():
            e.ignore()
            return
        self._timer.stop()
        super().closeEvent(e)

    def reject(self):
        """Esc / 「关闭」按钮 走这里：同样受忙碌保护"""
        if not self._guard_close():
            return
        self._timer.stop()
        super().reject()
