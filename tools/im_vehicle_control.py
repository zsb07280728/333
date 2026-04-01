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
    logger.setLevel(logging.DEBUG)  # 改为DEBUG级别以查看详细日志

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


def clean_special_chars(raw_str):
    """清理JSON字符串中的特殊字符（中文引号、全角字符、不可见字符）"""
    if not raw_str:
        return raw_str

    cleaned = raw_str

    # 1. 替换各种类型的引号为标准英文引号
    quote_replacements = {
        '"': '"',   # 左双引号
        '"': '"',   # 右双引号
        ''': "'",   # 左单引号
        ''': "'",   # 右单引号
        '＂': '"',  # 全角双引号
        '＇': "'",  # 全角单引号
        '`': "'",   # 反引号
        '´': "'",   # 重音符
    }
    for old, new in quote_replacements.items():
        cleaned = cleaned.replace(old, new)

    # 2. 替换全角字符为半角
    fullwidth_replacements = {
        '：': ':',  # 全角冒号
        '，': ',',  # 全角逗号
        '｛': '{',  # 全角左花括号
        '｝': '}',  # 全角右花括号
        '［': '[',  # 全角左方括号
        '］': ']',  # 全角右方括号
        '（': '(',  # 全角左括号
        '）': ')',  # 全角右括号
    }
    for old, new in fullwidth_replacements.items():
        cleaned = cleaned.replace(old, new)

    # 3. 移除或替换其他可能的问题字符
    cleaned = cleaned.replace('\u200b', '')  # 零宽空格
    cleaned = cleaned.replace('\ufeff', '')  # BOM标记
    cleaned = cleaned.replace('\xa0', ' ')   # 不间断空格 → 普通空格
    cleaned = cleaned.replace('\u3000', ' ') # 全角空格 → 普通空格

    return cleaned


def clean_json_str_for_single_object(raw_str):
    """清理JSON字符串（仅用于单个JSON对象）：先清理特殊字符，再移除换行符、制表符、多余空格"""
    if not raw_str:
        return ""

    # 首先清理特殊字符
    cleaned = clean_special_chars(raw_str)

    # 1. 移除所有换行符、制表符
    cleaned = re.sub(r'[\n\r\t]', '', cleaned)
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
        cleaned_str = clean_json_str_for_single_object(raw_str)

        # 添加调试日志
        if raw_str != cleaned_str:
            logger.debug(f"parse_planning_data - 清理前: {repr(raw_str[:100])}")
            logger.debug(f"parse_planning_data - 清理后: {repr(cleaned_str[:100])}")

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
        logger.error(f"规划数据JSON解析失败!")
        logger.error(f"  错误信息: {str(e)}")
        logger.error(f"  原始字符串: {repr(raw_str[:200])}")
        logger.error(f"  清理后字符串: {repr(clean_json_str_for_single_object(raw_str)[:200])}")
        if hasattr(e, 'colno') and e.colno:
            cleaned = clean_json_str_for_single_object(raw_str)
            if e.colno <= len(cleaned):
                problem_char = cleaned[e.colno-1] if e.colno > 0 else ''
                logger.error(f"  问题字符: '{problem_char}' (Unicode: U+{ord(problem_char):04X})")
    return functions


