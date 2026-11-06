# -*- coding: utf-8 -*-
"""导出弹窗：左侧为本次已选会话，右侧为导出选项

选项分四块：
    时间范围   近 7 天 / 近 30 天 / 今年 / 全部，或自行填起止时间
    消息类型   8 个复选框（文本、图片、语音…）；一个都不勾 = 导出全部类型
    输出方式   每个会话一个文件 / 每个会话一个工作表
    保存位置   固定为「微信聊天记录导出目录」，不可修改（所有导出文件都在这里）

弹窗只负责"收集参数"，真正的写盘在 ui/export_runner.py 的后台线程里做。
除保存位置外的选项都会记进 QSettings，下次打开时回显。
"""
import datetime
import os

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDateTimeEdit, QDialog,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QRadioButton, QVBoxLayout)

from core.contacts import CAT_NAMES
from core.messages import EXPORT_TYPES
from app_config import Prefs, export_dir
from . import icons

# 时间快捷块：(显示名, 天数)；「今年」「全部」不是按天数算，用 None / -1 占位
CHIPS = [('近 7 天', 7), ('近 30 天', 30), ('今年', None), ('全部', -1)]

# 输出方式：(模式 key, 标题, 一句话说明)
# 「合并为一个工作表」已按需求下线，只剩两种：每会话一个文件 / 每会话一个工作表
MODES = [('per_sheet', '每个会话一个工作表', '单文件，按会话名切换工作表'),
         ('per_file', '每个会话单独一个文件', '推荐，方便分别发送')]


