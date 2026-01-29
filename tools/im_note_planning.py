#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
记事本规划控制工具 - IM链路专用
用于对比预期APIINFO与第一步-规划列的数据
"""

import json
import logging
import re
import time
import os
import sys
from datetime import datetime
from typing import Dict, List, Tuple
import uuid
import requests
from fastfeishu.exceptions.exception import FeiShuException

from fastfeishu.core import FeiShuSheet


def setup_logger(task_type: str = "note_planning_im"):
    """配置日志"""
    # 使用相对路径，确保日志保存在工具目录下
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "log", f"{task_type}_log")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"创建日志文件夹: {log_dir}")

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"{task_type}_{current_time}.log")

    logger = logging.getLogger(f"{task_type}")
    logger.setLevel(logging.INFO)
    
    # 清除已有处理器以避免重复日志
    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(formatter)

    class TerminalFilter(logging.Filter):
        def filter(self, record):
            return (record.levelno == logging.INFO and
                    ("启动" in record.getMessage() or "完成" in record.getMessage() or
                     "加载飞书文档" in record.getMessage() or "找到目标Sheet" in record.getMessage() or
                     "分片写入" in record.getMessage() or
                     "开始对比" in record.getMessage()))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TerminalFilter())

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def safe_str(value):
    """安全转换值为字符串"""
    if value is None:
        return ""
    return str(value).strip()


def deep_sort_dict(d):
    """深度排序字典，确保对比的一致性"""
    if isinstance(d, dict):
        return {k: deep_sort_dict(v) for k, v in sorted(d.items())}
    elif isinstance(d, list):
        sorted_items = [deep_sort_dict(item) for item in d]
        return sorted(sorted_items)
    else:
        return d


def parse_single_function(func_data):
    """解析单个函数数据，统一格式为{"name": "", "arguments": {}}"""
    parsed = {"name": "", "arguments": {}}
    if not func_data:
        return parsed

    if isinstance(func_data, dict):
        # 处理带function嵌套的格式（规划列）
        if "function" in func_data:
            func_data = func_data["function"]
        # 提取名称和参数
        parsed["name"] = func_data.get("name", "").strip()
        parsed["arguments"] = func_data.get("arguments", {})
    return parsed


def clean_json_str(raw_str):
    """清理JSON字符串：移除所有换行符、制表符、多余空格"""
    if not raw_str:
        return ""
    # 1. 移除所有换行符、制表符
    cleaned = re.sub(r'[\n\r\t]', '', raw_str)
    # 2. 移除JSON语法中不需要的空格（冒号、逗号前后的空格）
    # 冒号前的空格："key" : value → "key":value
    cleaned = re.sub(r'\s*:\s*', ':', cleaned)
    # 逗号前的空格：value , "key" → value,"key"
    cleaned = re.sub(r'\s*,\s*', ',', cleaned)
    # 大括号/中括号内外的空格： { "key" } → {"key"}
    cleaned = re.sub(r'\s*([\{\}\[\]])\s*', r'\1', cleaned)
    # 字符串内的多余空格保留（如参数值中的空格）
    return cleaned.strip()


def parse_planning_data(raw_str):
    """解析规划列数据（支持数组格式的多个函数）- 忽略所有换行和空格"""
    functions = []
    if not raw_str:
        return functions

    try:
        # 关键修改：清理所有换行、空格后再解析
        cleaned_str = clean_json_str(raw_str)
        json_data = json.loads(cleaned_str)

        # 处理数组格式（多个函数）
        if isinstance(json_data, list):
            for item in json_data:
                parsed_func = parse_single_function(item)
                if parsed_func["name"]:  # 只保留有名称的有效函数
                    functions.append(parsed_func)
        # 处理单个函数对象
        elif isinstance(json_data, dict):
            parsed_func = parse_single_function(json_data)
            if parsed_func["name"]:
                functions.append(parsed_func)

    except json.JSONDecodeError as e:
        logger = logging.getLogger(__name__)
        logger.warning(f"规划数据JSON解析失败（原始字符串：{raw_str[:100]}...）: {str(e)}")
    return functions


def parse_expected_data(raw_str):
    """解析预期列数据（支持换行分割的多个函数）- 忽略所有换行和空格"""
    functions = []
    if not raw_str:
        return functions

    try:
        # 关键修改1：先按换行分割（兼容多条函数），再分别清理
        raw_lines = [line.strip() for line in raw_str.split('\n') if line.strip()]
        for line in raw_lines:
            # 关键修改2：清理当前行的所有多余空白字符
            cleaned_str = clean_json_str(line)
            json_data = json.loads(cleaned_str)
            parsed_func = parse_single_function(json_data)
            if parsed_func["name"]:  # 只保留有名称的有效函数
                functions.append(parsed_func)
    except json.JSONDecodeError as e:
        logger = logging.getLogger(__name__)
        logger.warning(f"预期数据JSON解析失败（原始字符串：{raw_str[:100]}...）: {str(e)}")
    return functions


def function_to_key(func):
    """将函数转换为可哈希的键（用于集合对比）"""
    # 名称小写化，参数排序后转为字符串
    return (
        func["name"].lower(),
        json.dumps(func["arguments"], sort_keys=True)
    )


# LLM模型配置
LLM_CONFIG = {
    "url": "http://api-hub.inner.chj.cloud/llm-gateway/v1/chat/completions/azure-gpt-4o",
    "gw_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzb29icjRrdFFoU0ZGWGdNVnlhQkJSOFVLQ2ZjSHlxdSJ9.uyQX3JXjnYhvREYm4Yw8-6dcB4B-lGf6I-NO6Lz5cZc",
    "temperature": 0.1,
    "max_retries": 3
}

SEMANTIC_COLS = ["query"]  # 需要语义对比的子字段（仅query）
SPECIAL_FIELD = "is_long"  # 特殊判定字段（结果中arguments/rarguments的is_long）
SPECIAL_VALUE = "true"     # 特殊值：is_long=true时跳过query对比


def call_llm(prompt: str) -> str:
    """调用GPT-4o模型，返回模型响应"""
    request_id = str(uuid.uuid4())
    headers = {
        'Content-Type': 'application/json',
        'BCS-ApiHub-RequestId': request_id,
        'X-CHJ-GWToken': LLM_CONFIG['gw_token']
    }

    request_data = {
        "model": "azure-gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": LLM_CONFIG['temperature'],
        "stream": False,
        "store": False
    }

    retries = 0
    while retries < LLM_CONFIG['max_retries']:
        try:
            response = requests.post(LLM_CONFIG['url'], headers=headers, json=request_data, timeout=90)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            logger_instance = logging.getLogger(__name__)
            logger_instance.error(f"4o模型调用异常（{retries + 1}/{LLM_CONFIG['max_retries']}）：{str(e)}")
            time.sleep(3)
            retries += 1
    return ""


def compare_semantic(text1: str, text2: str) -> bool:
    """基于GPT-4o的语义对比，判断两段文本核心语义是否一致（修复错别字判定+核心关键词匹配）"""
    # 前置空值判断（保留）
    text1_clean = safe_str(text1)
    text2_clean = safe_str(text2)
    if text1_clean != "" and text2_clean == "":
        return False
    if text1_clean == "" and text2_clean != "":
        return False

    prompt = f"""你必须严格按照以下步骤和规则执行判定，不得偏离：

