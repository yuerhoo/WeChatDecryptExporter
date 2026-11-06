# -*- coding: utf-8 -*-
"""界面图标与勾选框贴图（全部用 QPainter 现画，不依赖任何外部图片文件）

为什么不用 png 素材：
    1) 程序是单文件 exe，多一个资源文件就多一份 add-data 与打包路径判断；
    2) 图标要跟着语境换色（顶栏灰、主按钮白、删除红），现画才能一套线条多处复用；
    3) 勾选框里的「勾」QSS 画不出来（QSS 只能画方框），必须由 image 提供，
       这里按需生成到缓存目录，再把路径写进样式表。

坐标约定：
    所有图标都按 24x24 的画布设计，再整体缩放到目标像素尺寸（scale），
    所以 16px 的按钮图标和 64px 的结果图标是同一套线条，不会走形。
"""
import os

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
                         QPolygonF)

# 图标缓存：(名字, 颜色) -> QIcon；尺寸在 QIcon 内部按需缩放
_ICON_CACHE = {}
# 样式表贴图缓存：{'tick': 'C:/.../tick_16.png', ...}
_QSS_ASSETS = None


# ---------------------------------------------------------------------------
# 缓存目录 / 样式表贴图
# ---------------------------------------------------------------------------
def _cache_dir():
    """贴图落盘目录：%LOCALAPPDATA%\\WeChatExportUI（不可写时退回临时目录）

    不用 exe 同级目录：单文件 exe 常被放在桌面或只读目录，往旁边写文件容易被拦截。
    """
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('TEMP') or '.'
    d = os.path.join(base, 'WeChatExportUI')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.environ.get('TEMP') or '.'
    return d


def _save(pm, filename):
    """存 png 并返回 QSS 里能直接用的正斜杠路径（Windows 反斜杠在 url() 里要转义）"""
    path = os.path.join(_cache_dir(), filename)
    try:
        pm.save(path, 'PNG')
    except Exception:
        return ''
    return path.replace('\\', '/')


def _tick_path(rect):
    """对勾的折线路径（按传入矩形等比取点，方便 16px 和 40px 共用）

    三个点：左下起笔 → 折点 → 右上收笔，圆头圆角看起来最稳。
    """
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath(QPointF(x + w * 0.26, y + h * 0.53))
    path.lineTo(x + w * 0.44, y + h * 0.72)
    path.lineTo(x + w * 0.76, y + h * 0.30)
    return path


def _tick_pixmap(color, size=16, dpr=2):
    """生成一张只有对勾的透明贴图

    dpr=2 表示按 2 倍像素画（画布 32px，逻辑尺寸仍是 16px）——用 setDevicePixelRatio
    告诉 Qt 这是高清图，QSS 里指定 16px 时会自动按物理像素取用，高分屏下不发虚。
    """
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_tick_path(QRectF(0, 0, size, size)))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _arrow_pixmap(color, size=12, dpr=2):
    """下拉框的小三角（改用细细的折线，比系统默认的实心三角更贴合整体风格）"""
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(size * 0.22, size * 0.40))
    path.lineTo(size * 0.50, size * 0.66)
    path.lineTo(size * 0.78, size * 0.40)
    p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _dot_pixmap(color, size=16, dpr=2):
    """单选框选中态的「实心圆点」贴图

    单选框和勾选框不同：选中画的是中间一个圆点。QSS 只能改边框/底色，
    纯 border 技巧（粗边框留白心）在部分缩放比例下会糊成一团，
    所以这里直接生成一张居中实心圆的 png，叠在白底圆框里最清晰。
    """
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    # 圆点直径约为圆框的一半（8/16），居中放置
    d = size * 0.5
    p.drawEllipse(QRectF((size - d) / 2.0, (size - d) / 2.0, d, d))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def qss_assets():
    """返回样式表需要的贴图路径（同一次运行只生成一次）

    QSS 里的写法：image: url("<路径>")
    """
    global _QSS_ASSETS
    if _QSS_ASSETS is not None:
        return _QSS_ASSETS
    _QSS_ASSETS = {
        'tick': _save(_tick_pixmap('#FFFFFF', 16), 'tick_white.png'),
        'tick_disabled': _save(_tick_pixmap('#F4F8FC', 16), 'tick_disabled.png'),
        'arrow': _save(_arrow_pixmap('#6B7280'), 'arrow.png'),
        'arrow_disabled': _save(_arrow_pixmap('#B3BAC5'), 'arrow_disabled.png'),
        # 单选框选中态的实心圆点（主色 / 禁用淡化）
        'dot': _save(_dot_pixmap('#185FA5', 16), 'dot_blue.png'),
        'dot_disabled': _save(_dot_pixmap('#B7CCE2', 16), 'dot_disabled.png'),
    }
    return _QSS_ASSETS


