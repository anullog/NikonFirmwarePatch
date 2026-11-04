# XPan 影像区域（原型） / XPan image area (prototype)

[简体中文](#简体中文) · [English](#english)

## 简体中文

`--features xpan` 在**照片拍摄菜单 → 影像区域 → 选择影像区域**中加入 **XPan (36×13)**，即 65:24 的全宽画幅（8256×3048）。

**原型，第 2–3 阶段。** 未经审核或实机测试，配方为推导所得。选择 XPan 后，背屏实时取景显示 65:24 黑边（模拟器已验证）；EVF 黑边和 65:24 JPEG／HEIF 尺寸已构建，但只能在相机上验证。交互菜单中为选项 7，从不包含在选项 5 中。

### 设计

XPan 在相机内部按原厂 16:9 影像区域（设定值 4）运行，另加一个 XPan 标记。传感器读出、自动对焦、测光及所有读取影像区域的代码保持原厂 16:9 行为；只有决定裁切的环节（取景黑边、输出尺寸、文件信息、回放）将在后续阶段改为 65:24。

| 阶段 | 结果 | 状态 |
|---|---|---|
| 1 | 菜单中的 XPan 行；可选择，按照片拍摄菜单库分别保存 | 模拟器通过；尚未实机测试 |
| 2 | 背屏实时取景中的 65:24 不透明黑边 | 模拟器通过；尚未实机测试 |
| 2b | EVF 中的黑边 | 已构建；模拟器不支持 EVF，待实机测试 |
| 3 | JPEG／HEIF 8256×3048（M 6192×2288，S 4128×1528）及预览尺寸 | 实验性构建；模拟器无法拍摄 16:9 静态图像，待实机测试 |
| 4 | NEF 裁切、回放、i 菜单、信息显示、对焦点范围 | 尚未开始 |

### 已知限制

- EVF 黑边假定 1280×960 面板上画面居中（第 244–715 行），尚未验证；HDMI 输出和图像查看尚无黑边。
- 第 3 阶段只改变 JPEG／HEIF 和预览尺寸，NEF 仍为 16:9（8280×4656）；尚不清楚相机是裁切还是压缩画面。
- 黑边区域内的对焦点和图标仍会显示。
- 选择影像区域行显示的图标仍为 16:9。
- 按钮加指令拨盘和 i 菜单不提供 XPan；用这些方式离开 XPan 后，XPan 会自动清除（影像区域不是 16:9 时，每帧同步例程清除标记）。
- 菜单设定保存到存储卡时不包含 XPan 标记。

### 构建

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.13 --tag x8 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0413.bin --features xpan --version 04.13 --tag x8
```

撤销方法：重新安装官方 C 3.11；Z8 支持降级。

### 实现

[`z8xpan/patcher.py`](../z8xpan/patcher.py)：

| 位置 | 改动 |
|---|---|
| GUI 状态 2Ah | 回调和页面记录指向新的六行页面（原厂五行加 XPan，XPan 行复制 16:9 行，项目 35BBh） |
| 各行 OK 动作 | 原厂各行先清除标记再执行原厂设定；XPan 先设为 16:9，再置标记 |
| 进入回调 | 光标和选中标记沿用原厂映射；16:9 且有标记时为 XPan |
| 可用性回调 | XPan 与 16:9 在相同条件下可用（原厂条件 1Bh） |
| `410162c4` | 逐项槽复制的 32 位分支：复制 07000603h 时一并复制标记字节，使每个照片拍摄菜单库保存自己的选择 |

标记位于 `A03D482C2`，是照片拍摄菜单库组中 DS 07000602h 与 07000603h 之间的填充字节。

第 2 阶段（黑边）：

| 位置 | 改动 |
|---|---|
| `402aa36c` / `402aa498` | 背屏（`15484000h`）和 EVF（`15480000h`）直接输入（实时取景）启动／停止时，分别记录运行状态（`408CFF00` 起，经文本段 RAM 别名写入） |
| `402ad198` | 每帧写入叠加层尺寸时，按显示单元记录宽高（此处只用 x17，不改变条件标志） |
| `402cf9d0` | 每帧提交 GUI 叠加层之前：XPan 生效且该单元实时取景运行时，16:9 画面高 = 宽 × 9/16、在面板上居中，65:24 画面高 = 宽 × 24/65（取偶数）、在 16:9 画面中居中；其上下 GUI 留空（透明）的像素改为不透明黑色，图标保留。只处理宽 1024（背屏，第 152–529 行）或 1280（EVF）的叠加层。CPU 写入前后清理缓存行。 |

第 3 阶段（文件尺寸，实验性）：同一提交点调用的同步例程在 XPan 生效时，把 RAM 中静态图像尺寸表（`A02A946A4` 起，经 `41061928` 读取）的 16:9 项改为 65:24 值，XPan 关闭时恢复：JPEG L/M/S、三项全尺寸渲染、1920×1080／640×360／320×180／160×90 预览改为 1920×712／640×236／320×118／160×60。原始数据读出（8280×4656）不变。

## English

`--features xpan` adds **XPan (36×13)** to **Photo shooting menu → Image area → Choose image area**: a full-width 65:24 frame (8256×3048).

**Prototype, milestones 2–3.** Not reviewed or tested on a camera; its recipes are derived. With XPan selected, rear-monitor live view shows 65:24 black bars (emulator-checked). EVF bars and 65:24 JPEG/HEIF sizes are built but can only be checked on the camera. In the interactive menu it is option 7 and is never part of option 5.

### Design

Inside the camera, XPan runs as the stock 16:9 image area (value 4) plus an XPan flag. Sensor readout, autofocus, metering and every piece of code that reads the image area keep their stock 16:9 behaviour. Only the steps that decide the crop (view bars, output size, file data, playback) change to 65:24, in later milestones.

| Milestone | Result | Status |
|---|---|---|
| 1 | XPan row in the menu; selectable, saved per photo shooting menu bank | Emulator pass; not camera-tested |
| 2 | Opaque 65:24 bars in rear-monitor live view | Emulator pass; not camera-tested |
| 2b | Bars in the EVF | Built; the emulator has no EVF, needs a camera test |
| 3 | JPEG/HEIF at 8256×3048 (M 6192×2288, S 4128×1528) and preview sizes | Experimental build; the emulator can't capture 16:9 stills, needs a camera test |
| 4 | NEF crop, playback, i menu, info display, AF point range | Not started |

### Known limits

- EVF bars assume the picture is centred on the 1280×960 panel (rows 244–715), not yet confirmed; HDMI output and image review don't have bars.
- Milestone 3 changes only the JPEG/HEIF and preview sizes; NEFs stay 16:9 (8280×4656). Whether the camera crops or squeezes the picture is not yet known.
- Focus points and icons inside the bar area still show.
- The icon on the Choose image area row still shows 16:9.
- The button-and-dial switch and the i menu don't offer XPan. Leaving XPan that way clears it: whenever the image area isn't 16:9, the per-frame sync routine clears the flag.
- Saving menu settings to a card doesn't include the XPan flag.

### Build

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.13 --tag x8 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0413.bin --features xpan --version 04.13 --tag x8
```

To undo, install official C 3.11 again; Z8 accepts downgrades.

### Implementation

[`z8xpan/patcher.py`](../z8xpan/patcher.py):

| Site | Change |
|---|---|
| GUI state 2Ah | Callbacks and page record point to a new six-row page: the stock five rows plus XPan, a copy of the 16:9 row with item 35BBh |
| Row OK actions | Each stock row clears the flag, then runs its stock setter; XPan sets 16:9 first, then the flag |
| Entry callback | Cursor and mark follow the stock mapping, and XPan when the value is 16:9 and the flag is set |
| Enable callback | XPan is available under the same condition as 16:9 (stock condition 1Bh) |
| `410162c4` | 32-bit case of the per-item slot copy: copying 07000603h also copies the flag byte, so each photo shooting menu bank keeps its own choice |

The flag is at `A03D482C2`, the padding byte between DS 07000602h and 07000603h in the photo-bank group.

Milestone 2 (bars):

| Site | Change |
|---|---|
| `402aa36c` / `402aa498` | Direct input (live view) start/stop of the rear monitor (`15484000h`) and the EVF (`15480000h`): record per unit whether live view is running (from `408CFF00`, written through the text section's RAM alias) |
| `402ad198` | Each frame's overlay size store: record width and height per unit (uses only x17 and leaves the flags alone) |
| `402cf9d0` | Before each GUI overlay frame is presented, with XPan active and live view running on that unit: the 16:9 picture is width × 9/16, centred on the panel; the 65:24 window is width × 24/65 (even), centred in it. Every pixel above and below that the GUI left transparent becomes opaque black, and icons stay. Only overlays 1024 wide (rear monitor, rows 152–529) or 1280 wide (EVF) are painted. Cache lines are cleaned around the CPU writes. |

Milestone 3 (file sizes, experimental): a sync routine called from the same present hook rewrites the 16:9 entries of the still-size table in RAM (from `A02A946A4`, read through `41061928`) to 65:24 values while XPan is active, and restores them when it is off: JPEG L/M/S, the three full-size rendering entries, and the 1920×1080/640×360/320×180/160×90 previews become 1920×712/640×236/320×118/160×60. The raw readout (8280×4656) is unchanged.