### 步骤1：提取核心信息（先做这一步，再判定）
分别提取文本1和文本2的「核心信息」（仅保留：核心事实/关键词+关键人物（如有），彻底剔除语气词、冗余修饰、格式性文字、时间修饰词（今天/明天/昨天等））。
⚠️ 关键规则：
1. 关键人物的字/词必须完全一致（包括错别字），如"零零"≠"玲玲"、"大姨"≠"大夷"；
2. 核心事实/关键词只需重叠即可，无需完全匹配冗余内容（如时间修饰词、语气词、格式词）；
3. 时间修饰词（明天/今天/昨天等）属于冗余信息，提取核心信息时必须剔除。

示例：
- 文本：记个备忘录，今天上了舞蹈课 → 核心信息：上了舞蹈课
- 文本：今天上了舞蹈课 → 核心信息：上了舞蹈课
- 文本：上午9点见客户，下午3点团队会议 → 核心信息：9点见客户 3点团队会议
- 文本：明天上午9点见客户下午3点团队会议 → 核心信息：9点见客户 3点团队会议
- 文本：外婆的生日是农历几号，今年阳历是哪天，需要提前准备什么 → 核心信息：外婆 生日 准备
- 文本：零零今年都学会了什么 → 核心信息：零零 今年学会的内容