class ExportDialog(QDialog):
    """导出参数弹窗（模态）

    自己不写盘、不碰数据库，只做三件事：收集参数 → 存偏好 → 发 startExport 信号。
    真正的读库和写 xlsx 在 ui/export_runner.py 的后台线程里，避免界面卡死。
    """

    startExport = pyqtSignal(object)     # 携带组装好的 ExportOpts 发给主窗口

    def __init__(self, sessions, data, dec_dir, prefs, parent=None):
        """参数：
        sessions  已勾选的会话（core.contacts.Sess 列表），左侧清单显示它们
        data      联系人/群昵称数据，导出发送给后台线程解析发送人用
        dec_dir   解密库根目录
        prefs     app_config.Prefs，用于回显/保存上次的选项
        """
        super().__init__(parent)
        self.sessions = list(sessions)   # 本次要导出的会话（Sess 对象）
        self.data = data                 # 联系人/群昵称数据，导出时解析发送人用
        self.dec_dir = dec_dir           # 解密库根目录
        self.prefs = prefs
        self.setWindowTitle('导出聊天记录')
        self.setModal(True)
        self.setMinimumSize(900, 600)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)

        # ---------- 标题 ----------
        head = QVBoxLayout()
        head.setSpacing(2)
        lb_title = QLabel('导出聊天记录')
        lb_title.setObjectName('h1')
        head.addWidget(lb_title)
        self.lb_sub = QLabel('勾选好的会话会导出到统一的「微信聊天记录导出目录」，同名文件自动覆盖，'
                             '不会按账号或批次再建子文件夹。')
        self.lb_sub.setObjectName('sub')
        self.lb_sub.setWordWrap(True)
        head.addWidget(self.lb_sub)
        v.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        v.addLayout(body, 1)

        # ---------- 左：已选会话清单（超过可视高度自动滚动） ----------
        gb_list = QGroupBox('本次导出')
        left = QVBoxLayout(gb_list)
        left.setSpacing(6)
        self.lb_count = QLabel('已选 <b><font color="#185FA5">%d</font></b> 个会话'
                               % len(self.sessions))
        left.addWidget(self.lb_count)
        by_cat = {}
        for s in self.sessions:
            by_cat[s.category] = by_cat.get(s.category, 0) + 1
        lb_cat = QLabel(' · '.join('%s %d' % (CAT_NAMES.get(c, c), n)
                                   for c, n in sorted(by_cat.items())) or '—')
        lb_cat.setObjectName('tip')
        left.addWidget(lb_cat)
        self.lst = QListWidget()
        self.lst.setToolTip('最多显示全部已选会话，可滚动查看')
        for s in self.sessions:
            # count 为 None 表示后台还在统计，先显示省略号
            txt = '%s   %s' % (s.display, ('…' if s.count is None else format(s.count, ',')))
            item = QListWidgetItem(txt)
            # 用图标区分群聊和单聊，长列表里更好扫
            item.setIcon(icons.icon('group' if s.category == 'group' else 'chat',
                                    '#8A94A3'))
            self.lst.addItem(item)
        left.addWidget(self.lst, 1)
        bclear = QPushButton('返回主窗口重新选择')
        bclear.setObjectName('quiet')
        bclear.setToolTip('关掉这个窗口，回到列表调整勾选（已勾选的不会被清空）')
        bclear.clicked.connect(self.reject)
        left.addWidget(bclear)
        body.addWidget(gb_list, 1)

        # ---------- 右：选项区 ----------
        right = QVBoxLayout()
        right.setSpacing(10)

        # 时间范围：快捷按钮 + 两个日期时间控件（手改时间会自动取消快捷按钮的选中态）
        gb1 = QGroupBox('时间范围')
        gv1 = QVBoxLayout(gb1)
        gv1.setSpacing(8)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        self.chips = []
        for name, _v in CHIPS:
            b = QPushButton(name)
            b.setObjectName('chip')
            b.setCheckable(True)              # 做成"选中态可见"的胶囊按钮
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip('把起止时间设为%s（点日期可改成任意区间）' % name)
            b.clicked.connect(lambda _c, n=name: self._apply_chip(n))
            self.chips.append((b, name))
            chips_row.addWidget(b)
        chips_row.addStretch(1)
        gv1.addLayout(chips_row)
        tr = QHBoxLayout()
        tr.setSpacing(8)
        self.dt0 = QDateTimeEdit()
        self.dt0.setCalendarPopup(True)
        self.dt0.setDisplayFormat('yyyy-MM-dd HH:mm')
        self.dt0.setToolTip('开始时间（含）')
        self.dt0.dateTimeChanged.connect(lambda _d: self._clear_chips())
        self.dt1 = QDateTimeEdit()
        self.dt1.setCalendarPopup(True)
        self.dt1.setDisplayFormat('yyyy-MM-dd HH:mm')
        self.dt1.setToolTip('结束时间（含）')
        self.dt1.dateTimeChanged.connect(lambda _d: self._clear_chips())
        tr.addWidget(self.dt0, 1)
        lb_arrow = QLabel('至')
        lb_arrow.setObjectName('tip')
        tr.addWidget(lb_arrow)
        tr.addWidget(self.dt1, 1)
        gv1.addLayout(tr)
        lb_time_tip = QLabel('选「全部」时不做时间过滤，文件名里也显示「全部」')
        lb_time_tip.setObjectName('tip')
        gv1.addWidget(lb_time_tip)
        right.addWidget(gb1)

        # 消息类型：8 个复选框，按 EXPORT_TYPES 的顺序排（每行 4 个）
        gb2 = QGroupBox('消息类型')
        gv2 = QGridLayout(gb2)
        gv2.setHorizontalSpacing(10)
        gv2.setVerticalSpacing(8)
        self.type_boxes = {}
        for i, (name, code) in enumerate(EXPORT_TYPES):
            cb = QCheckBox(name)
            cb.setCursor(Qt.PointingHandCursor)
            cb.setToolTip('勾选后才会导出「%s」类消息' % name)
            cb.stateChanged.connect(lambda _s: self._update_est())
            self.type_boxes[code] = cb
            gv2.addWidget(cb, i // 4, i % 4)
        link = QHBoxLayout()
        link.addWidget(QLabel('一个都不勾 = 导出全部类型'))
        link.addStretch(1)
        b_all = QPushButton('全选')
        b_all.setObjectName('quiet')
        b_all.setCursor(Qt.PointingHandCursor)
        b_all.clicked.connect(lambda: self._set_types(None))
        b_only = QPushButton('仅文本')
        b_only.setObjectName('quiet')
        b_only.setCursor(Qt.PointingHandCursor)
        b_only.clicked.connect(lambda: self._set_types({1}))   # local_type 1 = 文本
        link.addWidget(b_all)
        link.addWidget(b_only)
        gv2.addLayout(link, 2, 0, 1, 4)
        right.addWidget(gb2)

        # 输出方式：两个单选，objectName 直接存模式 key，导出时按名字取值
        # 注意：QRadioButton 不渲染富文本（<span> 会原样显示成文字），
        #       说明文字只能用纯文本 + 「·」分隔，详细解释放 tooltip 里
        gb3 = QGroupBox('输出方式')
        gv3 = QVBoxLayout(gb3)
        gv3.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        for key, label, tip in MODES:
            rb = QRadioButton('%s　·　%s' % (label, tip))
            rb.setCursor(Qt.PointingHandCursor)
            rb.setObjectName(key)
            # 动态属性：让样式表把整行画成"可点卡片"（选中时浅蓝底 + 主色边）
            rb.setProperty('modeCard', True)
            rb.setMinimumHeight(40)
            rb.setToolTip(tip)
            rb.toggled.connect(lambda _c: self._update_est())
            self.mode_group.addButton(rb)
            gv3.addWidget(rb)
        right.addWidget(gb3)

        # 保存位置：固定为「微信聊天记录导出目录」，只读展示 + 一个打开按钮
        # （需求：所有导出文件必须集中在这一个目录下，不按账号/批次再建子文件夹）
        gb4 = QGroupBox('保存位置')
        gv4 = QVBoxLayout(gb4)
        gv4.setSpacing(8)
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.ed_dir = QLineEdit(export_dir())
        self.ed_dir.setReadOnly(True)          # 不可编辑，避免文件散落到各处
        self.ed_dir.setToolTip('所有导出文件都保存在这里，不按账号或批次再建子文件夹')
        sr.addWidget(self.ed_dir, 1)
        bopen = QPushButton('打开文件夹')
        bopen.setIcon(icons.icon('folder', '#4B5563'))
        bopen.setIconSize(QSize(16, 16))
        bopen.setToolTip('在资源管理器里打开导出目录')
        bopen.clicked.connect(self._open_dir)
        sr.addWidget(bopen)
        gv4.addLayout(sr)
        lb_name = QLabel('文件名：序号_备注名_开始时间_结束时间.xlsx；同名文件自动覆盖')
        lb_name.setObjectName('tip')
        lb_name.setWordWrap(True)
        gv4.addWidget(lb_name)
        right.addWidget(gb4)

        body.addLayout(right, 2)

        # ---------- 底部：预计结果 + 取消 / 开始导出 ----------
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.lb_est = QLabel('')
        self.lb_est.setObjectName('tip')
        foot.addWidget(self.lb_est)
        foot.addStretch(1)
        bc = QPushButton('取消')
        bc.setToolTip('回到主窗口，已勾选的会话保持不变')
        bc.clicked.connect(self.reject)
        foot.addWidget(bc)
        self.bgo = QPushButton('开始导出')
        self.bgo.setObjectName('primary')
        self.bgo.setIcon(icons.icon('play', '#FFFFFF'))
        self.bgo.setIconSize(QSize(16, 16))
        self.bgo.setCursor(Qt.PointingHandCursor)
        self.bgo.setMinimumHeight(34)
        self.bgo.setToolTip('按上面的选项导出到「微信聊天记录导出目录」')
        self.bgo.clicked.connect(self._go)
        foot.addWidget(self.bgo)
        v.addLayout(foot)

        self._restore()          # 回显上次的选项
        self._update_est()

    # ---------- 记忆回显 ----------
    def _restore(self):
        """把上次保存的选项恢复到界面（默认：近 30 天 / 仅文本 / 每个会话单独一个文件）"""
        from PyQt5.QtCore import QDateTime
        now = datetime.datetime.now()
        chip = self.prefs.get('export/range_mode', '近 30 天')
        if chip == '自定义':
            # 自定义范围存的是 epoch 秒（QDateTime.toTime_t），这里还原成控件值
            d0 = self.prefs.get('export/t0', None)
            d1 = self.prefs.get('export/t1', None)
            base = QDateTime(now - datetime.timedelta(days=30))
            end = QDateTime(now.replace(hour=23, minute=59))
            self.dt0.setDateTime(QDateTime.fromTime_t(int(float(d0))) if d0 else base)
            self.dt1.setDateTime(QDateTime.fromTime_t(int(float(d1))) if d1 else end)
            self._clear_chips()
        else:
            names = [c[0] for c in CHIPS]
            self._apply_chip(chip if chip in names else '近 30 天')

        # 消息类型：存的是一组 int，QSettings 会读回成列表/字符串，统一转 int
        types = self.prefs.get('export/types', None)
        codes = set(int(t) for t in types) if types else {1}
        for c, cb in self.type_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(c in codes)
            cb.blockSignals(False)

        mode = self.prefs.get('export/mode', 'per_file')
        # 老版本可能存着已下线的 'single_sheet'（或别的脏值），这里统一回落到默认模式，
        # 否则下面一轮循环会把所有单选都置灰，_go() 取 checkedButton() 会拿到 None
        if mode not in [k for k, _l, _t in MODES]:
            mode = 'per_file'
        for b in self.mode_group.buttons():
            b.blockSignals(True)
            b.setChecked(b.objectName() == mode)
            b.blockSignals(False)

        # 保存位置不记忆：永远显示统一的导出目录
        self.ed_dir.setText(export_dir())

    def _save_prefs(self):
        """记录本次选项，供下次打开时回显（保存位置是固定的，不入库）"""
        self.prefs.set('export/range_mode', self._current_chip())
        self.prefs.set('export/t0', self.dt0.dateTime().toTime_t())
        self.prefs.set('export/t1', self.dt1.dateTime().toTime_t())
        codes = [c for c, cb in self.type_boxes.items() if cb.isChecked()]
        self.prefs.set('export/types', codes)
        self.prefs.set('export/mode',
                       self.mode_group.checkedButton().objectName())

    # ---------- 时间快捷块 ----------
    def _apply_chip(self, name):
        """点快捷块：把起止时间填好，并高亮当前快捷块"""
        now = datetime.datetime.now()
        if name == '近 7 天':
            d0 = (now - datetime.timedelta(days=7)).replace(hour=0, minute=0)
            d1 = now.replace(hour=23, minute=59)
        elif name == '近 30 天':
            d0 = (now - datetime.timedelta(days=30)).replace(hour=0, minute=0)
            d1 = now.replace(hour=23, minute=59)
        elif name == '今年':
            d0 = datetime.datetime(now.year, 1, 1, 0, 0)
            d1 = now.replace(hour=23, minute=59)
        else:  # 全部：用足够宽的区间覆盖所有消息（微信消息最早不会早于 2010 年）
            d0 = datetime.datetime(2010, 1, 1, 0, 0)
            d1 = now.replace(year=2038, month=12, day=31, hour=23, minute=59)
        for b, n in self.chips:
            b.setChecked(n == name)
        # 改控件值会触发 dateTimeChanged → _clear_chips，先屏蔽信号免得把刚点亮的按钮又灭掉
        self.dt0.blockSignals(True)
        self.dt1.blockSignals(True)
        self.dt0.setDateTime(d0)
        self.dt1.setDateTime(d1)
        self.dt0.blockSignals(False)
        self.dt1.blockSignals(False)
        self._update_est()

    def _clear_chips(self):
        """手动改了时间 → 不再属于任何快捷范围"""
        for b, _n in self.chips:
            b.setChecked(False)
        self._update_est()

    def _current_chip(self):
        """当前选中的快捷块名；都不选中即为「自定义」"""
        for b, n in self.chips:
            if b.isChecked():
                return n
        return '自定义'

    # ---------- 类型/目录 ----------
    def _set_types(self, codes):
        """codes=None 表示全选，否则只勾选集合内的类型"""
        for c, cb in self.type_boxes.items():
            cb.setChecked(True if codes is None else c in codes)
        self._update_est()

    def _open_dir(self):
        """在资源管理器里打开导出目录（不存在时先创建）"""
        d = export_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception:
            pass

    def _update_est(self):
        """底部预计结果：会话数 / 已知条数 / 会生成几个文件"""
        n = len(self.sessions)
        if not n:
            self.lb_est.setText('未选择会话')
            return
        known = sum(s.count or 0 for s in self.sessions)
        pending = sum(1 for s in self.sessions if s.count is None)
        if known and not pending:
            cnt = '约 %s 条' % format(known, ',')
        elif pending and not known:
            cnt = '条数统计中，以实际导出为准'
        else:
            cnt = '已知 %s 条，其余统计中' % format(known, ',')
        btn = self.mode_group.checkedButton()
        mode = btn.objectName() if btn is not None else 'per_file'
        tail = {'per_file': '将生成 %d 个 xlsx' % n,
                'per_sheet': '合并为 1 个 xlsx · %d 个工作表' % n}[mode]
        self.lb_est.setText('已选 %d 个会话 · %s · %s' % (n, cnt, tail))

    # ---------- 开始 ----------
    def _go(self):
        """校验参数 → 组装 ExportOpts → 存偏好 → 关弹窗并发出导出信号"""
        if not self.sessions:
            QMessageBox_warning(self, '还没有选择会话，请先回主窗口勾选要导出的会话。')
            return
        if self.dt0.dateTime() > self.dt1.dateTime():
            QMessageBox_warning(self, '时间范围不对：开始时间（%s）晚于结束时间（%s）。\n\n'
                                     '请调整这两个时间，或直接点上面的快捷块。'
                                % (self.dt0.dateTime().toString('yyyy-MM-dd HH:mm'),
                                   self.dt1.dateTime().toString('yyyy-MM-dd HH:mm')))
            return
        from core.exporter import ExportOpts
        o = ExportOpts()
        o.mode = self.mode_group.checkedButton().objectName()
        if self._current_chip() == '全部':
            # 「全部」时把时间条件置空：SQL 不带时间过滤，文件名后缀也写「全部」
            o.t0 = o.t1 = None
        else:
            o.t0 = self.dt0.dateTime().toTime_t()
            o.t1 = self.dt1.dateTime().toTime_t()
        codes = [c for c, cb in self.type_boxes.items() if cb.isChecked()]
        o.types = set(codes) if codes else None      # 一个都没勾 = 不按类型过滤
        o.keyword = ''
        self._save_prefs()
        self.accept()
        self.startExport.emit(o)


def QMessageBox_warning(parent, msg='请填写保存位置'):
    """延迟导入 QMessageBox：模块级导入在部分 PyQt5 版本里会拖慢启动"""
    from PyQt5.QtWidgets import QMessageBox
    QMessageBox.warning(parent, '提示', msg)
