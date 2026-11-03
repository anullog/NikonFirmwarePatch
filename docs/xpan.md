# XPan 影像区域（原型） / XPan image area (prototype)

[简体中文](#简体中文) · [English](#english)

## 简体中文

`--features xpan` 在**照片拍摄菜单 → 影像区域 → 选择影像区域**中加入 **XPan (36×13)**，即 65:24 的全宽画幅（8256×3048）。

**原型，第 1 阶段。** 未经审核或实机测试，配方为推导所得。目前只加入菜单项：选择 XPan 后，相机按 16:9 拍摄，取景和文件均为 16:9。交互菜单中为选项 7，从不包含在选项 5 中。

### 设计

XPan 在相机内部按原厂 16:9 影像区域（设定值 4）运行，另加一个 XPan 标记。传感器读出、自动对焦、测光及所有读取影像区域的代码保持原厂 16:9 行为；只有决定裁切的环节（取景黑边、输出尺寸、文件信息、回放）将在后续阶段改为 65:24。

| 阶段 | 结果 | 状态 |
|---|---|---|
| 1 | 菜单中的 XPan 行；可选择，按照片拍摄菜单库分别保存 | 模拟器通过；尚未实机测试 |
| 2 | 实时取景、EVF 和图像查看中的 65:24 不透明黑边 | 尚未开始 |
| 3 | JPEG／HEIF 8256×3048（M 6192×2286，S 4128×1524）及相应 EXIF | 尚未开始 |
| 4 | NEF 裁切、回放、i 菜单、信息显示、对焦点范围 | 尚未开始 |

### 已知限制（第 1 阶段）

- 选择影像区域行显示的图标仍为 16:9。
- 用按钮加指令拨盘或 i 菜单切换影像区域时，不会显示 XPan；在 XPan 之后用这些方式选择 16:9，菜单可能仍标记 XPan。
- 菜单设定保存到存储卡时不包含 XPan 标记。

### 构建

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.10 --tag x1 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0410.bin --features xpan --version 04.10 --tag x1
```

撤销方法：重新安装官方 C 3.11；Z8 支持降级。

### 实现

[`z8xpan/patcher.py`](../z8xpan/patcher.py)：

| 位置 | 改动 |
|---|---|
| GUI 状态 2Ah | 回调和页面记录指向新的六行页面（原厂五行加 XPan，XPan 行复制 16:9 行，项目 35BBh） |
| 各行 OK 动作 | 原厂各行先清除标记再执行原厂设定；XPan 设为 16:9 并置标记 |
| 进入回调 | 光标和选中标记沿用原厂映射；16:9 且有标记时为 XPan |
| 可用性回调 | XPan 与 16:9 在相同条件下可用（原厂条件 1Bh） |
| `410162c4` | 逐项槽复制的 32 位分支：复制 07000603h 时一并复制标记字节，使每个照片拍摄菜单库保存自己的选择 |

标记位于 `A03D482C2`，是照片拍摄菜单库组中 DS 07000602h 与 07000603h 之间的填充字节。

## English

`--features xpan` adds **XPan (36×13)** to **Photo shooting menu → Image area → Choose image area**: a full-width 65:24 frame (8256×3048).

**Prototype, milestone 1.** Not reviewed or tested on a camera; its recipes are derived. So far it adds only the menu row: with XPan selected, the camera shoots 16:9, and both the view and the files are 16:9. In the interactive menu it is option 7 and is never part of option 5.

### Design

Inside the camera, XPan runs as the stock 16:9 image area (value 4) plus an XPan flag. Sensor readout, autofocus, metering and every piece of code that reads the image area keep their stock 16:9 behaviour. Only the steps that decide the crop (view bars, output size, file data, playback) change to 65:24, in later milestones.

| Milestone | Result | Status |
|---|---|---|
| 1 | XPan row in the menu; selectable, saved per photo shooting menu bank | Emulator pass; not camera-tested |
| 2 | Opaque 65:24 bars in live view, the EVF and image review | Not started |
| 3 | JPEG/HEIF at 8256×3048 (M 6192×2286, S 4128×1524) with matching EXIF | Not started |
| 4 | NEF crop, playback, i menu, info display, AF point range | Not started |

### Known limits (milestone 1)

- The icon on the Choose image area row still shows 16:9.
- Switching the image area with a button and command dial, or from the i menu, never shows XPan. Selecting 16:9 that way after XPan may leave XPan marked in the menu.
- Saving menu settings to a card doesn't include the XPan flag.

### Build

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.10 --tag x1 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0410.bin --features xpan --version 04.10 --tag x1
```

To undo, install official C 3.11 again; Z8 accepts downgrades.

### Implementation

[`z8xpan/patcher.py`](../z8xpan/patcher.py):

| Site | Change |
|---|---|
| GUI state 2Ah | Callbacks and page record point to a new six-row page: the stock five rows plus XPan, a copy of the 16:9 row with item 35BBh |
| Row OK actions | Each stock row clears the flag, then runs its stock setter; XPan sets 16:9 and the flag |
| Entry callback | Cursor and mark follow the stock mapping, and XPan when the value is 16:9 and the flag is set |
| Enable callback | XPan is available under the same condition as 16:9 (stock condition 1Bh) |
| `410162c4` | 32-bit case of the per-item slot copy: copying 07000603h also copies the flag byte, so each photo shooting menu bank keeps its own choice |

The flag is at `A03D482C2`, the padding byte between DS 07000602h and 07000603h in the photo-bank group.
