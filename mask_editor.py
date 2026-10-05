import sys
import os
import shutil
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel,
                               QPushButton, QSlider, QSpinBox, QColorDialog, QFileDialog,
                               QComboBox, QGroupBox, QMessageBox, QScrollArea, QSizePolicy)
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QFont
import numpy as np
from PIL import Image
import cv2
import matplotlib.pyplot as plt


class MiniMap(QLabel):
    """小型預覽圖，顯示完整影像並標示目前可見區域"""
    # 點擊minimap時發送信號 (相對位置 0~1)
    navigation_requested = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.setStyleSheet("border: 1px solid #666; background-color: #222;")
        self.setAlignment(Qt.AlignCenter)

        self.preview_pixmap = None   # 縮小的合成圖
        self.viewport_rect = QRectF()  # 可見區域在 0~1 歸一化座標
        self._img_rect = QRect()     # preview_pixmap 在 label 中的繪製位置

    def update_preview(self, composited_image: QImage, viewport_rect: QRectF):
        """
        composited_image: 完整的合成圖 (原圖+mask)
        viewport_rect: 可見區域 (歸一化 0~1 座標, x, y, w, h)
        """
        if composited_image is None:
            return
        self.viewport_rect = viewport_rect

        # 將合成圖縮放到 minimap 尺寸內
        pixmap = QPixmap.fromImage(composited_image)
        available = self.size()
        margin = 4
        target_w = available.width() - margin * 2
        target_h = available.height() - margin * 2
        scaled = pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_pixmap = scaled

        # 計算繪製位置（居中）
        x = (available.width() - scaled.width()) // 2
        y = (available.height() - scaled.height()) // 2
        self._img_rect = QRect(x, y, scaled.width(), scaled.height())

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(34, 34, 34))

        if self.preview_pixmap:
            painter.drawPixmap(self._img_rect.topLeft(), self.preview_pixmap)

            # 繪製可見區域矩形
            vr = self.viewport_rect
            rx = self._img_rect.x() + vr.x() * self._img_rect.width()
            ry = self._img_rect.y() + vr.y() * self._img_rect.height()
            rw = vr.width() * self._img_rect.width()
            rh = vr.height() * self._img_rect.height()

            # 裁剪到圖片範圍
            rx = max(rx, self._img_rect.x())
            ry = max(ry, self._img_rect.y())
            rw = min(rw, self._img_rect.right() - rx)
            rh = min(rh, self._img_rect.bottom() - ry)

            if rw > 0 and rh > 0:
                # 半透明遮罩：可見區域外變暗
                overlay = QColor(0, 0, 0, 100)
                view_rect = QRectF(rx, ry, rw, rh)

                # 上方
                painter.fillRect(QRectF(self._img_rect.x(), self._img_rect.y(),
                                        self._img_rect.width(), ry - self._img_rect.y()), overlay)
                # 下方
                painter.fillRect(QRectF(self._img_rect.x(), ry + rh,
                                        self._img_rect.width(), self._img_rect.bottom() - (ry + rh)), overlay)
                # 左方
                painter.fillRect(QRectF(self._img_rect.x(), ry,
                                        rx - self._img_rect.x(), rh), overlay)
                # 右方
                painter.fillRect(QRectF(rx + rw, ry,
                                        self._img_rect.right() - (rx + rw), rh), overlay)

                # 紅色邊框
                pen = QPen(QColor(255, 60, 60), 2)
                painter.setPen(pen)
                painter.drawRect(QRectF(rx, ry, rw, rh))

        painter.end()

    def mousePressEvent(self, event):
        self._navigate(event.pos())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._navigate(event.pos())

    def _navigate(self, pos):
        """將點擊位置轉為歸一化座標並發信號"""
        if not self.preview_pixmap or self._img_rect.width() == 0:
            return
        # 歸一化到 0~1
        nx = (pos.x() - self._img_rect.x()) / self._img_rect.width()
        ny = (pos.y() - self._img_rect.y()) / self._img_rect.height()
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        self.navigation_requested.emit(nx, ny)


