# -*- coding: utf-8 -*-
"""导出执行与进度弹窗

职责：
    ExportWorker           在工作线程里跑 core.exporter.export_sessions，
                           通过信号把进度/日志回传主线程（Qt 的界面只能在主线程改）
    ExportProgressDialog   模态进度窗：进度条 + 当前会话 + 逐会话日志，
                           结束后变成结果页（成功/跳过/失败统计 + 打开文件夹）

为什么要开线程：十万级消息要读几十个库、写几个 xlsx，放在主线程会让窗口卡死假死。

导出进行中点关闭不会直接退出：先弹确认，确认后只是"请求取消"，
等当前会话写完后线程收尾，窗口再关（避免线程还在写文件时把窗口销毁）。
"""
import os

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton,
                             QVBoxLayout)

from core.exporter import export_sessions
from . import icons


class ExportWorker(QThread):
    """后台导出线程：唯一入口是 run()，结果放在 self.result"""

    # done, total, 名称, 本会话已写行数, 本会话总行数
    tick = pyqtSignal(int, int, str, int, int)
    logline = pyqtSignal(str)      # 预留：需要单独打日志时用

    def __init__(self, sessions, opts, data, dec_dir):
        """参数就是 core.exporter.export_sessions 的前四个入参，原样转交"""
        super().__init__()
        self.sessions, self.opts, self.data, self.dec_dir = sessions, opts, data, dec_dir
        self.result = None         # export_sessions 的返回值，供结束后展示
        self._cancel = False       # 取消标记（由主线程置位，导出循环里轮询）

    def cancel(self):
        """请求取消：不会立即中断，当前会话写完后在循环头退出"""
        self._cancel = True

    def run(self):
        """线程体：把 exporter 的进度回调转成 Qt 信号"""
        def prog(done, total, name, cur, session_rows):
            """exporter 的回调 → Qt 信号。只做转发，绝不直接碰界面控件
            （工作线程改 UI 会崩，必须靠信号回主线程）"""
            self.tick.emit(done, total, name, cur, session_rows)

        self.result = export_sessions(
            self.sessions, self.opts, self.data, self.dec_dir,
            progress=prog, cancelled=lambda: self._cancel)


