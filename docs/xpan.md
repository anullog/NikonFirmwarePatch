# XPan 影像区域（原型） / XPan image area (prototype)

[简体中文](#简体中文) · [English](#english)

## 简体中文

`--features xpan` 在**照片拍摄菜单 → 影像区域 → 选择影像区域**中加入 **XPan (36×13)**，即 65:24 的全宽画幅（8256×3048）。

**原型，第 2–4 阶段（04.13.y4）。** 未经审核或实机测试，配方为推导所得。选择 XPan 后，背屏实时取景显示 65:24 黑边（模拟器已验证）；EVF 黑边和 65:24 JPEG／HEIF 尺寸已构建，但只能在相机上验证。交互菜单中为选项 7，从不包含在选项 5 中。

### 设计

XPan 在相机内部按原厂 16:9 影像区域（设定值 4）运行，另加一个 XPan 标记。传感器读出、自动对焦、测光及所有读取影像区域的代码保持原厂 16:9 行为；只有决定裁切的环节（取景黑边、输出尺寸、文件信息、回放）将在后续阶段改为 65:24。

| 阶段 | 结果 | 状态 |
|---|---|---|
| 1 | 菜单中的 XPan 行；可选择，按照片拍摄菜单库分别保存 | 模拟器通过；尚未实机测试 |
| 2 | 背屏实时取景中的 65:24 不透明黑边 | 模拟器通过；尚未实机测试 |
| 2b | EVF 中的黑边 | 已构建；模拟器不支持 EVF，待实机测试 |
| 3 | JPEG／HEIF 8256×3048（M 6192×2288，S 4128×1528）及预览尺寸 | 实验性构建；模拟器无法拍摄 16:9 静态图像，待实机测试 |
| 4 | 对焦点范围限制在 65:24 画面内（所有自动对焦区域模式及自定义宽区域尺寸） | 模拟器通过（单点对焦框停在 65:24 画面上下边缘）；尚未实机测试 |
| 4b | XPan 图标：实时取景／EVF 信息显示、影像区域菜单行和 XPan 行、i 菜单图块 | 实时取景和菜单模拟器通过；i 菜单图块待实机测试 |
| 4c | 按钮加指令拨盘和 i 菜单中可选择 XPan（16:9 之后） | 已构建；模拟器无拨盘，待实机测试 |
| 5 | NEF 裁切、回放 | 尚未开始 |

### 已知限制

- EVF 黑边假定 1280×960 面板上画面居中（第 244–715 行），尚未验证；图像查看尚无黑边。
- 第 3 阶段只改变 JPEG／HEIF 和预览尺寸，NEF 仍为 16:9（8280×4656）；尚不清楚相机是裁切还是压缩画面。
- 黑边区域内的图标仍会显示。高于 65:24 画面的自定义宽区域对焦框只保留居中的一行，会伸入黑边。
- XPan 只用于照片模式；视频模式下 16:9 按原厂运行（无黑边，尺寸和对焦范围为原厂值）。
- HDMI 输出不经过背屏／EVF 的显示单元，尚无黑边。
- 回放时 XPan 照片的影像区域图标仍为 16:9（文件中不记录 XPan）。
- i 菜单的影像区域列表（在该项上按 OK）没有单独的 XPan 行：XPan 生效时 16:9 行显示为 XPan，在列表中选择任何一行都会清除 XPan。要选择 XPan，请在高亮该项时转动指令拨盘，或使用按钮加拨盘、照片拍摄菜单。
- 其他改变影像区域的方式（按钮切换 FX／DX、遥控）会使 XPan 自动清除（影像区域不是 16:9 时，每帧同步例程清除标记）。
- 菜单设定保存到存储卡时不包含 XPan 标记。

### 构建

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.13 --tag y4 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0413.bin --features xpan --version 04.13 --tag y4
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

第 4 阶段（对焦点范围）：同一同步例程还替换 RAM 中自动对焦网格描述符（`40F3493C` 每次调用时复制；各自动对焦区域模式表 `A02A5CE38`／`A02A5D714`，自定义宽区域尺寸表 `A02A4C470`／`A02A54954`）的 16:9 项：保持对焦框大小、步长和边距，行数减为 65:24 画面（传感器第 1228–4276 行）内可容纳的最多行数（奇偶性不变），并以原中间行为中心。XPan 生效的条件为：有 XPan 标记、影像区域为 16:9，且照片／视频选择器位于照片；黑边使用同一判断结果。

图标：新增两幅 8 位灰度 PNG 图像（ID 1862h 为 36×32，1863h 为 36×36），即原厂 16:9 图像的边框加“XPAN”文字。PNG 使用未压缩的 deflate 块，因此字节不随 zlib 版本变化。图像查找 `4032DC64` 在 `4032DCA0` 处挂钩，原厂表之后的 ID 从 XPan 记录读取。有 XPan 标记时，实时取景信息显示（`4041A9E4`，原 84Bh）和影像区域菜单项的两个图标选择处（`405DBDA0`、`405D9A1C`，原 1173h）改用 XPan 图像。XPan 行使用 1863h；i 菜单图块的选择器（`A02870848`）由同步例程切换。

按钮加拨盘（控制端，`40D49780`）：顺序表（`A02823428`，7 行）复制到可写区域并在 16:9 之后加入 XPan 行（`40D4E82C` 返回新表，8 行）。`40D3A7BC` 使 XPan 行与 16:9 行同样可用（视频中不可用），`40D497D4` 按标记确定当前行，`40D4A384` 选择 XPan 时写入 16:9 并置待定字节，同步例程在影像区域读为 16:9 后设置标记（最多等待 200 帧）。弹出列表（`4049AC44`）的 16:9 项在 XPan 生效时显示 XPan。

i 菜单（影像区域项描述符 `43A1D408`）：拨盘步进（+10h）在 16:9 之后插入 XPan；列表打开（+20h）记录标记，取消（+30h）恢复标记；列表应用（`404D948C`）清除标记；拨盘列表（`404EA144`）和列表标签（`A028760A8`，同步例程切换）在 XPan 生效时显示 XPan。

## English

`--features xpan` adds **XPan (36×13)** to **Photo shooting menu → Image area → Choose image area**: a full-width 65:24 frame (8256×3048).

**Prototype, milestones 2–4 (04.13.y4).** Not reviewed or tested on a camera; its recipes are derived. With XPan selected, rear-monitor live view shows 65:24 black bars (emulator-checked). EVF bars and 65:24 JPEG/HEIF sizes are built but can only be checked on the camera. In the interactive menu it is option 7 and is never part of option 5.

### Design

Inside the camera, XPan runs as the stock 16:9 image area (value 4) plus an XPan flag. Sensor readout, autofocus, metering and every piece of code that reads the image area keep their stock 16:9 behaviour. Only the steps that decide the crop (view bars, output size, file data, playback) change to 65:24, in later milestones.

| Milestone | Result | Status |
|---|---|---|
| 1 | XPan row in the menu; selectable, saved per photo shooting menu bank | Emulator pass; not camera-tested |
| 2 | Opaque 65:24 bars in rear-monitor live view | Emulator pass; not camera-tested |
| 2b | Bars in the EVF | Built; the emulator has no EVF, needs a camera test |
| 3 | JPEG/HEIF at 8256×3048 (M 6192×2288, S 4128×1528) and preview sizes | Experimental build; the emulator can't capture 16:9 stills, needs a camera test |
| 4 | Focus points kept inside the 65:24 frame (every AF-area mode and custom wide-area size) | Emulator pass (the focus box stops at the 65:24 frame's top and bottom); not camera-tested |
| 4b | XPan icon: live view/EVF info display, Image area menu rows and the XPan row, i-menu tile | Emulator pass for live view and menus; i-menu tile needs a camera test |
| 4c | XPan in the button-and-command-dial choice and the i menu (after 16:9) | Built; the emulator has no dials, needs a camera test |
| 5 | NEF crop, playback | Not started |

### Known limits

- EVF bars assume the picture is centred on the 1280×960 panel (rows 244–715), not yet confirmed; image review doesn't have bars.
- Milestone 3 changes only the JPEG/HEIF and preview sizes; NEFs stay 16:9 (8280×4656). Whether the camera crops or squeezes the picture is not yet known.
- Icons inside the bar area still show. Custom wide-area AF boxes taller than the 65:24 frame keep one centred row and reach into the bars.
- XPan applies to photo mode only; in video mode 16:9 runs stock (no bars, stock sizes and focus range).
- HDMI output doesn't go through the rear-monitor/EVF display units and has no bars yet.
- In playback, XPan pictures show the 16:9 image-area icon (files don't record XPan).
- The i-menu Image area list (OK on the item) has no XPan row of its own. While XPan is current, the 16:9 row shows XPan, and picking any row in the list clears XPan. To pick XPan, turn the command dial with the item highlighted, or use the button and dial or the photo shooting menu.
- Other ways of changing the image area (the FX/DX button toggle, remote control) clear XPan: whenever the image area isn't 16:9, the per-frame sync routine clears the flag.
- Saving menu settings to a card doesn't include the XPan flag.

### Build

```sh
python patch.py "/path/to/Z_8_0311.bin" --features xpan --version 04.13 --tag y4 --output build/xpan
python verify.py "/path/to/Z_8_0311.bin" build/xpan/Z_8_0413.bin --features xpan --version 04.13 --tag y4
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

Milestone 4 (focus point range): the same sync routine also swaps the 16:9 entries of the AF grid descriptors in RAM. `40F3493C` copies one on every call, from the AF-area mode tables `A02A5CE38`/`A02A5D714` and the custom wide-area size tables `A02A4C470`/`A02A54954`. Box size, step and edge margins stay the same. The row count drops to the most rows that fit the 65:24 frame (sensor rows 1228–4276), keeping its parity, centred on the stock middle row. XPan is active when the flag is set, the image area is 16:9 and the photo/video selector is on photo; the bars use the same result.

Icon: two new 8-bit greyscale PNG images, id 1862h (36×32) and 1863h (36×36): the stock 16:9 frame with "XPAN" text. The PNGs use stored deflate blocks, so their bytes don't depend on the zlib version. The image lookup `4032DC64` is hooked at `4032DCA0` so ids past the stock table come from the XPan records. With the flag set, the live-view info display (`4041A9E4`, was 84Bh) and the two Image area row icon pickers (`405DBDA0`, `405D9A1C`, was 1173h) use the XPan images. The XPan row uses 1863h, and the sync routine switches the i-menu tile selector (`A02870848`).

Button and dial (control side, `40D49780`): the order table (`A02823428`, 7 rows) is copied to writable memory with an XPan row after 16:9, and `40D4E82C` returns the copy (8 rows). `40D3A7BC` makes the XPan row available exactly when 16:9 is (never in video), and `40D497D4` picks the current row from the flag. `40D4A384` writes 16:9 for the XPan row and sets a pending byte. The sync routine turns it into the flag once the image area reads 16:9 (it waits up to 200 frames). The pop-up list (`4049AC44`) shows XPan in its 16:9 slot while XPan is current.

i menu (Image area item descriptor `43A1D408`): the dial step (+10h) inserts XPan after 16:9. Opening the list (+20h) remembers the flag and cancelling (+30h) restores it. A list apply (`404D948C`) clears the flag. The dial list (`404EA144`) and the list label (`A028760A8`, switched by the sync routine) show XPan while XPan is current.
