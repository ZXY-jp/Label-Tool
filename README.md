# Mask Editor — 脊椎 MRI 分割遮罩編輯工具

以 PySide6 撰寫的桌面工具，用來**逐 slice 檢視與修正既有的分割遮罩（mask）**
並以「整個 patient」為單位輸出修正結果。
原始 mask 永遠不會被覆寫，所有修正都寫到獨立的 `masks_result/` 資料夾。
<img width="1312" height="811" alt="image" src="https://github.com/user-attachments/assets/bd3a25c7-ac2a-4768-9cbb-d3cec95a66f5" />

---

## 1. 環境需求

| 套件 | 用途 |
|---|---|
| Python 3.9+ | |
| `PySide6` | GUI |
| `numpy` | 遮罩陣列運算 |
| `opencv-python` | 讀寫影像、flood fill |
| `matplotlib` | 取用 `tab20` 色盤 |


```bash
pip install PySide6 numpy opencv-python matplotlib 
```

## 2. 啟動

```bash
python mask_editor.py
```

點左上角 **「載入資料夾」**，選擇 **`images` 資料夾本身**（不是它的上一層）。

---

## 3. 資料夾結構（必須遵守）

程式以「使用者選的 `images` 資料夾」為基準，自動在**同一層**尋找 `masks/` 與 `masks_result/`：

```
dataset_root/                 ← images 的上一層
├── images/                   ← 載入時選這個資料夾
│   ├── P001/                 ← 一個 patient 一個子資料夾
│   │   ├── slice_001.png
│   │   ├── slice_002.png
│   │   └── ...
│   └── P002/
│       └── ...
├── masks/                    ← 原始 mask（唯讀，程式不會修改）
│   ├── P001/
│   │   ├── slice_001.png     ← 與 images 相同的相對路徑與檔名
│   │   └── ...
│   └── P002/
└── masks_result/             ← 編輯結果輸出（不存在時自動建立）
    └── P001/
        └── ...
```

### 對應規則

- **images 與 mask 以「相對路徑 + 檔名」一對一對應**：
  `images/P001/slice_001.png` ↔ `masks/P001/slice_001.png` ↔ `masks_result/P001/slice_001.png`
- 只掃描 **`images/<patient>/*.png` 這一層**：
  - `images/` 底下直接放的檔案會被忽略
  - patient 資料夾內更深的子資料夾不會被讀取
  - 非 `.png` 檔（`.jpg`、`.tif`、`.nii.gz`、DICOM…）不會被列出
- `masks/` 不存在時會跳出警告並自動建立空資料夾，所有 slice 以空白遮罩開始。

### 命名建議

| 規則 | 原因 |
|---|---|
| slice 檔名請**補零**（`slice_001` 而非 `slice_1`） | slice 順序依**字串排序**，`slice_10` 會排在 `slice_2` 前面 |
| patient ID 請用**固定長度、彼此不互為子字串**的名稱（如 `P001`、`P010`） | 程式以 `patient_id in 路徑字串` 判斷某 slice 屬於哪個 patient；`1` 會誤配到 `10`、`11` 的路徑，造成未保存提示與保存範圍錯誤 |
| patient ID 不要和路徑中其他資料夾名稱重複（例如不要叫 `images`、`masks`） | 同上，子字串比對會誤判 |

---

## 4. 讀檔規則

### 4.1 影像（images）

- 以 `cv2.imread(..., IMREAD_UNCHANGED)` 讀取，支援：
  - **8-bit 灰階 / 彩色 PNG**：直接顯示（彩色依 OpenCV 慣例視為 BGR）
  - **16-bit PNG**：每張 slice **各自**做 min–max 正規化到 0–255 後顯示
- 正規化、對比度、亮度只影響**顯示**，不會修改原始影像檔。

### 4.2 遮罩（mask）讀取優先順序

開啟某張 slice 時，mask 來源依下列順序決定（左側狀態欄會顯示「mask來源」）：

