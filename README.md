# 研发 UI 还原自查

面向 Web/H5 与原生 App 的研发自查工具。输入 Figma 画板和运行页面，生成可浏览的 HTML 报告、局部证据、JSON 与 Excel；改完用同一基线复验。研发无需把设备截图上传到 Figma。

本仓库从只读参考 [codex-UI-acceptance](https://github.com/3375144237-glitch/codex-UI-acceptance) 的 `e6aea0e2151681c4d57a0cc0157a854f3b7a4c39` 迁移基础工具，研发版改动仅在 [yanfa-yanshou---UI](https://github.com/3375144237-glitch/yanfa-yanshou---UI) 维护。

## 先体验报告

Python 3.9+；建议使用独立虚拟环境。Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

```bash
git clone https://github.com/3375144237-glitch/yanfa-yanshou---UI.git
cd yanfa-yanshou---UI
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/yanfa_review.py demo
```

命令打印 HTML 路径，可直接打开。演示是合成页面，包含差异报告与 fixed/ 修复后报告，用于体验功能，不代表真实产品验收。

## 两种运行页面入口

| 入口 | 需要 | 自动采集 | 边界 |
|---|---|---|---|
| Web/H5 | localhost/测试网址、视口、就绪元素 | Chromium 截图、DOM 可见边界、计算样式 | Shadow DOM、iframe、Canvas 内部、跨平台字体与复杂视觉效果需要另行复核 |
| 原生 App | 现有 Appium 会话、逻辑视口和元素树单位倍率 | 当前截图、XML 元素树及可用文字/边界 | 字号、颜色等未暴露属性记未验收；Android/iOS 需对应驱动；鸿蒙等需自己的驱动或快照适配器 |
| 已有测试框架 | 统一快照 JSON、可选截图 | 直接使用标准快照检查 | 采集方负责真实来源、单位与环境信息 |

Web 另需 Node 20+：

```bash
npm ci
npm run browser:install
```

在项目中复制 [Web 配置](examples/web-review.json) 或 [原生配置](examples/native-review.json) 为 `config/页面.local.json`，填入实际 Figma 链接和运行入口。相对路径按配置文件所在目录解析。示例中的链接和会话 ID 必须替换。

Figma REST 使用本机环境变量 `FIGMA_TOKEN`，需要文件内容和评论读取权限。也支持由现有 Figma MCP 导出统一快照，不依赖某个人电脑里的私有脚本。令牌、浏览器 storage state、设备会话和产物不提交 Git。

```bash
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json --previous outputs/上一轮/review.json
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json --previous outputs/上一轮/review.json --only 1:23,1:24
```

默认每轮创建独立目录；`--out` 指向不存在的新目录。使用 `--strict`：确认差异退出 1，执行错误退出 2，检查覆盖不足退出 3，否则退出 0。默认检查生成报告后退出 0，调用方仍应阅读报告中的确认程度和覆盖状态。

## 研发怎么使用

在能访问研发代码与运行环境的编码助手中加载本仓库的 [ui-design-review Skill](.agents/skills/ui-design-review/SKILL.md)，例如：

> 对照这个 Figma 链接，检查 localhost 的课程详情页。当前只检查课程卡片。自动生成配置，先帮我识别对应元素，给出设计值、实际值、证据和修复入口。修复后用同一基线复验。

工具先按唯一名称、key 或文字自动配对，输出 suggested-mappings.json；自动配对的差异是候选，助手需结合截图确认。重复组件、找不到的元素保持待确认。稳定 selector / accessibility ID 是可选增强；不要求第一天给所有元素加标识。确认后的映射与检查范围写入项目配置，一次配置后复用。

每条问题有稳定 ID、设计/实际值、差值、运行定位、设计链接、局部对比与可复制修复说明。HTML 可筛选和搜索；Excel 用于交付，JSON 是机器复验依据。源码提示必须由助手在研发仓库中核实，采集器不会猜代码文件和行号。

## 判定与复验

- 用 logical_px 统一几何坐标；720/@2x 是可选项目规则。Figma 单位、截图像素、CSS px、dp/pt 必须明确换算，不能按机型猜。
- 分别比较位置、尺寸与可读样式。重合度不作为通过门槛，像素图用于辅助判断。
- Comment、Annotation、Measurement 都保留读取状态。REST 当前读取前两项，Measurement 需通过 MCP/界面补充。未读完时保留候选；经项目明确确认的例外可带理由记录为 waived。
- 工具保留设计说明文本，但不会自动执行自由文本中的例外规则。助手应先把范围、文案性质、单位和容差转成配置。
- 自动配对不等于已确认；未知属性、裁切、范围外和缺失依据不记通过。
- 固定文案需声明 copy=fixed；动态文案仍检查可读样式，超长/模板/图片内容由助手结合证据复核。
- 复验记录 resolved / still_present / not_rechecked；只有设计内容、设备环境、页面状态和检查规则一致，且该属性本轮可靠检查，才消除旧问题。处理状态与机器复验结果独立。
- 报告展示的是“已声明属性”的检查覆盖，不承诺一个通用还原度分数。完整验收还需检查图片、阴影、遮挡、响应式、交互状态与真实设备。

详细配置见 [接入与快照规范](docs/接入与快照规范.md)。历史双图处理入口 `scripts/ui_review.py` 继续保留（原 720/@2x 规则），不参与研发版自动复验。

## 开发与验证

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check scripts/capture-web.mjs
```

测试包含属性容差、歧义映射、状态漂移、缺失/裁切、跨轮复验、报告输出和 Appium 协议模拟。真实 Web 采集用 [浏览器冒烟脚本](scripts/smoke_web.py)；真实设备/Figma 凭据由项目接入时验证。

创建文件前读取 [问题与报错日志](问题与报错日志.md)。核心文档达到 7,200 字符时蒸馏，始终低于 8,000 字符。