class MaskCanvas(QLabel):
    """可編輯的畫布"""
    def __init__(self, parent_editor=None):
        super().__init__()
        self.setMinimumSize(800, 600)
        self.setAlignment(Qt.AlignCenter)
        
        self.parent_editor = parent_editor
        
        self.original_array = None
        self.original_image = None
        self.original_mask = None
        self.mask_array = None
        self.display_pixmap = None
        
        self.undo_history = []
        self.max_undo_steps = 50
        
        self.drawing = False
        self.panning = False
        self.space_pressed = False
        self.last_point = QPoint()
        self.brush_size = 10
        self.current_label = 1
        self.eraser_mode = False
        self.mask_opacity = 0.5
        
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
        
        self.contrast_factor = 1.0
        self.brightness_offset = 0
        
        self.label_colors = self.generate_label_colors(20)
        
        self.setFocusPolicy(Qt.StrongFocus)
    
    def generate_label_colors(self, n_labels):
        colors = {}
        colors[0] = (0, 0, 0, 0)
        tab20_colors = plt.cm.tab20.colors
        for i in range(1, 20):
            rgb = tab20_colors[i]
            colors[i] = (
                int(rgb[0] * 255),
                int(rgb[1] * 255),
                int(rgb[2] * 255),
                200
            )
        return colors
    
    def save_state(self):
        if self.mask_array is not None:
            self.undo_history.append(self.mask_array.copy())
            if len(self.undo_history) > self.max_undo_steps:
                self.undo_history.pop(0)
    
    def undo(self):
        if len(self.undo_history) > 0:
            self.mask_array = self.undo_history.pop()
            self.update_display()
            return True
        return False
    
    def reset_to_original(self):
        if self.original_mask is not None:
            self.save_state()
            self.mask_array = self.original_mask.copy()
            self.update_display()
    
    def keep_only_label(self, label_value):
        """清空mask，只保留指定label的區域"""
        if self.mask_array is None:
            return False
        self.save_state()
        new_mask = np.zeros_like(self.mask_array)
        new_mask[self.mask_array == label_value] = label_value
        self.mask_array = new_mask
        self.update_display()
        return True
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self.space_pressed = True
            if self.parent_editor:
                self.parent_editor.update_mode_display()
        elif event.key() == Qt.Key_Z and (event.modifiers() & Qt.ControlModifier) and not event.isAutoRepeat():
            self.undo()
            if self.parent_editor:
                print("✓ Ctrl+Z 返回上一步")
        elif event.key() == Qt.Key_A and not event.isAutoRepeat():
            # A: 切換到上一個 label
            if self.parent_editor:
                self.parent_editor.prev_label()
        elif event.key() == Qt.Key_D and not event.isAutoRepeat():
            # D: 切換到下一個 label
            if self.parent_editor:
                self.parent_editor.next_label()
        elif event.key() == Qt.Key_W and not event.isAutoRepeat():
            # W: 畫筆模式
            if self.parent_editor:
                self.parent_editor.set_tool_mode('brush')
        elif event.key() == Qt.Key_S and not event.isAutoRepeat():
            # S: 橡皮擦模式
            if self.parent_editor:
                self.parent_editor.set_tool_mode('eraser')
    
    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self.space_pressed = False
            if self.parent_editor:
                self.parent_editor.update_mode_display()
    
    def wheelEvent(self, event):
        modifiers = event.modifiers()
        delta = event.angleDelta().y()
        
        if modifiers & Qt.ControlModifier:
            if delta > 0:
                self.zoom_factor *= 1.1
            else:
                self.zoom_factor /= 1.1
            self.zoom_factor = max(0.1, min(self.zoom_factor, 10.0))
            self.update_display()
            # 同步縮放滑桿
            if self.parent_editor:
                self.parent_editor.sync_zoom_slider()
        else:
            if self.parent_editor:
                if delta > 0:
                    self.parent_editor.prev_slice()
                else:
                    self.parent_editor.next_slice()
    
    def reset_view(self):
        self.zoom_factor = 1.5
        self.pan_offset = QPoint(0, 0)
        self.update_display()
        if self.parent_editor:
            self.parent_editor.sync_zoom_slider()
    
    def load_images(self, img_path, mask_path):
        self.undo_history = []
        self.zoom_factor = 1.5
        self.pan_offset = QPoint(0, 0)
        
        img = cv2.imread(img_path, cv2.IMREAD_UNCHANGED)
        
        if img.dtype == np.uint16:
            img_min, img_max = img.min(), img.max()
            if img_max > img_min:
                img_8bit = ((img - img_min) / (img_max - img_min) * 255).astype(np.uint8)
            else:
                img_8bit = np.zeros_like(img, dtype=np.uint8)
        else:
            img_8bit = img
        
        if len(img_8bit.shape) == 2:
            img_rgb = cv2.cvtColor(img_8bit, cv2.COLOR_GRAY2RGB)
        else:
            img_rgb = cv2.cvtColor(img_8bit, cv2.COLOR_BGR2RGB)
        
        self.original_array = img
        h, w = img_rgb.shape[:2]
        self.original_image = QImage(img_rgb.data, w, h, w*3, QImage.Format_RGB888).copy()
        
        mask_result_path = mask_path.replace("\\masks\\", "\\masks_result\\")
        mask_result_path = mask_result_path.replace("/masks/", "/masks_result/")
    
        mask_loaded = False
        
        if os.path.exists(mask_result_path):
            mask = cv2.imread(mask_result_path, cv2.IMREAD_UNCHANGED)
            if mask is not None:
                if len(mask.shape) == 3:
                    mask = mask[:, :, 0]
                self.original_mask = mask.astype(np.uint8).copy()
                self.mask_array = mask.astype(np.uint8).copy()
                mask_loaded = True
        
        if not mask_loaded and os.path.exists(mask_path):
            mask = cv2.imread(mask_path, cv2.IMREAD_UNCHANGED)
            if mask is not None:
                if len(mask.shape) == 3:
                    mask = mask[:, :, 0]
                self.original_mask = mask.astype(np.uint8).copy()
                self.mask_array = mask.astype(np.uint8).copy()
                mask_loaded = True
        
        if not mask_loaded:
            self.original_mask = np.zeros((h, w), dtype=np.uint8)
            self.mask_array = np.zeros((h, w), dtype=np.uint8)
        
        self.update_display()
    
    def _build_composited_array(self):
        """建立合成影像的 numpy array (原圖+遮罩疊加), 回傳 (h,w,3) uint8"""
        if self.original_image is None or self.mask_array is None:
            return None
        h = self.original_image.height()
        w = self.original_image.width()
        ptr = self.original_image.constBits()
        img_array = np.array(ptr).reshape(h, w, 3).copy()

        if self.contrast_factor != 1.0 or self.brightness_offset != 0:
            img_array = img_array.astype(np.float32)
            img_array = (img_array - 128) * self.contrast_factor + 128
            img_array = img_array + self.brightness_offset
            img_array = np.clip(img_array, 0, 255).astype(np.uint8)

        result = img_array.astype(np.float32)
        unique_labels = np.unique(self.mask_array)
        for lv in unique_labels:
            if lv == 0:
                continue
            if lv in self.label_colors:
                c = self.label_colors[lv]
                alpha = (c[3] / 255.0) * self.mask_opacity
                color_rgb = np.array([c[0], c[1], c[2]], dtype=np.float32)
                m = (self.mask_array == lv)
                result[m] = result[m] * (1.0 - alpha) + color_rgb * alpha
        return result.astype(np.uint8)

    def get_viewport_rect(self):
        """回傳目前可見區域的歸一化矩形 (QRectF, 0~1)"""
        if self.mask_array is None:
            return QRectF(0, 0, 1, 1)
        img_w = self.mask_array.shape[1]
        img_h = self.mask_array.shape[0]

        scaled_w = img_w * self.zoom_factor
        scaled_h = img_h * self.zoom_factor

        # 圖片在畫布上的起始位置
        cx = (self.width() - scaled_w) / 2 + self.pan_offset.x()
        cy = (self.height() - scaled_h) / 2 + self.pan_offset.y()

        # 可見區域在圖片座標中的範圍
        vx = -cx / self.zoom_factor
        vy = -cy / self.zoom_factor
        vw = self.width() / self.zoom_factor
        vh = self.height() / self.zoom_factor

        # 歸一化
        nx = vx / img_w
        ny = vy / img_h
        nw = vw / img_w
        nh = vh / img_h

        return QRectF(nx, ny, nw, nh)

    def update_display(self):
        if self.original_image is None or self.mask_array is None:
            return
        
        result_array = self._build_composited_array()
        if result_array is None:
            return
        h, w = result_array.shape[:2]
        
        result_img = QImage(result_array.data, w, h, w * 3, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(result_img)
        
        scaled_size = pixmap.size() * self.zoom_factor
        scaled_pixmap = pixmap.scaled(scaled_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.display_pixmap = scaled_pixmap
        
        final_pixmap = QPixmap(self.size())
        final_pixmap.fill(Qt.black)
        
        painter = QPainter(final_pixmap)
        x = (self.width() - scaled_pixmap.width()) // 2 + self.pan_offset.x()
        y = (self.height() - scaled_pixmap.height()) // 2 + self.pan_offset.y()
        painter.drawPixmap(x, y, scaled_pixmap)
        painter.end()
        
        self.setPixmap(final_pixmap)

        # 更新 minimap
        if self.parent_editor and self.parent_editor.minimap:
            composited_qimg = QImage(result_array.data, w, h, w * 3, QImage.Format_RGB888).copy()
            vr = self.get_viewport_rect()
            self.parent_editor.minimap.update_preview(composited_qimg, vr)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.mask_array is not None:
            if self.space_pressed:
                self.save_state()
                click_point = self.map_to_image(event.pos())
                self.flood_fill(click_point)
                self.update_display()
            else:
                self.save_state()
                self.drawing = True
                self.last_point = self.map_to_image(event.pos())
                self.draw_point(self.last_point)
                self.update_display()
        elif event.button() == Qt.RightButton:
            self.panning = True
            self.last_point = event.pos()

    def mouseMoveEvent(self, event):
        if self.drawing and self.mask_array is not None and not self.space_pressed:
            current_point = self.map_to_image(event.pos())
            self.draw_line(self.last_point, current_point)
            self.last_point = current_point
            self.update_display()
        elif self.panning:
            delta = event.pos() - self.last_point
            self.pan_offset += delta
            self.last_point = event.pos()
            self.update_display()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = False
        elif event.button() == Qt.RightButton:
            self.panning = False
    
    def map_to_image(self, pos):
        if not self.display_pixmap or self.mask_array is None:
            return QPoint(0, 0)
        
        scaled_w = int(self.mask_array.shape[1] * self.zoom_factor)
        scaled_h = int(self.mask_array.shape[0] * self.zoom_factor)
        
        offset_x = (self.width() - scaled_w) // 2 + self.pan_offset.x()
        offset_y = (self.height() - scaled_h) // 2 + self.pan_offset.y()
        
        img_x = int((pos.x() - offset_x) / self.zoom_factor)
        img_y = int((pos.y() - offset_y) / self.zoom_factor)
        
        img_x = max(0, min(img_x, self.mask_array.shape[1] - 1))
        img_y = max(0, min(img_y, self.mask_array.shape[0] - 1))
        
        return QPoint(img_x, img_y)
    
    def flood_fill(self, point):
        x, y = point.x(), point.y()
        h, w = self.mask_array.shape
        if not (0 <= x < w and 0 <= y < h):
            return
        target_value = self.mask_array[y, x]
        new_value = 0 if self.eraser_mode else self.current_label
        if target_value == new_value:
            return
        flood_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
        cv2.floodFill(self.mask_array, flood_mask, (x, y), int(new_value),
                       loDiff=0, upDiff=0, flags=4)
    
    def draw_point(self, point):
        x, y = point.x(), point.y()
        h, w = self.mask_array.shape
        radius = self.brush_size // 2
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx*dx + dy*dy <= radius*radius:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        self.mask_array[ny, nx] = 0 if self.eraser_mode else self.current_label
    
    def draw_line(self, start, end):
        x0, y0 = start.x(), start.y()
        x1, y1 = end.x(), end.y()
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.draw_point(QPoint(x0, y0))
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def navigate_to(self, nx, ny):
        """將畫布中心移動到歸一化座標 (nx, ny) 處"""
        if self.mask_array is None:
            return
        img_w = self.mask_array.shape[1]
        img_h = self.mask_array.shape[0]

        # 目標圖片座標
        target_x = nx * img_w
        target_y = ny * img_h

        # 希望 target 出現在畫布正中央
        # canvas_center = img_offset + target * zoom
        # img_offset = (canvas_w/2 - scaled_w/2) + pan_x
        # 我們要讓 target 對應到 canvas 中心
        # canvas_w/2 = (canvas_w - scaled_w)/2 + pan_x + target_x * zoom
        # pan_x = canvas_w/2 - (canvas_w - scaled_w)/2 - target_x * zoom
        # pan_x = scaled_w/2 - target_x * zoom

        scaled_w = img_w * self.zoom_factor
        scaled_h = img_h * self.zoom_factor

        pan_x = scaled_w / 2 - target_x * self.zoom_factor
        pan_y = scaled_h / 2 - target_y * self.zoom_factor

        self.pan_offset = QPoint(int(pan_x), int(pan_y))
        self.update_display()


class MaskEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mask Editor - 編輯既有遮罩")
        self.setGeometry(100, 100, 1400, 800)
        
        self.images_dir = None
        self.masks_dir = None
        self.masks_result_dir = None
        self.current_img_path = None
        self.current_mask_path = None
        self.current_result_path = None
        
        self.all_image_paths = []
        self.current_patient_images = []
        self.current_slice_index = 0
        self.saved_patients = set()
        self.edited_masks = {}

        self.minimap = None  # 先設為 None, init_ui 裡建立
        
        self.init_ui()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # ── 左側：檔案樹 ──
        left_panel = QVBoxLayout()
        
        load_btn = QPushButton("載入資料夾")
        load_btn.clicked.connect(self.load_directories)
        left_panel.addWidget(load_btn)
        
        self.status_label = QLabel("尚未載入資料夾")
        self.status_label.setWordWrap(True)
        left_panel.addWidget(self.status_label)
        
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabel("檔案")
        self.file_tree.itemClicked.connect(self.on_file_selected)
        left_panel.addWidget(self.file_tree)
        
        # ── 中間：畫布 ──
        self.canvas = MaskCanvas(parent_editor=self)
        
        # ── 右側：工具面板（放進 ScrollArea）──
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_widget = QWidget()
        right_panel = QVBoxLayout(right_widget)
        right_panel.setSpacing(4)
        right_scroll.setWidget(right_widget)
        
        # --- Label 選擇器 ---
        label_group = QGroupBox("Label選擇")
        label_layout = QVBoxLayout()
        
        label_names = [
            'L5', 'L4', 'L3', 'L2', 'L1',
            'T12', 'T11', 'T10', 'T9',
            'spinal canal',
            'L5-S1', 'L4-L5', 'L3-L4', 'L2-L3', 'L1-L2',
            'T12-L1', 'T11-T12', 'T10-T11', 'T9-T10'
        ]
        
        self.label_combo = QComboBox()
        colors = plt.cm.tab20.colors
        for i, name in enumerate(label_names):
            display_text = f"Label {i+1}: {name}"
            self.label_combo.addItem(display_text, i+1)
            c = colors[(i+1) % 20]
            q_color = QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255))
            self.label_combo.setItemData(i, QBrush(q_color), Qt.BackgroundRole)
        self.label_combo.addItem("Label 20", 20)
        self.label_combo.currentIndexChanged.connect(self.change_label)
        label_layout.addWidget(self.label_combo)
        
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(100, 30)
        self.color_preview.setStyleSheet("border: 1px solid black;")
        self.update_color_preview()
        label_layout.addWidget(self.color_preview)
        
        label_group.setLayout(label_layout)
        right_panel.addWidget(label_group)
        
        # --- 工具模式 ---
        mode_group = QGroupBox("當前工具模式")
        mode_layout = QVBoxLayout()
        
        self.brush_btn = QPushButton("🖌 畫筆模式 (W)")
        self.brush_btn.setCheckable(True)
        self.brush_btn.setChecked(True)
        self.brush_btn.clicked.connect(lambda: self.set_tool_mode('brush'))
        mode_layout.addWidget(self.brush_btn)
        
        self.eraser_btn = QPushButton("🧹 橡皮擦模式 (S)")
        self.eraser_btn.setCheckable(True)
        self.eraser_btn.clicked.connect(lambda: self.set_tool_mode('eraser'))
        mode_layout.addWidget(self.eraser_btn)
        
        self.mode_hint_label = QLabel("空白鍵+左鍵: 填色 | A/D: 切換Label")
        self.mode_hint_label.setStyleSheet("color: gray; font-size: 10px;")
        self.mode_hint_label.setWordWrap(True)
        mode_layout.addWidget(self.mode_hint_label)
        
        mode_group.setLayout(mode_layout)
        right_panel.addWidget(mode_group)
        
        # Undo
        undo_btn = QPushButton("返回上一步 (Undo)")
        undo_btn.clicked.connect(self.undo_action)
        right_panel.addWidget(undo_btn)
        
        # 筆刷大小
        right_panel.addWidget(QLabel("筆刷大小:"))
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, 30)
        self.size_slider.setValue(10)
        self.size_slider.valueChanged.connect(self.change_brush_size)
        right_panel.addWidget(self.size_slider)
        self.size_label = QLabel("10")
        right_panel.addWidget(self.size_label)
        
        # Mask透明度
        right_panel.addWidget(QLabel("顯示透明度:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(50)
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        right_panel.addWidget(self.opacity_slider)
        self.opacity_label = QLabel("50%")
        right_panel.addWidget(self.opacity_label)
        
        # --- 影像顯示調整 ---
        img_group = QGroupBox("影像顯示調整")
        img_layout = QVBoxLayout()
        
        img_layout.addWidget(QLabel("對比度:"))
        self.contrast_slider = QSlider(Qt.Horizontal)
        self.contrast_slider.setMinimum(50)
        self.contrast_slider.setMaximum(200)
        self.contrast_slider.setValue(100)
        self.contrast_slider.valueChanged.connect(self.change_contrast)
        img_layout.addWidget(self.contrast_slider)
        self.contrast_label = QLabel("1.0x")
        img_layout.addWidget(self.contrast_label)
        
        img_layout.addWidget(QLabel("亮度:"))
        self.brightness_slider = QSlider(Qt.Horizontal)
        self.brightness_slider.setMinimum(-100)
        self.brightness_slider.setMaximum(100)
        self.brightness_slider.setValue(0)
        self.brightness_slider.valueChanged.connect(self.change_brightness)
        img_layout.addWidget(self.brightness_slider)
        self.brightness_label = QLabel("0")
        img_layout.addWidget(self.brightness_label)
        
        reset_image_btn = QPushButton("重置影像顯示")
        reset_image_btn.clicked.connect(self.reset_image_adjustments)
        img_layout.addWidget(reset_image_btn)
        
        img_group.setLayout(img_layout)
        right_panel.addWidget(img_group)
        
        # # ═══════════════════════════════════════════
        # # ★ 新增：縮放工具條 + MiniMap 預覽
        # # ═══════════════════════════════════════════
        # zoom_group = QGroupBox("縮放與導覽")
        # zoom_layout = QVBoxLayout()
        # zoom_layout.setSpacing(4)

        # # 縮放按鈕列
        # zoom_btn_layout = QHBoxLayout()
        # self.zoom_out_btn = QPushButton("−")
        # self.zoom_out_btn.setFixedWidth(36)
        # self.zoom_out_btn.clicked.connect(self.zoom_out)
        # zoom_btn_layout.addWidget(self.zoom_out_btn)

        # self.zoom_slider = QSlider(Qt.Horizontal)
        # self.zoom_slider.setMinimum(10)   # 0.1x
        # self.zoom_slider.setMaximum(500)  # 5.0x
        # self.zoom_slider.setValue(150)     # 初始 1.5x
        # self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        # zoom_btn_layout.addWidget(self.zoom_slider)

        # self.zoom_in_btn = QPushButton("+")
        # self.zoom_in_btn.setFixedWidth(36)
        # self.zoom_in_btn.clicked.connect(self.zoom_in)
        # zoom_btn_layout.addWidget(self.zoom_in_btn)
        # zoom_layout.addLayout(zoom_btn_layout)

        # self.zoom_pct_label = QLabel("150%")
        # self.zoom_pct_label.setAlignment(Qt.AlignCenter)
        # zoom_layout.addWidget(self.zoom_pct_label)

        # # Fit / 100% / Reset 快捷按鈕
        # zoom_preset_layout = QHBoxLayout()
        # fit_btn = QPushButton("Fit")
        # fit_btn.setToolTip("適合畫布大小")
        # fit_btn.clicked.connect(self.zoom_fit)
        # zoom_preset_layout.addWidget(fit_btn)

        # btn_100 = QPushButton("100%")
        # btn_100.clicked.connect(lambda: self._set_zoom(1.0))
        # zoom_preset_layout.addWidget(btn_100)

        # btn_150 = QPushButton("150%")
        # btn_150.clicked.connect(lambda: self._set_zoom(1.5))
        # zoom_preset_layout.addWidget(btn_150)

        # btn_200 = QPushButton("200%")
        # btn_200.clicked.connect(lambda: self._set_zoom(2.0))
        # zoom_preset_layout.addWidget(btn_200)
        # zoom_layout.addLayout(zoom_preset_layout)

        # # MiniMap
        # zoom_layout.addWidget(QLabel("預覽導覽圖:"))
        # self.minimap = MiniMap()
        # self.minimap.navigation_requested.connect(self._on_minimap_navigate)
        # zoom_layout.addWidget(self.minimap, alignment=Qt.AlignCenter)

        # zoom_group.setLayout(zoom_layout)
        # right_panel.addWidget(zoom_group)
        # # ═══════════════════════════════════════════

        # 重置 / 保存按鈕
        keep_label10_btn = QPushButton("清空Mask（僅保留 Label 10: spinal canal）")
        keep_label10_btn.setStyleSheet("color: #CC6600;")
        keep_label10_btn.clicked.connect(self.keep_only_label10)
        right_panel.addWidget(keep_label10_btn)
        
        reset_btn = QPushButton("重置到原始Mask")
        reset_btn.clicked.connect(self.reset_mask)
        right_panel.addWidget(reset_btn)
        
        reset_view_btn = QPushButton("重置視圖")
        reset_view_btn.clicked.connect(self.reset_view)
        right_panel.addWidget(reset_view_btn)
        
        save_btn = QPushButton("保存整個Patient")
        save_btn.setStyleSheet("font-weight: bold; padding: 10px;")
        save_btn.clicked.connect(self.save_mask)
        right_panel.addWidget(save_btn)
        
        right_panel.addStretch()
        
        # 組合布局
        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(self.canvas, 3)
        main_layout.addWidget(right_scroll, 1)

    # ── 縮放相關方法 ──

    def sync_zoom_slider(self):
        """將 canvas 的 zoom_factor 同步到滑桿（避免遞迴觸發）"""
        self.zoom_slider.blockSignals(True)
        val = int(self.canvas.zoom_factor * 100)
        val = max(self.zoom_slider.minimum(), min(val, self.zoom_slider.maximum()))
        self.zoom_slider.setValue(val)
        self.zoom_slider.blockSignals(False)
        self.zoom_pct_label.setText(f"{val}%")

    def _on_zoom_slider(self, value):
        self.canvas.zoom_factor = value / 100.0
        self.zoom_pct_label.setText(f"{value}%")
        self.canvas.update_display()

    def zoom_in(self):
        self._set_zoom(self.canvas.zoom_factor * 1.25)

    def zoom_out(self):
        self._set_zoom(self.canvas.zoom_factor / 1.25)

    def _set_zoom(self, factor):
        factor = max(0.1, min(factor, 5.0))
        self.canvas.zoom_factor = factor
        self.canvas.update_display()
        self.sync_zoom_slider()

    def zoom_fit(self):
        """自動調整縮放讓圖片剛好填滿畫布"""
        if self.canvas.mask_array is None:
            return
        img_h, img_w = self.canvas.mask_array.shape
        canvas_w = self.canvas.width()
        canvas_h = self.canvas.height()
        if img_w == 0 or img_h == 0:
            return
        factor = min(canvas_w / img_w, canvas_h / img_h) * 0.95
        self.canvas.pan_offset = QPoint(0, 0)
        self._set_zoom(factor)

    def _on_minimap_navigate(self, nx, ny):
        """minimap 點擊 → 移動畫布"""
        self.canvas.navigate_to(nx, ny)

    # ── 其餘方法（與原版相同）──
    
    def update_mode_display(self):
        if self.canvas.space_pressed:
            self.mode_hint_label.setText("🪣 填色模式啟動中")
            self.mode_hint_label.setStyleSheet("color: #00AA00; font-size: 11px; font-weight: bold;")
        else:
            self.mode_hint_label.setText("空白鍵+左鍵: 填色 | A/D: 切換Label")
            self.mode_hint_label.setStyleSheet("color: gray; font-size: 10px;")
    
    def load_directories(self):
        images_dir = QFileDialog.getExistingDirectory(self, "選擇images資料夾")
        if not images_dir:
            return
        self.images_dir = Path(images_dir)
        self.masks_dir = self.images_dir.parent / "masks"
        self.masks_result_dir = self.images_dir.parent / "masks_result"
        self.edited_masks.clear()
        
        if not self.masks_result_dir.exists():
            self.masks_result_dir.mkdir(parents=True)
        if not self.masks_dir.exists():
            QMessageBox.warning(self, "警告", 
                              f"找不到masks資料夾: {self.masks_dir}\n將建立空白遮罩")
            self.masks_dir.mkdir(parents=True)
        
        self.saved_patients.clear()
        if self.masks_result_dir.exists():
            for patient_dir in self.masks_result_dir.iterdir():
                if patient_dir.is_dir():
                    self.saved_patients.add(patient_dir.name)
        
        self.status_label.setText(
            f"✓ images: {self.images_dir.name}\n"
            f"✓ masks: {self.masks_dir.name}\n"
            f"✓ 結果: masks_result\n"
            f"已保存: {len(self.saved_patients)} 個patient"
        )
        self.populate_tree()
    
    def populate_tree(self):
        self.file_tree.clear()
        self.all_image_paths = []
        for id_dir in sorted(self.images_dir.iterdir()):
            if not id_dir.is_dir():
                continue
            id_item = QTreeWidgetItem(self.file_tree, [id_dir.name])
            if id_dir.name in self.saved_patients:
                id_item.setBackground(0, QColor(200, 255, 200))
            for img_file in sorted(id_dir.glob("*.png")):
                img_item = QTreeWidgetItem(id_item, [img_file.name])
                img_item.setData(0, Qt.UserRole, str(img_file))
                self.all_image_paths.append(str(img_file))
    
    def on_file_selected(self, item, column):
        img_path = item.data(0, Qt.UserRole)
        if not img_path:
            return
        img_path = Path(img_path)
        new_patient_id = img_path.parent.name
        
        if self.current_img_path:
            old_patient_id = self.current_img_path.parent.name
            if old_patient_id != new_patient_id:
                old_edits = [k for k in self.edited_masks.keys() if old_patient_id in k]
                if old_edits:
                    reply = QMessageBox.question(
                        self, "保存提示",
                        f"Patient {old_patient_id} 有 {len(old_edits)} 個未保存的編輯。\n是否先保存？",
                        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
                    )
                    if reply == QMessageBox.Yes:
                        old_img = Path(old_edits[0])
                        temp_current = self.current_img_path
                        self.current_img_path = old_img
                        self.save_mask()
                        self.current_img_path = temp_current
                    elif reply == QMessageBox.Cancel:
                        return
        
        if self.current_img_path and self.canvas.mask_array is not None:
            if self.canvas.original_mask is not None:
                if not np.array_equal(self.canvas.mask_array, self.canvas.original_mask):
                    self.edited_masks[str(self.current_img_path)] = self.canvas.mask_array.copy()
        
        self.current_img_path = img_path
        current_patient_dir = img_path.parent
        self.current_patient_images = sorted([str(f) for f in current_patient_dir.glob("*.png")])
        try:
            self.current_slice_index = self.current_patient_images.index(str(img_path))
        except ValueError:
            self.current_slice_index = 0
        
        relative_path = img_path.relative_to(self.images_dir)
        self.current_mask_path = self.masks_dir / relative_path
        self.current_result_path = self.masks_result_dir / relative_path
        
        self.canvas.load_images(str(self.current_img_path), str(self.current_mask_path))
        
        if str(img_path) in self.edited_masks:
            self.canvas.mask_array = self.edited_masks[str(img_path)].copy()
            self.canvas.update_display()
        
        if str(img_path) in self.edited_masks:
            mask_status = "暫存編輯版本"
        elif self.current_result_path.exists():
            mask_status = "masks_result (已保存)"
        elif self.current_mask_path.exists():
            mask_status = "masks (原始)"
        else:
            mask_status = "空白"
        
        patient_id = current_patient_dir.name
        edited_count = len([k for k in self.edited_masks.keys() if patient_id in k])
        
        self.status_label.setText(
            f"✓ images: {self.images_dir.name}\n"
            f"✓ mask來源: {mask_status}\n"
            f"✓ 結果: masks_result\n"
            f"Patient: {patient_id}\n"
            f"Slice: {self.current_slice_index + 1}/{len(self.current_patient_images)}\n"
            f"已編輯: {edited_count} 個slice"
        )
        # 同步縮放滑桿
        self.sync_zoom_slider()
    
    def set_tool_mode(self, mode):
        self.brush_btn.setChecked(mode == 'brush')
        self.eraser_btn.setChecked(mode == 'eraser')
        self.canvas.eraser_mode = (mode == 'eraser')
    
    def undo_action(self):
        if not self.canvas.undo():
            QMessageBox.information(self, "提示", "沒有可以撤銷的操作")
    
    def keep_only_label10(self):
        """清空mask，只保留label 10 (spinal canal)"""
        if self.canvas.mask_array is None:
            return
        has_label10 = np.any(self.canvas.mask_array == 10)
        reply = QMessageBox.question(
            self, "確認",
            "確定要清空mask，只保留 Label 10 (spinal canal) 嗎？\n"
            f"{'目前mask中有 Label 10 的區域。' if has_label10 else '⚠ 目前mask中沒有 Label 10，執行後將變成空白遮罩。'}\n\n"
            "此操作可用 Ctrl+Z 復原。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.canvas.keep_only_label(10)
            print("✓ 已清空mask，僅保留 Label 10 (spinal canal)")
    
    def reset_mask(self):
        reply = QMessageBox.question(
            self, "確認", 
            "確定要重置到原始mask嗎？\n這將清除所有修改，恢復到載入時的狀態。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.canvas.reset_to_original()
    
    def reset_view(self):
        self.canvas.reset_view()
        self.sync_zoom_slider()
    
    def change_label(self, index):
        self.canvas.current_label = self.label_combo.currentData()
        self.update_color_preview()
    
    def prev_label(self):
        """切換到上一個 label (A鍵)"""
        idx = self.label_combo.currentIndex()
        if idx > 0:
            self.label_combo.setCurrentIndex(idx - 1)
    
    def next_label(self):
        """切換到下一個 label (D鍵)"""
        idx = self.label_combo.currentIndex()
        if idx < self.label_combo.count() - 1:
            self.label_combo.setCurrentIndex(idx + 1)
    
    def update_color_preview(self):
        label = self.canvas.current_label
        color = self.canvas.label_colors[label]
        self.color_preview.setStyleSheet(
            f"background-color: rgb({color[0]}, {color[1]}, {color[2]}); border: 1px solid black;"
        )
    
    def change_brush_size(self, value):
        self.canvas.brush_size = value
        self.size_label.setText(str(value))
    
    def change_opacity(self, value):
        self.canvas.mask_opacity = value / 100.0
        self.opacity_label.setText(f"{value}%")
        self.canvas.update_display()
    
    def change_contrast(self, value):
        contrast = value / 100.0
        self.canvas.contrast_factor = contrast
        self.contrast_label.setText(f"{contrast:.1f}x")
        self.canvas.update_display()
    
    def change_brightness(self, value):
        self.canvas.brightness_offset = value
        self.brightness_label.setText(str(value))
        self.canvas.update_display()
    
    def reset_image_adjustments(self):
        self.contrast_slider.setValue(100)
        self.brightness_slider.setValue(0)
    
    def save_mask(self):
        if self.current_result_path is None or self.canvas.mask_array is None:
            return
        
        if self.current_img_path and self.canvas.mask_array is not None:
            if self.canvas.original_mask is not None:
                if not np.array_equal(self.canvas.mask_array, self.canvas.original_mask):
                    self.edited_masks[str(self.current_img_path)] = self.canvas.mask_array.copy()
        
        current_patient_id = self.current_img_path.parent.name
        patient_dir = self.images_dir / current_patient_id
        patient_images = sorted(patient_dir.glob("*.png"))
        
        saved_count = 0
        edited_saved = 0
        kept_saved = 0
        copied_saved = 0
        
        for img_file in patient_images:
            relative_path = img_file.relative_to(self.images_dir)
            mask_src = self.masks_dir / relative_path
            mask_dst = self.masks_result_dir / relative_path
            mask_dst.parent.mkdir(parents=True, exist_ok=True)
            
            if str(img_file) in self.edited_masks:
                cv2.imwrite(str(mask_dst), self.edited_masks[str(img_file)])
                saved_count += 1
                edited_saved += 1
            elif mask_dst.exists():
                saved_count += 1
                kept_saved += 1
            elif mask_src.exists():
                shutil.copy2(str(mask_src), str(mask_dst))
                saved_count += 1
                copied_saved += 1
        
        keys_to_remove = [k for k in self.edited_masks.keys() if current_patient_id in k]
        for key in keys_to_remove:
            del self.edited_masks[key]
        
        if self.canvas.mask_array is not None:
            self.canvas.original_mask = self.canvas.mask_array.copy()
        
        self.saved_patients.add(current_patient_id)
        self.update_tree_colors()
        
        self.status_label.setText(
            f"✓ images: {self.images_dir.name}\n"
            f"✓ masks: {self.masks_dir.name}\n"
            f"✓ 結果: masks_result\n"
            f"Patient: {current_patient_id}\n"
            f"Slice: {self.current_slice_index + 1}/{len(self.current_patient_images)}\n"
            f"已編輯: 0 個slice (已保存)"
        )
        
        QMessageBox.information(
            self, "保存成功", 
            f"已保存 patient {current_patient_id}:\n\n"
            f"總共: {saved_count} 個 slice\n"
            f"本次編輯: {edited_saved} 個\n"
            f"保留已存在: {kept_saved} 個\n"
            f"複製原始: {copied_saved} 個"
        )
    
    def update_tree_colors(self):
        root = self.file_tree.invisibleRootItem()
        for i in range(root.childCount()):
            patient_item = root.child(i)
            patient_id = patient_item.text(0)
            if patient_id in self.saved_patients:
                patient_item.setBackground(0, QColor(200, 255, 200))
            else:
                patient_item.setBackground(0, QColor(255, 255, 255))
    
    def sync_tree_selection(self):
        """根據目前 current_img_path 同步左側檔案樹的選取與展開"""
        if self.current_img_path is None:
            return
        target_path = str(self.current_img_path)
        root = self.file_tree.invisibleRootItem()
        for i in range(root.childCount()):
            patient_item = root.child(i)
            for j in range(patient_item.childCount()):
                slice_item = patient_item.child(j)
                if slice_item.data(0, Qt.UserRole) == target_path:
                    # 展開 patient 節點並選取對應 slice
                    self.file_tree.blockSignals(True)
                    patient_item.setExpanded(True)
                    self.file_tree.setCurrentItem(slice_item)
                    self.file_tree.scrollToItem(slice_item)
                    self.file_tree.blockSignals(False)
                    return
    
    def prev_slice(self):
        if not self.current_patient_images:
            return
        self.current_slice_index = (self.current_slice_index - 1) % len(self.current_patient_images)
        self.load_slice_by_index(self.current_slice_index)
    
    def next_slice(self):
        if not self.current_patient_images:
            return
        self.current_slice_index = (self.current_slice_index + 1) % len(self.current_patient_images)
        self.load_slice_by_index(self.current_slice_index)
    
    def load_slice_by_index(self, index):
        if self.current_img_path and self.canvas.mask_array is not None:
            if self.canvas.original_mask is not None:
                if not np.array_equal(self.canvas.mask_array, self.canvas.original_mask):
                    self.edited_masks[str(self.current_img_path)] = self.canvas.mask_array.copy()
        
        if 0 <= index < len(self.current_patient_images):
            img_path = Path(self.current_patient_images[index])
            self.current_img_path = img_path
            
            relative_path = img_path.relative_to(self.images_dir)
            self.current_mask_path = self.masks_dir / relative_path
            self.current_result_path = self.masks_result_dir / relative_path
            
            self.canvas.load_images(str(self.current_img_path), str(self.current_mask_path))
            
            if str(img_path) in self.edited_masks:
                self.canvas.mask_array = self.edited_masks[str(img_path)].copy()
                self.canvas.update_display()
            
            if str(img_path) in self.edited_masks:
                mask_status = "暫存編輯版本"
            elif self.current_result_path.exists():
                mask_status = "masks_result (已保存)"
            elif self.current_mask_path.exists():
                mask_status = "masks (原始)"
            else:
                mask_status = "空白"
            
            patient_id = img_path.parent.name
            edited_count = len([k for k in self.edited_masks.keys() if patient_id in k])
            
            self.status_label.setText(
                f"✓ images: {self.images_dir.name}\n"
                f"✓ mask來源: {mask_status}\n"
                f"✓ 結果: masks_result\n"
                f"Patient: {patient_id}\n"
                f"Slice: {index + 1}/{len(self.current_patient_images)}\n"
                f"已編輯: {edited_count} 個slice"
            )
            self.sync_zoom_slider()
            self.sync_tree_selection()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    editor = MaskEditor()
    editor.show()
    sys.exit(app.exec())