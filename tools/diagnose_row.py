#!/usr/bin/env python3
"""诊断特定行的JSON解析问题"""

import json
import sys
import os
from fastfeishu.core import FeiShuSheet

# 导入主模块的函数
import importlib.util
spec = importlib.util.spec_from_file_location("vehicle_control",
                                              os.path.join(os.path.dirname(__file__),
                                                          "4o_vehicle_control.py"))
vehicle_control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vehicle_control)

clean_json_string = vehicle_control.clean_json_string


def diagnose_string(label, raw_str):
    """详细诊断一个字符串"""
    print(f"\n{'=' * 100}")
    print(f"{label}")
    print(f"{'=' * 100}")

    print(f"\n原始字符串 (repr):")
    print(f"  {repr(raw_str)}")

    print(f"\n字符串长度: {len(raw_str)}")

    # 检查特殊字符
    print(f"\n特殊字符检查:")
    special_chars_found = False
    for i, char in enumerate(raw_str):
        code = ord(char)
        # 只显示非ASCII或特殊的ASCII字符
        if code > 127 or (code < 32 and char not in ['\n', '\r', '\t']):
            print(f"  位置 {i:3d}: '{char}' -> U+{code:04X} ({code})")
            special_chars_found = True
        # 检查可疑的引号和标点
        elif char in ['"', '"', ''', ''', '：', '，', '｛', '｝']:
            print(f"  位置 {i:3d}: '{char}' -> U+{code:04X} ({code}) ⚠️  可疑字符!")
            special_chars_found = True

    if not special_chars_found:
        print("  未发现特殊字符")

    # 清理字符串
    cleaned = clean_json_string(raw_str)

    if cleaned != raw_str:
        print(f"\n清理后的字符串 (repr):")
        print(f"  {repr(cleaned)}")
        print(f"\n清理改变了 {len([1 for a, b in zip(raw_str, cleaned) if a != b])} 个字符")

    # 尝试解析
    print(f"\nJSON解析测试:")
    try:
        parsed = json.loads(cleaned)
        print(f"  ✅ 解析成功!")
        print(f"  结果: {json.dumps(parsed, ensure_ascii=False, indent=2)}")
        return True
    except json.JSONDecodeError as e:
        print(f"  ❌ 解析失败!")
        print(f"  错误: {e}")
        print(f"  错误位置: column {e.colno}")
        if e.colno <= len(cleaned):
            problem_char = cleaned[e.colno-1]
            print(f"  问题字符: '{problem_char}' (U+{ord(problem_char):04X})")
            context_start = max(0, e.colno-20)
            context_end = min(len(cleaned), e.colno+20)
            print(f"  上下文: ...{repr(cleaned[context_start:context_end])}...")
        return False


def main():
    from dotenv import load_dotenv
    load_dotenv()

    print("=" * 100)
    print("诊断工具: 检查飞书表格第11-13行的JSON解析问题")
    print("=" * 100)

    FEISHU_DOC_URL = r'https://li.feishu.cn/sheets/NKYZs3PvYhnicItU0rCcwxapnEc?sheet=0NNYFR'

    print(f"\n正在加载飞书文档...")
    sheet = FeiShuSheet(FEISHU_DOC_URL)

    # 诊断第11-13行 (索引10-12)
    rows_to_check = [10, 11, 12]

    # 获取所有行
    all_rows = list(sheet.iterrows())

    for idx in rows_to_check:
        if idx >= len(all_rows):
            print(f"警告: 索引 {idx} 超出范围 (总行数: {len(all_rows)})")
            continue

        print(f"\n\n{'#' * 100}")
        print(f"# 第 {idx + 1} 行 (索引 {idx})")
        print(f"{'#' * 100}")

        # iterrows 返回 (index, row)
        _, row = all_rows[idx]
        case_id = row.get('CaseID', f'row_{idx+1}')

        print(f"\nCaseID: {case_id}")
        print(f"name: {row.get('name', 'N/A')}")
        print(f"rname: {row.get('rname', 'N/A')}")

        # 诊断 arguments
        args_result = diagnose_string(
            f"📝 arguments (预期值)",
            row.get('arguments', '')
        )

        # 诊断 rarguments
        rargs_result = diagnose_string(
            f"📝 rarguments (实际值)",
            row.get('rarguments', '')
        )

        # 总结
        print(f"\n{'=' * 100}")
        if args_result and rargs_result:
            print(f"✅ 第 {idx + 1} 行: 两个JSON都解析成功")
        elif args_result:
            print(f"⚠️  第 {idx + 1} 行: arguments成功, rarguments失败")
        elif rargs_result:
            print(f"⚠️  第 {idx + 1} 行: arguments失败, rarguments成功")
        else:
            print(f"❌ 第 {idx + 1} 行: 两个JSON都解析失败")
        print(f"{'=' * 100}")

    print("\n\n" + "=" * 100)
    print("诊断完成!")
    print("=" * 100)


if __name__ == "__main__":
    main()