### 步骤2：核心判定规则（唯一标准）
1. 一致（返回「是」）：满足以下任一条件即判定一致：
   - 核心事实/关键词完全重叠（即使存在时间修饰词、语序调整、信息简化、冗余信息增减）；
   - 关键人物（如有）一致 + 核心诉求/事实重叠；
   - 仅新增/删减时间修饰词（明天/今天/昨天等）、语气词、格式性文字，核心事实不变。
2. 不一致（返回「否」）：
   - 关键人物不同（包括错别字，如"零零"→"玲玲"、"大姨"≠"大夷"）；
   - 核心诉求/事实完全无关（如"见客户" vs "上课"、"舞蹈课" vs "数学课"）；
   - 核心时间/事件关键词改变（如"9点见客户" vs "10点见客户"、"团队会议" vs "部门会议"）。

### 步骤3：输出要求（违反则判定为无效）
- 仅能返回「是」或「否」两个汉字，无任何其他字符（包括空格、标点、解释、换行）；
- 核心关键词重叠时，即使存在时间修饰词（明天/今天）等冗余信息差异，也必须返回「是」。

### 强化案例（必须对齐这些判定结果）
✅ 正确案例（返回「是」）：
1. 预期：记个备忘录，今天上了舞蹈课 | 结果：今天上了舞蹈课 → 是
2. 预期：上午9点见客户，下午3点团队会议 | 结果：明天上午9点见客户下午3点团队会议 → 是
3. 预期：外婆的生日是农历几号，今年阳历是哪天，需要提前准备什么 | 结果：外婆的农历生日是哪一天 → 是
4. 预期：准备送礼物给大姨，之前记过大姨的喜好吗 | 结果：我之前记过的大姨的喜好是什么 → 是

❌ 反例（返回「否」）：
1. 预期：外婆的生日是几号 | 结果：妈妈的生日是几号 → 否
2. 预期：上午9点见客户 | 结果：上午10点见客户 → 否
3. 预期：零零今年都学会了什么 | 结果：我之前记的玲玲今年都学会了什么 → 否
4. 预期：今天上了舞蹈课 | 结果：今天上了数学课 → 否

### 待判定文本
文本1（预期）：{text1_clean}
文本2（结果）：{text2_clean}

