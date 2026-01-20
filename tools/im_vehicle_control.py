from fastfeishu.core import FeiShuSheet

import json
import logging
import re
import time
import os
from datetime import datetime


# 配置日志
def setup_logger():
    # 使用相对路径，确保日志保存在工具目录下
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "log", "im_vehicle_control_log")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"创建日志文件夹: {log_dir}")

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"im_vehicle_control_{current_time}.log")

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(formatter)

    class TerminalFilter(logging.Filter):
        def filter(self, record):
            return (record.levelno == logging.INFO and
                    ("启动" in record.getMessage() or "完成" in record.getMessage() or
                     "加载飞书文档" in record.getMessage() or "找到目标Sheet" in record.getMessage() or
                     "分片写入" in record.getMessage()))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TerminalFilter())

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()


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
    """清理JSON字符串：移除所有换行符、制表符、多余空格（仅保留JSON语法必需的空格）"""
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
        logger.warning(f"预期数据JSON解析失败（原始字符串：{raw_str[:100]}...）: {str(e)}")
    return functions


def function_to_key(func):
    """将函数转换为可哈希的键（用于集合对比）"""
    # 名称小写化，参数排序后转为字符串
    return (
        func["name"].lower(),
        json.dumps(func["arguments"], sort_keys=True)
    )


def compare_api_info(planning_raw, expected_raw, case_id):
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

    # 4. 处理无有效函数的特殊情况
    if not plan_functions:
        error_details.append("规划列无有效函数")
        is_passed = False

    # 5. 记录错误日志并返回结果
    if not is_passed:
        logger.error("\n".join(error_details))
        return "FAILED"

    return "PASS"


def write_results_in_chunks(sheet, column_name, results, chunk_size=2000):
    """分片写入结果以避免API限制"""
    total_results = len(results)
    logger.info(f"开始分片写入 {total_results} 条结果，每片 {chunk_size} 条")
    
    for i in range(0, total_results, chunk_size):
        chunk = results[i:i + chunk_size]
        try:
            # 写入当前分片，从适当的行开始
            start_row = i + 2  # 从第2行开始写入（第1行是表头）
            sheet.write_column(column_name, chunk, start_row=start_row)
            logger.info(f"已写入第 {i+1}-{min(i+chunk_size, total_results)} 行结果")
            
            # 避免API频率限制
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"写入第 {i+1}-{min(i+chunk_size, total_results)} 行结果时发生错误: {str(e)}")
            # 可能需要重试逻辑或其他错误处理
            raise e


def main():
    logger.info("=== IM_vehicle_control API对比工具启动 ===")
    start_time = time.time()

    # 加载飞书文档
    sheet = FeiShuSheet(FEISHU_DOC_URL)

    # 对比数据 - 核心修改3：彻底删除所有跳过逻辑，所有行全部执行对比+写入
    results_with_indices = []
    for idx, row in sheet.iterrows():
        # 原有逻辑读取空单元格会初始化为空字符串
        # 这里我保持同样的逻辑
        for k, _ in row.items():
            if row[k] is None:
                row[k] = ''

        # ============ 彻底删除了 continue 跳过代码，无任何行被忽略 ============
        result = compare_api_info(row['第一步-规划'], row['预期APIINFO'], row['CaseID'])
        results_with_indices.append(result)
        
        # 每处理1000行报告一次进度
        if (idx + 1) % 1000 == 0:
            logger.info(f"已处理 {idx + 1} 行数据")

    # 分片写入数据以避免API限制
    write_results_in_chunks(sheet, RESULT_COLUMN_NAME, results_with_indices)

    logger.info(f"=== IM_vehicle_control 流程完成，耗时: {time.time() - start_time:.2f}秒 ===")


# ================= 配置项 =================
FEISHU_DOC_URL = r'https://li.feishu.cn/sheets/HWdTsatumhZ4yntLvgNcjeLsnUh?sheet=c13f03'
EXPECTED_COLUMN_NAME = "预期APIINFO"
PLANNING_COLUMN_NAME = "第一步-规划"
RESULT_COLUMN_NAME = "APIINFO测试结果1231231"
CASE_ID_COLUMN_NAME = "CaseID"
# =========================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()