class ExportProgressDialog(QDialog):
    """导出进度窗：构造即开始导出"""

    def __init__(self, sessions, opts, data, dec_dir, parent=None):
        """建窗口的同时就启动后台线程：这个弹窗没有"准备"状态，打开即开工"""
        super().__init__(parent)
        self.setWindowTitle('正在导出')
        self.setModal(True)
        self.setMinimumSize(600, 460)
        self._done = False                 # 导出是否已结束（决定关闭行为）

        # 先连信号再 start()，避免漏掉最早的几条进度
        self.worker = ExportWorker(sessions, opts, data, dec_dir)
        self.worker.tick.connect(self._on_tick)
        self.worker.finished.connect(self._on_done)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(8)

        # ---------- 标题 ----------
        self.lb_title = QLabel('正在导出聊天记录')
        self.lb_title.setObjectName('h1')
        v.addWidget(self.lb_title)
        lb_sub = QLabel('导出过程中请勿关闭窗口；文件名里的时间范围与导出选项一致。')
        lb_sub.setObjectName('sub')
        lb_sub.setWordWrap(True)
        v.addWidget(lb_sub)
        # 结果状态的大图标（导出中隐藏，结束后显示对勾/感叹号）
        self.lb_icon = QLabel()
        self.lb_icon.setAlignment(Qt.AlignCenter)
        self.lb_icon.setVisible(False)
        v.addWidget(self.lb_icon)
        v.addSpacing(6)

        # ---------- 进度 ----------
        self.bar = QProgressBar()
        self.bar.setFormat('%v / %m 个会话 · %p%')
        v.addWidget(self.bar)
        self.lb_state = QLabel('准备中…')
        self.lb_state.setObjectName('tip')
        v.addWidget(self.lb_state)
        self.cur = QLabel('')              # 当前正在处理的会话名
        self.cur.setObjectName('badge')
        self.cur.setVisible(False)
        v.addWidget(self.cur)

        # ---------- 逐会话日志 ----------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText('每个会话导出完成后会在这里留一行记录。')
        v.addWidget(self.log, 1)

        self.btn_cancel = QPushButton('取消导出')
        self.btn_cancel.setIcon(icons.icon('close', '#4B5563'))
        self.btn_cancel.setIconSize(QSize(16, 16))
        self.btn_cancel.setToolTip('停止后续会话的导出（已写好的文件会保留）')
        self.btn_cancel.clicked.connect(self._cancel)
        v.addWidget(self.btn_cancel, 0, Qt.AlignRight)

        self._n = len(sessions)
        self._skipped = 0
        self.worker.start()

    def _on_tick(self, done, total, name, cur, session_rows):
        """进度回调：cur==0 是"开始处理某会话"，cur>0 是"某会话写完" """
        self.bar.setRange(0, total or 1)
        self.bar.setValue(done)
        self.lb_state.setText('已处理 %d / %d 个会话' % (done, total))
        if name and cur == 0:
            self.cur.setVisible(True)
            self.cur.setText('正在导出：%s' % name)
        elif name:
            self.log.appendPlainText('✓ %s　%s 条' % (name, format(session_rows, ',')))
            self.cur.setVisible(False)
            self.cur.setText('')

    def _cancel(self):
        """取消：置标记并禁用按钮，当前会话结束后线程收尾"""
        self.worker.cancel()
        self.btn_cancel.setEnabled(False)
        self.lb_state.setText('正在取消…（当前会话写完后停止）')

    def _on_done(self):
        """导出结束：把窗口切成结果页，并把「取消导出」换成「打开文件夹/关闭」"""
        self._done = True
        r = self.worker.result or {}
        self.btn_cancel.setEnabled(False)
        cancelled = bool(r.get('cancelled'))
        if cancelled:
            self.log.appendPlainText('已取消：后续会话未导出。')

        # 结果标题 + 状态大图标（一眼看出成功还是中断）
        self.setWindowTitle('导出已取消' if cancelled else '导出完成')
        self.lb_title.setText('导出已取消' if cancelled else '导出完成')
        self.lb_icon.setPixmap(icons.check_pixmap(
            not cancelled, 46, '#B26A00' if cancelled else '#17805B'))
        self.lb_icon.setVisible(True)

        ok = r.get('ok', 0)
        skipped = r.get('skipped', 0)
        failed = r.get('failed', 0)
        total = r.get('total', 0)
        self.bar.setRange(0, 1)
        self.bar.setValue(0 if cancelled else 1)
        self.bar.setFormat('已取消' if cancelled else '完成')
        # 进度条换成结果色（样式在 theme.py 里按 state 属性取）
        self.bar.setProperty('state', 'cancel' if cancelled else ('fail' if failed else 'done'))
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)

        self.lb_state.setText(
            '成功 <b><font color="#17805B">%d</font></b> 个 · '
            '跳过 %d 个（时间范围内没有消息） · '
            '<font color="%s">失败 %d 个</font>　|　'
            '共 <b>%s</b> 条 · 用时 %.1f 秒'
            % (ok, skipped, '#C0392B' if failed else '#6B7280', failed,
               format(total, ','), r.get('elapsed', 0)))
        for e in r.get('errors', []):
            self.log.appendPlainText('! %s' % e)

        # 结果区显示导出目录（所有文件都在这一个目录里）
        folder = r.get('folder', '') or ''
        self.cur.setVisible(bool(folder))
        self.cur.setText('保存位置：%s' % folder if folder else '')

        self.btn_cancel.deleteLater()
        bopen = QPushButton('打开文件夹')
        bopen.setObjectName('primary')
        bopen.setIcon(icons.icon('folder', '#FFFFFF'))
        bopen.setIconSize(QSize(16, 16))
        bopen.setCursor(Qt.PointingHandCursor)
        bopen.setToolTip('在资源管理器里查看刚导出的文件')
        bopen.clicked.connect(lambda: os.startfile(folder) if folder else None)
        bclose = QPushButton('关闭')
        bclose.setToolTip('关闭这个窗口，回到主界面')
        bclose.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(bclose)
        row.addWidget(bopen)
        self.layout().addLayout(row)

    def closeEvent(self, e):
        """导出未结束时关窗：先问一句，确认后只做"请求取消"，等线程收尾"""
        if not self._done:
            if QMessageBox.question(
                    self, '导出还没有完成',
                    '现在关闭会中断剩余会话的导出（已经写好的文件会保留）。\n\n'
                    '要停止导出吗？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                e.ignore()
                return
            self._cancel()
            e.ignore()                 # 等 _on_done 后再由用户关闭
            return
        super().closeEvent(e)
