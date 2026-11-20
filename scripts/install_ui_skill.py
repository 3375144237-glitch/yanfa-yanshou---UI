#!/usr/bin/env python3
"""Install the UI implementation skill into an existing Git project."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path(".agents/skills/ui-implementation")
START = "<!-- ui-implementation:start -->"
END = "<!-- ui-implementation:end -->"
RULE = f"""{START}
## 按设计实现 UI

- 新建或修改原生 App、Web/H5 界面前，必须先读 [UI 实现规则]({SKILL.as_posix()}/SKILL.md)，再读取对应 Figma 画板、评论及项目现有组件与平台规范。
- 写 UI 代码前，先确定目标平台和状态、布局、单位、安全区、字体、样式及资源的实现依据；依据不足时不猜测受影响部分。
- 页面与约定状态可运行后，按项目流程另行验收。开发阶段不运行问题采集。
{END}
"""


def project_path(project, relative):
    path = project / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != project and project in part.parents):
        raise ValueError(f"安装路径不能是符号链接：{relative}")
    return path


def install(project):
    project = project.resolve(strict=True)
    if not (project / ".git").exists():
        raise ValueError("请选择研发 Git 仓库根目录")
    if project == ROOT:
        raise ValueError("请选择研发仓库，不能安装到工具仓库本身")

    override = project_path(project, Path("AGENTS.override.md"))
    override_active = override.exists() and override.read_text(encoding="utf-8-sig").strip()
    agents = override if override_active else project_path(project, Path("AGENTS.md"))
    before = agents.read_bytes() if agents.exists() else b""
    start, end, rule = START.encode(), END.encode(), RULE.encode()
    if b"\r\n" in before:
        rule = rule.replace(b"\n", b"\r\n")
    if start in before or end in before:
        if before.count(start) != 1 or before.count(end) != 1 or rule not in before:
            raise ValueError(f"{agents.name} 中的安装规则已修改；请先核对该区块，再手动合并")
        updated = before
    else:
        separator = b"\r\n" if b"\r\n" in before else b"\n"
        bom = b"\xef\xbb\xbf" if before.startswith(b"\xef\xbb\xbf") else b""
        updated = bom + rule + (separator + before[len(bom):] if before[len(bom):] else b"")

    pending = {}
    for name in ("SKILL.md", "COPY-CLASSIFICATION.md"):
        relative = SKILL / name
        destination = project_path(project, relative)
        source = (ROOT / relative).read_bytes()
        if destination.exists():
            if destination.read_bytes() != source:
                raise ValueError(f"{relative} 已有不同内容；请先核对项目版本，再手动合并")
        else:
            pending[destination] = source

    for destination, content in pending.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            output.write(content)
    if updated != before:
        agents.write_bytes(updated)
    return agents


def main():
    parser = argparse.ArgumentParser(description="开工前将 UI 实现规则装入研发项目")
    parser.add_argument("--project", required=True, type=Path, help="研发 Git 仓库根目录")
    args = parser.parse_args()
    try:
        agents = install(args.project)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"安装未完成：{exc}\n")
    print(f"UI 实现规则已就绪：{agents}")
    print(f"Skill：{agents.parent / SKILL / 'SKILL.md'}")


if __name__ == "__main__":
    main()
