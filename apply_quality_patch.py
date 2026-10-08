"""给现有 workflow.py 增补反馈范围规则；安装过程不调用模型。"""
import argparse
import ast
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone
from uuid import uuid4


BEGIN = "# QUALITY_REVIEW_V1_BEGIN"
END = "# QUALITY_REVIEW_V1_END"
RULES = (
    "\n以下是对前述规则的具体执行说明：\n",
    "先结合原文和后续补充确定当前处理范围，再判断是否包含两个或以上独立诉求。",
    "独立诉求是可单独处理的不同问题或功能目标；同一问题的操作、异常现象、影响和期望修复不算多个诉求。",
    "当前范围包含多个独立诉求时，needs_clarification 必须为 true，即使主要诉求明确或优先顺序已说明。",
    "category 仍按主要诉求判断；bug、ux、feature 都可以同时 needs_clarification=true，不要因此一律改为 unclear。",
    "多诉求时 problem、evidence、acceptance_criteria 聚焦主要诉求，clarification_questions 至少询问是否将次要诉求另建记录或暂缓处理。",
    "已经说明哪个问题最急时，不要重复询问优先顺序，应确认剩余诉求如何拆分。",
    "例如：导出报错且无法下载，同时希望新增自动提醒。主诉求为故障，应 category=bug、needs_clarification=true，并确认提醒是否另建记录。",
    "若后续补充明确本条只处理其中一个诉求、其余另建或暂缓，则按已确认的本条范围重新判断，不重复澄清已经确认的问题。",
    "problem 只陈述反馈中报告的现象和影响；原因推断必须标为可能或待排查，不能把未经验证的技术原因当事实。",
    "acceptance_criteria 写与本条目标直接对应的可检查结果，可合理细化，但不得擅自新增独立功能、渠道、权限角色或技术实现约束。",
    "原文未提供的具体数值、响应时长、字体大小、准确率、接口状态码和标准要求，不得编造为已确认验收条件；必要时写具体参数或方案待确认。",
    "禁止把邮件推送、按负责人筛选统计、全局设置面板等额外功能直接纳入原文没有要求的验收范围。",
    "信息不足时，验收条件只写需要确认的场景、问题或目标，不使用无依据的数值例子诱导用户。",
    "返回前核对：多诉求标记是否正确；需要澄清时有1—3个问题，否则问题列表为空；引用逐字连续；每条验收条件均在当前范围内。",
)


def patch_text(source):
    tree = ast.parse(source)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "real_analysis"]
    if len(funcs) != 1:
        raise ValueError("没有找到唯一的 real_analysis 函数；请提供当前 workflow.py 核对版本。")
    assignments = [n for n in funcs[0].body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "system_prompt" for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError("system_prompt 的位置与补丁适用版本不一致，未修改文件。")
    assignment = assignments[0]
    if "Analysis.model_json_schema" not in (ast.get_source_segment(source, assignment) or ""):
        raise ValueError("现有提示词没有预期的 Analysis schema，未修改文件。")
    newline = "\r\n" if "\r\n" in source else "\n"
    indent = " " * assignment.col_offset
    lines = [indent + BEGIN, indent + "system_prompt += ("]
    lines += [indent + "    " + repr(rule) for rule in RULES]
    lines += [indent + ")", indent + END]
    block = newline.join(lines) + newline
    if BEGIN in source or END in source:
        if source.count(BEGIN) == source.count(END) == 1 and block in source:
            return source, False
        raise ValueError("检测到已安装或已改动的同名补丁，请先核对现有规则，未覆盖文件。")
    original_lines = source.splitlines(keepends=True)
    prefix = "".join(original_lines[:assignment.end_lineno])
    suffix = "".join(original_lines[assignment.end_lineno:])
    if not prefix.endswith(("\n", "\r")):
        prefix += newline
    updated = prefix + block + suffix
    compile(updated, "workflow.py", "exec")
    return updated, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, default=Path(__file__).resolve().parent / "workflow.py")
    args = parser.parse_args()
    target = args.workflow.resolve()
    temporary = None
    try:
        original = target.read_bytes()
        updated, changed = patch_text(original.decode("utf-8-sig"))
        if not changed:
            print("此补丁已经安装，没有重复添加规则。")
            return 0
        payload = updated.encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf"):
            payload = b"\xef\xbb\xbf" + payload
        if target.read_bytes() != original:
            raise ValueError("workflow.py 在检查期间发生变更，请关闭编辑后重试。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        backup = target.with_name(target.name + ".before_quality_v1." + stamp + ".bak")
        with backup.open("xb") as handle:
            handle.write(original)
        fd, temporary = tempfile.mkstemp(prefix=".quality_patch_", suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        if target.read_bytes() != original:
            raise ValueError("workflow.py 在保存前发生变更，未覆盖文件。")
        os.replace(temporary, target)
        temporary = None
        print("已安装提示词补丁，Python 语法检查通过。")
        print("修改文件：", target)
        print("原文件备份：", backup)
        print("下一步运行：.\\.venv\\Scripts\\python.exe quality_smoke_test.py")
        return 0
    except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
        print("未完成安装：", exc)
        return 1
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