# ---------------------------------------------------------------------------
# 勾选框绘制（表格里的勾选用它，和 QSS 的贴图保持同一套比例）
# ---------------------------------------------------------------------------
def paint_check(p, rect, checked, hover=False, enabled=True):
    """在 rect 中央画一个 16px 的勾选框

    p       QPainter（调用方负责 save/restore）
    rect    单元格矩形（勾选框居中放）
    checked 是否已勾选
    hover   鼠标是否悬停在该单元格上（未勾选时边框变主色，给出可点击的暗示）
    enabled 是否可用（禁用态整体变灰）
    """
    from PyQt5.QtGui import QBrush

    size = 16.0
    box = QRectF(rect.center().x() - size / 2.0 + 0.5,
                 rect.center().y() - size / 2.0 + 0.5, size - 1.0, size - 1.0)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)

    if checked:
        # 已勾选：主色实心块 + 白色对勾（禁用时整体降饱和）
        bg = QColor('#185FA5') if enabled else QColor('#B7CCE2')
        p.setPen(QPen(bg, 1.2))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(box, 3.6, 3.6)
        pen = QPen(QColor('#FFFFFF') if enabled else QColor('#F4F8FC'), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(_tick_path(box))
    else:
        border = '#185FA5' if (hover and enabled) else '#C6CEDB'
        fill = '#FFFFFF' if enabled else '#F7F8FA'
        if not enabled:
            border = '#E3E7EE'
        p.setPen(QPen(QColor(border), 1.2))
        p.setBrush(QBrush(QColor(fill)))
        p.drawRoundedRect(box, 3.6, 3.6)
    p.restore()


def check_pixmap(checked=True, size=44, color='#17805B'):
    """独立的大号勾/叉图标：用在导出结果页等需要一眼看清状态的地方"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 24.0, size / 24.0)
    if checked:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(2, 2, 20, 20))
        pen = QPen(QColor('#FFFFFF'), 2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(7.2, 12.3))
        path.lineTo(10.6, 15.6)
        path.lineTo(16.9, 8.4)
        p.drawPath(path)
    else:
        p.setPen(QPen(QColor(color), 1.8))
        p.setBrush(QColor('#FDECEA'))
        p.drawEllipse(QRectF(2, 2, 20, 20))
        p.setPen(QPen(QColor(color), 2.0))
        p.drawLine(QPointF(12, 7.2), QPointF(12, 13.4))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(11.1, 15.6, 1.9, 1.9))
    p.end()
    return pm


# ---------------------------------------------------------------------------
# 线条图标
# ---------------------------------------------------------------------------
def _draw(p, name, color):
    """在已经缩放到 24x24 坐标系的 painter 上画指定图标"""
    c = QColor(color)
    pen = QPen(c, 1.9)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    if name == 'settings':
        # 两根滑杆：线 + 圆形滑块（比齿轮更好认，也更适合 16px）
        p.drawLine(QPointF(3.6, 8.0), QPointF(20.4, 8.0))
        p.drawLine(QPointF(3.6, 16.0), QPointF(20.4, 16.0))
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(QRectF(12.4, 5.4, 5.2, 5.2))
        p.drawEllipse(QRectF(6.4, 13.4, 5.2, 5.2))

    elif name == 'info':
        p.drawEllipse(QRectF(3.5, 3.5, 17.0, 17.0))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(11.1, 7.0, 1.8, 1.8))
        pen2 = QPen(c, 1.9)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawLine(QPointF(12.0, 10.9), QPointF(12.0, 17.0))

    elif name == 'refresh':
        # 缺口在右上的圆弧 + 箭头，表示「重新检查 / 同步」
        p.drawArc(QRectF(4.2, 4.2, 15.6, 15.6), 55 * 16, 275 * 16)
        path = QPainterPath(QPointF(16.9, 3.4))
        path.lineTo(19.6, 7.0)
        path.lineTo(15.6, 8.0)
        p.drawPath(path)

    elif name == 'export':
        # 向下箭头 + 托盘：导出到文件夹
        p.drawLine(QPointF(12.0, 3.6), QPointF(12.0, 14.4))
        path = QPainterPath(QPointF(7.8, 10.3))
        path.lineTo(12.0, 14.6)
        path.lineTo(16.2, 10.3)
        p.drawPath(path)
        p.drawPath(_poly([(4.6, 16.6), (4.6, 19.0), (19.4, 19.0), (19.4, 16.6)]))

    elif name == 'folder':
        path = QPainterPath(QPointF(3.4, 7.6))
        path.lineTo(3.4, 17.8)
        path.quadTo(3.4, 19.6, 5.2, 19.6)
        path.lineTo(18.8, 19.6)
        path.quadTo(20.6, 19.6, 20.6, 17.8)
        path.lineTo(20.6, 9.6)
        path.quadTo(20.6, 7.8, 18.8, 7.8)
        path.lineTo(12.6, 7.8)
        path.lineTo(10.4, 5.2)
        path.lineTo(5.2, 5.2)
        path.quadTo(3.4, 5.2, 3.4, 7.6)
        p.drawPath(path)

    elif name == 'trash':
        p.drawLine(QPointF(4.2, 7.2), QPointF(19.8, 7.2))
        p.drawPath(_poly([(9.0, 7.0), (9.4, 4.2), (14.6, 4.2), (15.0, 7.0)]))
        p.drawPath(_poly([(6.2, 7.4), (6.2, 18.4), (7.8, 20.0), (16.2, 20.0),
                          (17.8, 18.4), (17.8, 7.4)]))
        p.drawLine(QPointF(10.2, 10.8), QPointF(10.2, 16.8))
        p.drawLine(QPointF(13.8, 10.8), QPointF(13.8, 16.8))

    elif name == 'key':
        p.drawEllipse(QRectF(3.6, 11.4, 8.4, 8.4))
        p.drawLine(QPointF(10.4, 13.6), QPointF(19.6, 4.4))
        p.drawLine(QPointF(16.8, 7.2), QPointF(15.0, 9.0))
        p.drawLine(QPointF(19.0, 5.0), QPointF(17.2, 6.8))

    elif name == 'search':
        p.drawEllipse(QRectF(4.0, 4.0, 12.2, 12.2))
        p.drawLine(QPointF(15.0, 15.0), QPointF(20.0, 20.0))

    elif name == 'play':
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        path = QPainterPath(QPointF(8.4, 5.6))
        path.lineTo(19.0, 12.0)
        path.lineTo(8.4, 18.4)
        path.closeSubpath()
        p.drawPath(path)

    elif name == 'close':
        p.drawLine(QPointF(6.4, 6.4), QPointF(17.6, 17.6))
        p.drawLine(QPointF(17.6, 6.4), QPointF(6.4, 17.6))

    elif name == 'chat':
        # 空状态用的大对话气泡（带三个点，说明「这里会显示聊天数据」）
        path = QPainterPath()
        path.addRoundedRect(QRectF(3.0, 4.4, 18.0, 13.2), 4.0, 4.0)
        p.drawPath(path)
        p.drawPath(_poly([(8.4, 17.6), (8.4, 21.0), (12.4, 17.6)]))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for x in (8.6, 12.0, 15.4):
            p.drawEllipse(QRectF(x - 0.95, 10.1, 1.9, 1.9))

    elif name == 'group':
        # 群聊：两个小人
        p.drawEllipse(QRectF(4.0, 4.6, 6.4, 6.4))
        p.drawPath(_poly([(2.6, 19.4), (2.6, 17.2), (4.6, 13.4), (8.8, 13.4),
                          (10.8, 17.2), (10.8, 19.4)]))
        p.drawEllipse(QRectF(13.4, 6.4, 5.6, 5.6))
        p.drawPath(_poly([(12.6, 19.4), (12.6, 17.6), (14.4, 14.4), (18.0, 14.4),
                          (19.8, 17.6), (19.8, 19.4)]))

    elif name == 'ok':
        p.drawEllipse(QRectF(3.4, 3.4, 17.2, 17.2))
        p.drawPath(_tick_path(QRectF(4.4, 3.4, 15.0, 17.0)))

    elif name == 'warn':
        p.drawPath(_poly([(12.0, 3.6), (21.0, 19.4), (3.0, 19.4), (12.0, 3.6)]))
        p.drawLine(QPointF(12.0, 9.4), QPointF(12.0, 13.6))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(11.1, 15.6, 1.8, 1.8))


def _poly(points):
    """由点列表生成折线路径（不闭合，配合圆头画笔很好看）"""
    path = QPainterPath(QPointF(*points[0]))
    for pt in points[1:]:
        path.lineTo(QPointF(*pt))
    return path


def pixmap(name, color='#4B5563', size=24):
    """直接拿一张 QPixmap（结果页的大图标用）"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 24.0, size / 24.0)
    _draw(p, name, color)
    p.end()
    return pm


def icon(name, color='#4B5563'):
    """拿一个 QIcon（按钮用）：内置 16/20/24/32/48 五个尺寸，缩放时不会糊"""
    key = (name, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    ic = QIcon()
    for s in (16, 20, 24, 32, 48):
        ic.addPixmap(pixmap(name, color, s))
    _ICON_CACHE[key] = ic
    return ic
