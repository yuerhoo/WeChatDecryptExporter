# -*- coding: utf-8 -*-
"""表格委托：把第 0 列画成自定义勾选框

为什么不用系统自带的勾选框：
    QTableView 里的勾选框由 QCommonStyle 直接画（QSS 的 QCheckBox::indicator 管不到它），
    在 Windows 上是个又小又扁的方框，高 DPI 下还发虚。这里自己画：
        · 尺寸、圆角、配色和 QSS 里的 QCheckBox 完全一致
        · 悬停时边框变主色，给出「这一格能点」的暗示
        · 整列（不只是那 16px）都能点，命中面积大很多
"""
from PyQt5.QtCore import QEvent, QRectF, QSize, Qt
from PyQt5.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import icons


class CheckDelegate(QStyledItemDelegate):
    """第 0 列专用：只画勾选框，其余交互交给模型"""

    BOX = 16          # 勾选框边长（逻辑像素）

    def paint(self, painter, option, index):
        """先让原生样式画整行背景（选中/交替色/悬停），再叠一个自己的勾选框"""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ''                                  # 文字留空，只要背景
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        hover = bool(option.state & QStyle.State_MouseOver)
        enabled = bool(option.state & QStyle.State_Enabled)
        icons.paint_check(painter, QRectF(option.rect), checked, hover, enabled)

    def sizeHint(self, option, index):
        """给勾选列一个最小宽度，避免被表头挤成 0"""
        return QSize(34, 30)

    def editorEvent(self, event, model, option, index):
        """整格可点：按下吞掉事件（防止误触发其它行为），松开时切换勾选"""
        if index.column() != 0:
            return False
        if event.type() == QEvent.MouseButtonRelease:
            if not option.rect.contains(event.pos()):
                return False
            if event.button() == Qt.LeftButton:
                cur = index.data(Qt.CheckStateRole)
                new = Qt.Unchecked if cur == Qt.Checked else Qt.Checked
                model.setData(index, new, Qt.CheckStateRole)
                return True
        elif event.type() == QEvent.MouseButtonPress:
            # 吞掉按下事件，避免 View 把它当成"开始编辑"或"拖选"
            return option.rect.contains(event.pos())
        return False


