# -*- coding: utf-8 -*-
"""会话列表的表格模型（QAbstractTableModel 子类）

为什么不用 QTableWidget：4 万多个联系人，QTableWidget 要一次性建几万个 item，
既慢又吃内存。Model/View 只向当前可见的几十行要数据，滚动才取，代价小得多。

列：第 0 列是勾选框，后面是名称 / 微信号 / 消息数 / 最近消息。
勾选状态存在 _checked 集合里（按 username），所以过滤、切 Tab、重载数据都不会丢。
"""
import datetime

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal


def fmt_last(ts):
    """最近消息时间：今年只显示 月-日 时:分，跨年才显示年份"""
    if not ts:
        return ''
    dt = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    if dt.year == now.year:
        return dt.strftime('%m-%d %H:%M')
    return dt.strftime('%Y-%m-%d')


class SessionTableModel(QAbstractTableModel):
    """会话列表的数据模型（一个 Tab 一个实例）

    列：第 0 列是勾选框（自绘），1 名称 / 2 微信号 / 3 消息数 / 4 最近消息。
    只存 Sess 的引用、不复制数据，4 万行也不会额外吃内存；
    勾选状态按 username 存在 set 里，所以跨 Tab、跨搜索过滤都不会丢。
    """

    HEADERS = ['', '名称', '微信号 / 群 ID', '消息数', '最近消息']
    checkedChanged = pyqtSignal(int)      # 勾选数量变化（主窗口用来刷"已选 N"）

    def __init__(self, parent=None):
        """_all 是全量、_rows 是过滤后真正显示的那一批，两者不要混用"""
        super().__init__(parent)
        self._all = []        # 当前 Tab 的全量会话
        self._rows = []       # 过滤后要显示的会话
        self._checked = set() # 已勾选的 username
        self._text = ''       # 当前搜索词（已转小写）

    # ---------- 数据源 ----------
    def set_sessions(self, sessions):
        """切换 Tab / 重载数据时整表替换"""
        self.beginResetModel()
        self._all = list(sessions)
        self._apply()
        self.endResetModel()
        self.checkedChanged.emit(len(self._checked))

    def set_filter(self, text):
        """按搜索词过滤（名称、微信号、alias、拼音首字母、全拼都能匹配）"""
        self.beginResetModel()
        self._text = (text or '').strip().lower()
        self._apply()
        self.endResetModel()

    def _apply(self):
        """根据当前搜索词算出 _rows（只做内存过滤，不查库）"""
        t = self._text
        if not t:
            self._rows = list(self._all)
        else:
            self._rows = [s for s in self._all if
                          t in s.display.lower()
                          or t in (s.username or '').lower()
                          or t in (s.alias or '').lower()
                          or t in (s.pinyin or '').lower()
                          or t in (s.pinyin_full or '').lower()]

    # ---------- 勾选 ----------
    def checked_sessions(self):
        """所有已勾选的会话（含被搜索词过滤掉、当前不可见的）"""
        return [s for s in self._rows if s.username in self._checked] + \
               [s for s in self._all if s.username in self._checked
                and s not in self._rows]

    def clear_checked(self):
        """清空本 Tab 的勾选"""
        self._checked.clear()
        self.layoutChanged.emit()
        self.checkedChanged.emit(0)

    def check_one(self, username):
        """只勾选一个（右键"只导出此会话"时可用）"""
        self._checked = {username}
        self.layoutChanged.emit()
        self.checkedChanged.emit(1)

    def toggle_all(self, check=None):
        """全选 / 全不选「当前可见」的行（表头勾选列被点击时调用）

        check=None 时按现状取反：还有没选的就全选，已经全选就全不选。
        只影响过滤后的行 —— 搜索「工作」再点全选，不会把别的会话也选上。
        """
        users = [s.username for s in self._rows]
        if check is None:
            check = not all(u in self._checked for u in users) if users else False
        if check:
            self._checked.update(users)
        else:
            self._checked.difference_update(users)
        self.layoutChanged.emit()
        self.checkedChanged.emit(len(self._checked))

    def visible_count(self):
        """当前可见（过滤后）的行数"""
        return len(self._rows)

    # ---------- 消息数回填 ----------
    def update_count(self, username, count):
        """后台统计完一个会话的消息数 → 更新对象并只刷新那一格（避免整表重绘）"""
        for coll in (self._all, self._rows):
            for s in coll:
                if s.username == username:
                    s.count = count
        for i, s in enumerate(self._rows):
            if s.username == username:
                ix = self.index(i, 3)
                self.dataChanged.emit(ix, ix)

    # ---------- QAbstractTableModel 必要接口 ----------
    def rowCount(self, parent=QModelIndex()):
        """行数 = 过滤后的行数（不是全量）；parent 有效说明在问子节点，返回 0"""
        # 树形结构才需要处理 parent，这里只看根层
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        """列数固定 5 列，与 HEADERS 等长"""
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """表头文字 + 勾选列的悬停提示（点表头 = 全选当前列表）"""
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.ToolTipRole and section == 0:
                # 勾选列的表头是空的，给个说明：点这里可以全选当前列表
                return '点击这里：全选 / 取消全选当前列表\n（只影响搜索过滤后看到的行）'
        return None

    def flags(self, index):
        """只有第 0 列可以勾选，其余列只可选中"""
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.DisplayRole):
        """View 每次要画一格就调一次这里，所以只做最简单的取值"""
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 1:
                return s.display
            if col == 2:
                return s.alias or s.username
            if col == 3:
                # count 为 None 表示后台还在统计
                return ('…' if s.count is None else
                        ('0' if s.count == 0 else format(s.count, ',')))
            if col == 4:
                return fmt_last(s.last_ts)
        elif role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if s.username in self._checked else Qt.Unchecked
        elif role == Qt.TextAlignmentRole and col in (3, 4):
            return Qt.AlignRight | Qt.AlignVCenter
        elif role == Qt.ToolTipRole:
            # 悬浮显示完整名称 + 原始 id（长名称被截断时有用）
            return '%s\n%s' % (s.display, s.username)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        """点勾选框时由 View 回调，只处理第 0 列的勾选状态"""
        if index.column() == 0 and role == Qt.CheckStateRole:
            s = self._rows[index.row()]
            if value == Qt.Checked:
                self._checked.add(s.username)
            else:
                self._checked.discard(s.username)
            self.dataChanged.emit(index, index)
            self.checkedChanged.emit(len(self._checked))
            return True
        return False

    def session_at(self, row):
        """取某一行对应的 Sess（右键菜单、双击导出用）"""
        return self._rows[row]
