# 按设计开发，完成后验收

面向 iOS、Android、鸿蒙原生 App，也支持 Web/H5。先把实现规则装进研发仓库，AI 在写页面前读设计和项目约束，直接按依据实现。页面可运行后，再用独立验收流程核对效果。

本仓库从只读参考 [codex-UI-acceptance](https://github.com/3375144237-glitch/codex-UI-acceptance) 迁移基础工具，研发版改动仅在本仓库维护。

## 开工前：把规则装进研发仓库

克隆本仓库后，在工具仓库目录运行（只需 Python 3.9+，此步骤不安装采集依赖）：

```bash
python3 scripts/install_ui_skill.py --project "/研发仓库的绝对路径"
```

命令把 [ui-implementation](.agents/skills/ui-implementation/SKILL.md) 装到研发仓库的 `.agents/skills/`，并在 Codex 当前读取的根规则文件（`AGENTS.md` 或非空的 `AGENTS.override.md`）加入必读入口，保留原有规则。相同内容可重复运行；遇到不同的同名 Skill 或已编辑的安装区块时会停止，提示人工合并。团队将这些项目规则纳入版本管理后，后续任务可以复用。

在研发仓库启动新的 Codex 任务，提供 Figma 链接和目标端，例如：

> 按这个 Figma 画板实现 iOS、Android、鸿蒙的课程详情页。先按项目的 UI 实现规则确认各端依据，再开始写代码。

AI 先确定设计节点、约束、资源和对应组件；缺少关键依据时先说明缺口，不猜测数值或替代素材。开发阶段只需完成产品代码与正常构建、运行。

## 完成后：按需验收

页面与目标状态可运行后，在工具仓库启动独立的 Codex 验收任务，使用 [ui-acceptance Skill](.agents/skills/ui-acceptance/SKILL.md)。提供研发仓库路径、Figma 链接、目标端和运行入口，例如“验收这个已完成的页面，分别给出各端结论”。只验收时，助手核对并给出结论，不修改产品代码。工具默认保存 JSON 和截图证据，需要表格时加 `--xlsx`。

| 入口 | 需要 | 自动采集 | 边界 |
|---|---|---|---|
| iOS / Android | 现有 Appium 会话、逻辑视口和元素树单位倍率 | 当前截图、XML 元素树及可用文字/边界 | 需对应驱动；树未暴露的字号、颜色等记未验收 |
| 鸿蒙 | 兼容现有接口且已验证的驱动，或测试框架导出的统一快照与截图 | 在驱动适配或快照导出后比较 | 工具未内置鸿蒙驱动；不能把 Android 采集结果当鸿蒙结果 |
| Web/H5 | localhost/测试网址、视口、就绪元素 | Chromium 截图、DOM 可见边界、计算样式 | Shadow DOM、iframe、Canvas 内部、跨平台字体与复杂视觉效果需要另行复核 |
| 已有测试框架 | 统一快照 JSON、可选截图 | 直接使用标准快照检查 | 采集方负责真实来源、单位与环境信息 |

使用采集工具时在工具仓库创建 Python 3.9+ 虚拟环境；Windows 使用 `.venv\Scripts\python.exe`。Web 采集另需 Node 20+。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 使用 Web 采集时执行：
npm ci
npm run browser:install
```

在项目中复制 [原生配置](examples/native-review.json) 或 [Web 配置](examples/web-review.json) 为 `config/页面.local.json`，填入实际 Figma 链接和运行入口。原生示例是 Android Appium；iOS 需核对坐标单位后修改平台字段，鸿蒙优先按[统一快照规范](docs/接入与快照规范.md)接入。相对路径按配置文件所在目录解析。示例中的链接和会话 ID 必须替换。

Figma REST 使用本机环境变量 `FIGMA_TOKEN`，需要文件内容和评论读取权限。也支持由现有 Figma MCP 导出统一快照，不依赖某个人电脑里的私有脚本。令牌、浏览器 storage state、设备会话和产物不提交 Git。

```bash
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json --xlsx
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json --previous outputs/上一轮/review.json
.venv/bin/python scripts/yanfa_review.py check --config config/课程页.local.json --previous outputs/上一轮/review.json --only 1:23,1:24
```

默认每轮创建独立目录；`--out` 指向不存在的新目录。使用 `--strict`：确认差异退出 1，执行错误退出 2，检查覆盖不足退出 3，否则退出 0。未启用时，正常完成验收采集即退出 0，调用方仍应阅读结果中的确认程度和覆盖状态。

工具先按唯一名称、key 或文字自动配对，输出 suggested-mappings.json；自动配对的差异是候选，助手需结合截图确认。重复组件、找不到的元素保持待确认。稳定 selector / accessibility ID 是可选增强；不要求第一天给所有元素加标识。确认后的映射与检查范围写入项目配置，一次配置后复用。

每条差异保留稳定 ID、设计值、实际值、差值、运行定位、设计链接和有条件生成的局部证据。JSON 是复验依据。源码提示必须由助手在研发仓库中核实。

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