| 優先 | 來源 | 狀態顯示 |
|---|---|---|
| 1 | 記憶體中本次未保存的編輯 | `暫存編輯版本` |
| 2 | `masks_result/<相對路徑>` | `masks_result (已保存)` |
| 3 | `masks/<相對路徑>` | `masks (原始)` |
| 4 | 以上皆無 → 與影像同尺寸的全 0 遮罩 | `空白` |

### 4.3 遮罩格式

- **單通道 PNG，像素值即 label 編號**（0 = 背景，1–20 = 各結構），以 `uint8` 處理。
  用一般看圖軟體開會幾乎全黑，屬正常現象。
- 若讀到 3 通道 mask，只取**第 0 通道**（OpenCV 的 B 通道）。
- **mask 尺寸必須與影像相同**；程式沒有檢查尺寸，不一致時疊圖或繪製會出錯。

### 4.4 Label 定義
- 與SPIDER 資料集採相同的標記方式，但要用在其他的Label也OK
- <img width="221" height="803" alt="image" src="https://github.com/user-attachments/assets/41ee5de0-9cdf-48cc-bf7e-3479f0e0c22d" />


| Label | 結構 | Label | 結構 |
|---|---|---|---|
| 1 | L5 | 11 | L5-S1 |
| 2 | L4 | 12 | L4-L5 |
| 3 | L3 | 13 | L3-L4 |
| 4 | L2 | 14 | L2-L3 |
| 5 | L1 | 15 | L1-L2 |
| 6 | T12 | 16 | T12-L1 |
| 7 | T11 | 17 | T11-T12 |
| 8 | T10 | 18 | T10-T11 |
| 9 | T9 | 19 | T9-T10 |
| 10 | spinal canal|

顯示顏色取自 matplotlib `tab20` 色盤（label *i* 使用第 *i* 色）。

---

## 5. 保存規則

按 **「保存整個Patient」** 時，對**目前 patient 的每一張 slice** 依序判斷：

| 情況 | 動作 |
|---|---|
| 本次有編輯（在記憶體中） | 以 `cv2.imwrite` 寫入 `masks_result/` |
| 本次未編輯，但 `masks_result/` 已有檔案 | 保留不動 |
| 本次未編輯，`masks_result/` 沒有，`masks/` 有 | 從 `masks/` 複製一份（`shutil.copy2`） |
| 以上皆無（無原始 mask 且未編輯） | **不產生檔案** |

保存後：

- 該 patient 在左側樹狀清單顯示**綠底**
- 下次載入資料夾時，`masks_result/` 底下已存在的 patient 資料夾都會被標成綠底
- 已保存的 mask 成為該 slice 的新「原始狀態」（「重置到原始Mask」會回到這個版本）

> **注意：** 編輯在保存前只存在記憶體中。直接關閉視窗**不會**提示，未保存的編輯會遺失。

---

## 6. 操作方式

> 鍵盤快捷鍵需要畫布取得焦點，請先在影像上點一下。

### 滑鼠

| 操作 | 功能 |
|---|---|
| 左鍵拖曳 | 用目前 label 塗畫（橡皮擦模式則塗成 0） |
| 空白鍵 + 左鍵 | 填色：將點擊處**相同 label 值、4-連通**的區域整片換成目前 label |
| 右鍵拖曳 | 平移影像 |
| 滾輪 | 上/下一張 slice（到頭尾會循環） |
| Ctrl + 滾輪 | 縮放（0.1×–10×） |

### 鍵盤

| 鍵 | 功能 |
|---|---|
| `W` | 畫筆模式 |
| `S` | 橡皮擦模式 |
| `A` / `D` | 上一個 / 下一個 label |
| `Ctrl + Z` | 復原（每張 slice 最多 50 步，切換 slice 後清空） |

### 右側面板

- **筆刷大小** 1–30 px（圓形筆刷，直徑）
- **顯示透明度** 0–100%：遮罩疊色強度，僅影響顯示
- **對比度 / 亮度**：僅影響顯示
- **清空 Mask（僅保留 Label 10）**：刪除 spinal canal 以外的所有 label，可 Ctrl+Z 復原
- **重置到原始 Mask**：回到這張 slice 載入時（或上次保存後）的狀態
- **重置視圖**：縮放回 1.5×、平移歸零