def parse_expected_data(raw_str):
    """解析预期列数据（支持换行分割的多个函数）- 保留换行以区分多个JSON对象"""
    functions = []
    if not raw_str:
        return functions

    try:
        # 关键修改：支持单个JSON对象、数组格式，以及换行分隔的多个JSON对象
        # 首先尝试解析为单个JSON对象或数组
        try:
            # 清理空格但保留换行符，以便能够区分多个JSON对象
            cleaned_str = raw_str.strip()

            # 添加调试日志
            if raw_str != cleaned_str:
                logger.debug(f"parse_expected_data - 清理前: {repr(raw_str[:100])}")
                logger.debug(f"parse_expected_data - 清理后: {repr(cleaned_str[:100])}")

            json_data = json.loads(cleaned_str)

            # 处理单个对象格式
            if isinstance(json_data, dict):
                parsed_func = parse_single_function(json_data)
                if parsed_func["name"]:
                    functions.append(parsed_func)
            # 处理数组格式
            elif isinstance(json_data, list):
                for item in json_data:
                    parsed_func = parse_single_function(item)
                    if parsed_func["name"]:
                        functions.append(parsed_func)
        except json.JSONDecodeError:
            # 如果不是单个对象或数组，尝试按换行分割处理多个JSON对象
            # 在这种情况下，我们需要清理每一行的前后空格，但保留换行作为分隔符
            lines = [line.strip() for line in raw_str.split('\n') if line.strip()]
            for line in lines:
                if line:  # 确保不是空行
                    try:
                        # 对每一行单独清理空格和格式
                        cleaned_line = clean_json_str_for_single_object(line)
                        json_obj = json.loads(cleaned_line)
                        parsed_func = parse_single_function(json_obj)
                        if parsed_func["name"]:
                            functions.append(parsed_func)
                    except json.JSONDecodeError as e:
                        logger.error(f"单行JSON解析失败!")
                        logger.error(f"  错误信息: {str(e)}")
                        logger.error(f"  原始行: {repr(line[:200])}")
                        logger.error(f"  清理后: {repr(cleaned_line[:200])}")
                        continue
    except Exception as e:
        logger.error(f"预期数据解析异常: {str(e)}, 原始字符串: {repr(raw_str[:200])}")
    return functions


def is_vehicle_brand_model_match(vehicle_brand, vehicle_model):
    """判断车辆品牌和车系是否匹配"""
    # 定义品牌和车系的对应关系（使用Unicode字符以匹配JSON解析结果）
    brand_models = {
        "\u7406\u60f3": [  # "理想"
            "one", "MEGA", "MEGA Home", "MEGA Ultra", "L9", "L9 Ultra", "L9 Pro", "L9 Max",
            "L8", "L8 Ultra", "L8 Pro", "L8 Max", "L8 Air", "L7", "L7 Ultra", "L7 Pro",
            "L7 Max", "L7 Air", "L6", "L6 Pro", "L6 Max", "i8", "i6"
        ],
        "\u7279\u65af\u62c9": [  # "特斯拉"
            "Model 3", "Model Y", "Model Y L", "Model S", "Model X", "Cybertruck"
        ]
    }

    # 检查品牌是否存在
    if vehicle_brand not in brand_models:
        return False

    # 检查车系是否属于该品牌
    return vehicle_model.lower() in [model.lower() for model in brand_models[vehicle_brand]]