最终判定结果（仅写是/否）："""
    try:
        response = call_llm(prompt)
        # 精准清洗：只保留中文的"是"或"否"，剔除所有无关字符
        clean_resp = response.strip()
        # 仅匹配纯"是"或"否"，过滤任何多余内容
        if clean_resp == "是":
            return True
        elif clean_resp == "否":
            return False
        else:
            # 格式错误时，默认判语义不一致（修复错别字场景的漏判）
            logger_instance = logging.getLogger(__name__)
            logger_instance.warning(f"LLM输出格式异常，响应：{response}，按核心不一致判定为否")
            return False
    except Exception as e:
        logger_instance = logging.getLogger(__name__)
        logger_instance.error(f"语义对比失败: {str(e)}")
        return False


def compare_api_info(planning_raw, expected_raw, case_id, logger):
    """对比多个函数（无序），集合完全一致则返回PASS"""
    plan_functions = parse_planning_data(planning_raw)
    expect_functions = parse_expected_data(expected_raw)

    error_details = [f"CaseID: {case_id}"]
    is_passed = True

    # ============ 核心规则【唯一】- 严格匹配你的要求 开始 ============
    # 规则1: 预期单元格原生值是空字符串(纯空白) + 规划单元格原生值是空字符串(纯空白) → 返回PASS
    if expected_raw == "" and planning_raw == "":
        return "PASS"
    # 规则2: 预期单元格原生值是空字符串(纯空白) + 规划单元格有任何内容 → 返回FAILED+日志
    elif expected_raw == "" and planning_raw != "":
        error_details.append("预期列为纯空白无任何内容，但是规划列有值，判定为错误")
        logger.error("\n".join(error_details))
        return "FAILED"
    # ============ 核心规则【唯一】- 严格匹配你的要求 结束 ============

    # 1. 转换为可对比的集合（忽略顺序）
    plan_keys = set(function_to_key(func) for func in plan_functions)
    expect_keys = set(function_to_key(func) for func in expect_functions)

    # 2. 校验函数数量一致性
    if len(plan_functions) != len(expect_functions):
        error_details.append(f"函数数量不匹配：预期{len(expect_functions)}个，规划{len(plan_functions)}个")
        is_passed = False
    else:
        # 3. 对比集合差异（缺失和多余的函数）
        missing = expect_keys - plan_keys  # 预期有但规划没有的函数
        extra = plan_keys - expect_keys  # 规划有但预期没有的函数

        if missing:
            error_details.append("规划列缺少以下函数：")
            for name, args in missing:
                error_details.append(f"  名称: {name}, 参数: {args}")
            is_passed = False

        if extra:
            error_details.append("规划列多出以下函数：")
            for name, args in extra:
                error_details.append(f"  名称: {name}, 参数: {args}")
            is_passed = False

    # 4. 如果函数数量匹配，进一步对比函数参数
    if is_passed and len(expect_functions) > 0:
        # 对比每个函数的参数
        for exp_func in expect_functions:
            exp_name = exp_func['name'].lower()
            exp_args = exp_func['arguments']
            
            # 找到对应的规划函数
            matched_plan_func = None
            for plan_func in plan_functions:
                if plan_func['name'].lower() == exp_name:
                    matched_plan_func = plan_func
                    break
            
            if matched_plan_func is None:
                continue  # 这种情况已经在上面处理过了
            
            # 对比参数
            plan_args = matched_plan_func['arguments']
            
            # 检查特殊字段 is_long
            is_long_value = safe_str(plan_args.get(SPECIAL_FIELD, "")).lower()
            is_special_case = (is_long_value == SPECIAL_VALUE.lower())
            
            # 遍历预期参数
            for arg_key, arg_correct in exp_args.items():
                # 跳过query字段如果is_long=true
                if is_special_case and arg_key == "query":
                    continue
                
                arg_result = safe_str(plan_args.get(arg_key, ""))
                arg_correct_str = safe_str(arg_correct)
                
                # 预期为空、结果有值 → 判错
                if arg_correct_str == "" and arg_result != "":
                    error_details.append(f"arguments.{arg_key}值多余：预期[空值]，结果[{arg_result}]")
                    is_passed = False
                    continue
                # 预期有值、结果为空 → 判错
                elif arg_correct_str != "" and arg_result == "":
                    error_details.append(f"arguments.{arg_key}值缺失：预期[{arg_correct_str}]，结果[空值]")
                    is_passed = False
                    continue
                
                # 两者都有值但不相等 → 语义/精准对比
                if arg_correct_str != arg_result:
                    # query字段语义对比，其他字段精准对比
                    if arg_key in SEMANTIC_COLS:
                        if not compare_semantic(arg_correct_str, arg_result):
                            error_details.append(f"arguments.{arg_key}语义不匹配：预期[{arg_correct_str}]，结果[{arg_result}]")
                            is_passed = False
                    else:
                        error_details.append(f"arguments.{arg_key}值不匹配：预期[{arg_correct_str}]，结果[{arg_result}]")
                        is_passed = False

    # 5. 处理无有效函数的特殊情况
    if not plan_functions:
        error_details.append("规划列无有效函数")
        is_passed = False

    # 6. 记录错误日志并返回结果
    if not is_passed:
        logger.error("\n".join(error_details))
        return "FAILED"

    return "PASS"


def write_results_in_chunks(sheet, column_name, results, chunk_size=2000):
    """分片写入结果以避免API限制"""
    total_results = len(results)
    logger_instance = logging.getLogger(__name__)
    logger_instance.info(f"开始分片写入 {total_results} 条结果，每片 {chunk_size} 条")

    for i in range(0, total_results, chunk_size):
        chunk = results[i:i + chunk_size]
        try:
            # 写入当前分片，从适当的行开始
            start_row = i + 2  # 从第2行开始写入（第1行是表头）
            sheet.write_column(column_name, chunk, start_row=start_row)
            logger_instance.info(f"已写入第 {i + 1}-{min(i + chunk_size, total_results)} 行结果")

            # 避免API频率限制
            time.sleep(0.5)
        except Exception as e:
            logger_instance.error(f"写入第 {i + 1}-{min(i + chunk_size, total_results)} 行结果时发生错误: {str(e)}")
            raise e


def main(feishu_doc_url: str = None):
    """主函数 - IM链路专用"""
    logger = setup_logger("note_planning_im")
    
    logger.info("=== 记事本规划IM链路对比工具启动 ===")
    start_time = time.time()

    if feishu_doc_url is None:
        feishu_doc_url = "https://li.feishu.cn/sheets/LcE5sMqpwh1OJ3twEOhcsF1JnOf"  # 默认IM链路URL

    # 加载飞书文档
    try:
        sheet = FeiShuSheet(feishu_doc_url)
    except FeiShuException as e:
        logger.error(f"连接飞书表格失败: {str(e)}")
        logger.error("请检查以下配置:")
        logger.error("1. FS_APP_ID 和 FS_APP_SECRET 环境变量是否已设置")
        logger.error("2. 飞书应用是否具有表格读写权限")
        logger.error("3. 飞书表格URL是否正确且已分享给应用")
        logger.error("4. 网络连接是否正常")
        raise e

    # 对比数据
    results_with_indices = []
    for idx, row in sheet.iterrows():
        # 原有逻辑读取空单元格会初始化为空字符串
        for k, _ in row.items():
            if row[k] is None:
                row[k] = ''

        case_id = safe_str(row.get('CaseID', f'CASE_{idx}'))
        expected_json = safe_str(row.get('预期APIINFO', ''))
        result_json = safe_str(row.get('第一步-规划', ''))
        
        result = compare_api_info(result_json, expected_json, case_id, logger)
        results_with_indices.append(result)

        # 每处理1000行报告一次进度
        if (idx + 1) % 1000 == 0:
            logger.info(f"已处理 {idx + 1} 行数据")

    # 分片写入结果
    result_column = "APIINFO测试结果666"  # IM链路结果列
    write_results_in_chunks(sheet, result_column, results_with_indices)

    # 统计结果
    total = len(results_with_indices)
    pass_count = results_with_indices.count("PASS")
    fail_count = total - pass_count

    logger.info(f"\n=== 记事本规划IM链路评分完成，耗时: {time.time() - start_time:.2f}秒 ===")
    logger.info(f"总用例数：{total} | 通过数：{pass_count}（{pass_count / total * 100:.2f}%） | 失败数：{fail_count}（{fail_count / total * 100:.2f}%）")


if __name__ == "__main__":
    # 从命令行参数获取飞书表格URL
    if len(sys.argv) < 2:
        print("使用方法:")
        print("python im_note_planning.py [feishu_doc_url]")
        print("feishu_doc_url: 飞书表格URL（可选，默认使用预设URL）")
        print("")
        print("配置说明:")
        print("- 需要设置 FS_APP_ID 和 FS_APP_SECRET 环境变量")
        print("- 详情请参见 CONFIG.md 文件")
        feishu_doc_url = None
    else:
        feishu_doc_url = sys.argv[1]
    
    # 执行主程序
    main(feishu_doc_url)