def compare_dicts(expected, actual, case_id):
    """对比字典参数，处理车辆控制特殊情况"""
    errors = []

    # 字符串标准化：去除空格、统一大小写
    def normalize_value(val):
        if isinstance(val, str):
            return val.strip().lower()
        return val

    # 标准化expected和actual的值
    expected_norm = {k: normalize_value(v) for k, v in expected.items()}
    actual_norm = {k: normalize_value(v) for k, v in actual.items()}

    expected_keys = set(expected_norm.keys())
    actual_keys = set(actual_norm.keys())

    # 检查是否为特殊情况：标准答案只有vehicle_model，模型输出额外提供了vehicle_brand且匹配
    # 注意：expected是标准答案（预期APIINFO），actual是模型输出（第一步-规划）
    is_special_case = (
            'action' in expected_norm and expected_norm['action'] == 'switch' and
            'vehicle_model' in expected_norm and
            'vehicle_brand' not in expected_norm and  # 标准答案没有vehicle_brand
            'vehicle_brand' in actual_norm and  # 模型输出有vehicle_brand
            'vehicle_model' in actual_norm
    )

    if is_special_case:
        # 检查模型补充的品牌是否与车系匹配
        if is_vehicle_brand_model_match(actual_norm['vehicle_brand'], expected_norm['vehicle_model']):
            # 模型正确补充了品牌字段 → 从actual中临时移除vehicle_brand进行后续比较
            actual_for_comparison = {k: v for k, v in actual_norm.items() if k != 'vehicle_brand'}
            # 用移除brand后的actual与expected比较
            actual_keys = set(actual_for_comparison.keys())
        else:
            errors.append(
                f"模型补充的品牌与车系不匹配: 标准车系='{expected_norm['vehicle_model']}', 模型品牌='{actual_norm['vehicle_brand']}'")
            return errors  # 立即返回错误
    else:
        # 非特殊情况，直接使用原始actual
        actual_for_comparison = actual_norm

    # 检查缺少的字段（模型输出比标准答案少字段）
    missing_keys = expected_keys - actual_keys
    if missing_keys:
        errors.append(f"缺少预期字段: {', '.join(missing_keys)}")

    # 检查额外的字段（模型输出比标准答案多字段）
    extra_keys = actual_keys - expected_keys
    if extra_keys:
        # 特殊处理：如果不是特殊情况且额外字段包含vehicle_brand，则报错
        if not is_special_case and 'vehicle_brand' in extra_keys:
            errors.append(f"存在额外字段: vehicle_brand")
        else:
            # 其他额外字段直接报错
            extra_non_brand = extra_keys - {'vehicle_brand'}
            if extra_non_brand:
                errors.append(f"存在额外字段: {', '.join(extra_non_brand)}")

    # 比较公共字段的值
    common_keys = expected_keys & actual_keys
    for key in common_keys:
        expected_val = expected_norm[key]
        actual_val = actual_for_comparison[key]  # 使用处理后的actual

        if isinstance(expected_val, dict) and isinstance(actual_val, dict):
            sub_errors = compare_dicts(expected_val, actual_val, case_id)
            if sub_errors:
                errors.extend([f"在字段'{key}'中: {e}" for e in sub_errors])
        elif isinstance(expected_val, list) and isinstance(actual_val, list):
            if sorted(expected_val) != sorted(actual_val):
                errors.append(f"列表字段'{key}'值不匹配: 预期={expected_val}, 实际={actual_val}")
        else:
            if expected_val != actual_val:
                errors.append(f"字段'{key}'值不匹配: 预期='{expected_val}', 实际='{actual_val}'")

    return errors


def function_to_key(func):
    """将函数转换为可哈希的键（用于集合对比）"""
    # 键生成保持原始参数，不做特殊处理
    # 特殊处理应在比较阶段进行，而不是键生成阶段
    # 使用排序键确保相同内容的字典产生相同的字符串表示
    return (
        func["name"].lower(),
        json.dumps(func["arguments"], sort_keys=True)
    )


def compare_api_info(planning_raw, expected_raw, case_id):
    """对比多个函数（无序），集合完全一致则返回PASS"""
    plan_functions = parse_planning_data(planning_raw)
    expect_functions = parse_expected_data(expected_raw)

    # 添加调试日志
    logger.debug(f"CaseID: {case_id} - plan_functions: {plan_functions}")
    logger.debug(f"CaseID: {case_id} - expect_functions: {expect_functions}")

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

    # 特殊处理：车辆控制场景 - 允许"预期无vehicle_brand，实际有vehicle_brand且匹配"的情况
    # 需要在函数匹配前就处理这种特殊情况
    processed_plan_functions = []
    for plan_func in plan_functions:
        # 如果是车辆控制函数，检查是否为特殊情况
        if ('action' in plan_func['arguments'] and
                plan_func['arguments']['action'] == 'switch' and
                'vehicle_brand' in plan_func['arguments'] and
                'vehicle_model' in plan_func['arguments']):

            # 查找是否有一个对应的预期函数符合特殊情况
            matched = False
            for exp_func in expect_functions:
                if ('action' in exp_func['arguments'] and
                        exp_func['arguments']['action'] == 'switch' and
                        'vehicle_model' in exp_func['arguments'] and
                        'vehicle_brand' not in exp_func['arguments']):

                    # 检查车系是否相同
                    if exp_func['arguments']['vehicle_model'] == plan_func['arguments']['vehicle_model']:
                        # 检查品牌车系是否匹配
                        if is_vehicle_brand_model_match(
                                plan_func['arguments']['vehicle_brand'],
                                plan_func['arguments']['vehicle_model']
                        ):
                            # 这是特殊情况，创建一个临时标准化版本用于键比较
                            temp_args = {k: v for k, v in plan_func['arguments'].items() if k != 'vehicle_brand'}
                            temp_func = {
                                "name": plan_func["name"],
                                "arguments": temp_args
                            }
                            processed_plan_functions.append(temp_func)
                            matched = True
                            break

            if not matched:
                processed_plan_functions.append(plan_func)
        else:
            processed_plan_functions.append(plan_func)

    # 使用处理后的函数列表进行比较
    normalized_expect = expect_functions
    normalized_plan = processed_plan_functions

    # 2. 转换为可对比的集合（忽略顺序）
    plan_keys = set(function_to_key(func) for func in normalized_plan)
    expect_keys = set(function_to_key(func) for func in normalized_expect)

    # 添加调试日志
    logger.debug(f"CaseID: {case_id} - plan_keys: {plan_keys}")
    logger.debug(f"CaseID: {case_id} - expect_keys: {expect_keys}")

    # 3. 校验函数数量一致性
    if len(normalized_plan) != len(normalized_expect):
        error_details.append(f"函数数量不匹配：预期{len(normalized_expect)}个，规划{len(normalized_plan)}个")
        is_passed = False
    else:
        # 4. 对比集合差异（缺失和多余的函数）
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

    # 5. 处理无有效函数的特殊情况
    if not normalized_plan:
        error_details.append("规划列无有效函数")
        is_passed = False

    # 6. 关键补充：对每个匹配的函数对进行严格的字段级检查
    # 确保当预期有vehicle_brand而模型输出没有时，能正确识别为缺少字段
    if len(normalized_expect) == len(normalized_plan) and expect_keys == plan_keys:
        # 使用函数名和参数作为键来精确匹配对应的函数对
        expected_map = {function_to_key(func): func for func in normalized_expect}
        actual_map = {function_to_key(func): func for func in normalized_plan}

        for key in expected_map.keys():
            if key in actual_map:
                expected_func = expected_map[key]
                actual_func = actual_map[key]

                # 检查是否为车辆控制相关函数
                if ('action' in expected_func["arguments"] and
                        expected_func["arguments"]["action"] == 'switch'):

                    # 特殊处理已在函数匹配前完成，这里只需进行标准字段比较
                    field_errors = compare_dicts(expected_func["arguments"], actual_func["arguments"], case_id)
                    if field_errors:
                        error_details.extend([f"字段级比较错误: {e}" for e in field_errors])
                        is_passed = False
                        break

                    # 调试信息：打印预期和实际参数
                    logger.info(f"CaseID: {case_id} - 预期参数: {expected_func['arguments']}")
                    logger.info(f"CaseID: {case_id} - 实际参数: {actual_func['arguments']}")

    # 7. 记录错误日志并返回结果
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
            logger.info(f"已写入第 {i + 1}-{min(i + chunk_size, total_results)} 行结果")

            # 避免API频率限制
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"写入第 {i + 1}-{min(i + chunk_size, total_results)} 行结果时发生错误: {str(e)}")
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
FEISHU_DOC_URL = r'https://li.feishu.cn/sheets/Dn5qsVZW7hgTwCtJvNkcJJJondf?sheet=0gzWit'
EXPECTED_COLUMN_NAME = "预期APIINFO"
PLANNING_COLUMN_NAME = "第一步-规划"
RESULT_COLUMN_NAME = "APIINFO测试结果"
CASE_ID_COLUMN_NAME = "CaseID"
#dialogActsDassSlots、第一步-规划
# =========================================

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()