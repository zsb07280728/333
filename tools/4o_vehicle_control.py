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
    log_dir = os.path.join(script_dir, "log", "4o_vehicle_control_log")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"创建日志文件夹: {log_dir}")

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"4o_vehicle_control_{current_time}.log")

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


def deep_sort_dict(d):
    if isinstance(d, dict):
        return {k: deep_sort_dict(v) for k, v in sorted(d.items())}
    elif isinstance(d, list):
        sorted_items = [deep_sort_dict(item) for item in d]
        return sorted(sorted_items)
    else:
        return d


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


def parse_arguments(raw_str, case_id="???"):
    if not raw_str:
        return {}

    try:
        cleaned_str = re.sub(r'\s+', ' ', raw_str.strip())
        cleaned_str = re.sub(r'\}\}+$', '}', cleaned_str)
        parsed = json.loads(cleaned_str)
        return deep_sort_dict(parsed)
    except json.JSONDecodeError as e:
        logger.warning(f"CaseID: {case_id} - 参数解析失败: {str(e)}, 原始数据: {raw_str[:50]}")
        return {"_parse_error": str(e)}


def compare_dicts(expected, actual, case_id):
    errors = []

    expected_keys = set(expected.keys())
    actual_keys = set(actual.keys())

    # 正确逻辑：expected是标准答案（arguments），actual是模型输出（rarguments）
    # 检查是否为特殊情况：标准答案只有vehicle_model，模型输出额外提供了vehicle_brand且匹配
    is_special_case = (
            'action' in expected and expected['action'] == 'switch' and
            'vehicle_model' in expected and
            'vehicle_brand' not in expected and  # 标准答案没有vehicle_brand
            'vehicle_brand' in actual and  # 模型输出有vehicle_brand
            'vehicle_model' in actual
    )

    if is_special_case:
        # 检查模型补充的品牌是否与车系匹配
        if is_vehicle_brand_model_match(actual['vehicle_brand'], expected['vehicle_model']):
            # 模型正确补充了品牌字段 → 从actual中临时移除vehicle_brand进行后续比较
            actual_for_comparison = {k: v for k, v in actual.items() if k != 'vehicle_brand'}
            # 用移除brand后的actual与expected比较
            actual_keys = set(actual_for_comparison.keys())
        else:
            errors.append(
                f"模型补充的品牌与车系不匹配: 标准车系='{expected['vehicle_model']}', 模型品牌='{actual['vehicle_brand']}'")
            return errors  # 立即返回错误
    else:
        # 非特殊情况，直接使用原始actual
        actual_for_comparison = actual

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
        expected_val = expected[key]
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


# ---------------------- 多结果解析与对比核心逻辑（关键修改） ----------------------
def parse_multi_results(name_str, args_str, case_id):
    """解析多结果数据（支持 & 分割、换行分割、JSON数组格式），优化空行和重复项处理"""
    results = []

    # ---------------------- 关键修改1：支持 & 分割name（同时保留换行分割） ----------------------
    # 先按 & 分割，再按换行分割，最后过滤空值和纯空格
    names = []
    if name_str:
        # 按 & 分割 → 再按换行分割 → 过滤空值
        split_by_amp = [n.strip() for n in name_str.split('&') if n.strip()]
        for item in split_by_amp:
            names.extend([n.strip() for n in item.split('\n') if n.strip()])

    # ---------------------- 关键修改2：支持 & 分割arguments（同时保留换行分割） ----------------------
    args_list = []
    if args_str:
        # 先按 & 分割多个JSON → 再按换行分割 → 过滤空值
        raw_args_items = [line.strip() for line in args_str.split('&') if line.strip()]
        split_args_lines = []
        for item in raw_args_items:
            split_args_lines.extend([line.strip() for line in item.split('\n') if line.strip()])

        if split_args_lines:
            for line in split_args_lines:
                parsed = parse_arguments(line, case_id)
                if parsed and "_parse_error" not in parsed:  # 过滤解析失败的项
                    args_list.append(parsed)
        else:
            # 尝试解析为JSON数组
            parsed_arr = parse_arguments(args_str, case_id)
            if isinstance(parsed_arr, list):
                args_list = [deep_sort_dict(item) for item in parsed_arr if
                             isinstance(item, dict) and "_parse_error" not in item]
            elif isinstance(parsed_arr, dict) and "_parse_error" not in parsed_arr:
                args_list.append(parsed_arr)

    # 核心逻辑：name和args一对一匹配（数量必须完全一致，否则丢弃不匹配项）
    if len(names) == len(args_list):
        for i in range(len(names)):
            results.append({
                "name": names[i],
                "args": args_list[i]
            })
    else:
        logger.warning(
            f"CaseID: {case_id} - name和args数量不匹配（name:{len(names)}个, args:{len(args_list)}个），跳过该组对比")

    # 去重（避免重复项导致的误判）
    unique_results = []
    seen_keys = set()
    for res in results:
        key = result_to_key(res)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_results.append(res)
    return unique_results


def result_to_key(result):
    """将结果转换为可哈希的键（用于无序集合对比）"""
    # 特殊处理：车辆控制相关的数据，只允许特定情况下的vehicle_brand字段
    args = result["args"]

    # 如果是车辆控制相关数据
    if ('action' in args and args['action'] == 'switch' and
            'vehicle_model' in args):

        # 关键修正：只有当标准答案（预期）没有vehicle_brand，而实际结果有vehicle_brand且匹配时，才忽略vehicle_brand
        # 但在result_to_key函数中我们无法直接知道这是预期还是实际，所以需要在调用处处理
        # 因此，这里保持原有逻辑，但依赖compare_multi_api_fields中的逻辑来确保正确性

        # 检查是否有vehicle_brand字段
        if 'vehicle_brand' in args:
            # 验证vehicle_brand是否与vehicle_model匹配
            if is_vehicle_brand_model_match(args['vehicle_brand'], args['vehicle_model']):
                # 创建键时忽略vehicle_brand字段（仅用于特殊情况）
                args_for_key = {k: v for k, v in args.items() if k != 'vehicle_brand'}
                return (
                    result["name"].lower(),
                    json.dumps(args_for_key, sort_keys=True)
                )

    # 默认行为
    return (
        result["name"].lower(),  # name不区分大小写
        json.dumps(result["args"], sort_keys=True)  # args无序对比（排序后序列化）
    )


def compare_multi_api_fields(expected_name_str, expected_args_str, actual_rname_str, actual_rargs_str, case_id):
    """对比多结果（无序），集合完全一致则返回PASS + 精准匹配预期为空的判定规则"""
    error_details = [f"CaseID: {case_id}"]
    is_passed = True

    # ============ 核心修改2：最优先执行【你的唯一规则】- 绝对精准 ============
    # 规则1: 预期name+args 纯空白 + 实际rname+rargs 纯空白 → 返回 PASS
    if expected_name_str == "" and expected_args_str == "" and actual_rname_str == "" and actual_rargs_str == "":
        return "PASS"
    # 规则2: 预期name+args 纯空白 + 实际rname+rargs 有任意内容 → 返回 FAILED + 日志报错
    elif expected_name_str == "" and expected_args_str == "" and (actual_rname_str != "" or actual_rargs_str != ""):
        error_details.append("预期name+arguments均为纯空白无任何内容，但是实际rname+rarguments有值，判定为错误")
        logger.error("\n".join(error_details))
        return "FAILED"

    # 1. 解析预期和实际的多结果数据（已支持&分割）
    expected_results = parse_multi_results(expected_name_str, expected_args_str, case_id)
    actual_results = parse_multi_results(actual_rname_str, actual_rargs_str, case_id)

    # 2. 校验结果数量一致性
    if len(expected_results) != len(actual_results):
        error_details.append(f"结果数量不匹配: 预期{len(expected_results)}个，实际{len(actual_results)}个")
        is_passed = False
    else:
        # 3. 转换为集合进行无序对比（核心：忽略顺序，只比内容）
        expected_keys = set(result_to_key(res) for res in expected_results)
        actual_keys = set(result_to_key(res) for res in actual_results)

        # 查找缺失和多余的结果
        missing = expected_keys - actual_keys
        if missing:
            error_details.append("实际结果缺少以下预期项：")
            for name, args_str in missing:
                error_details.append(f"  名称: {name}, 参数: {args_str}")
            is_passed = False

        extra = actual_keys - expected_keys
        if extra:
            error_details.append("实际结果多出以下非预期项：")
            for name, args_str in extra:
                error_details.append(f"  名称: {name}, 参数: {args_str}")
            is_passed = False

    # 4. 检查解析错误
    for res in expected_results + actual_results:
        if "_parse_error" in res["args"]:
            error_details.append(f"参数解析错误: {res['args']['_parse_error']}")
            is_passed = False

    # 5. 输出错误日志并返回结果
    if not is_passed:
        logger.error("\n".join(error_details))
        return "FAILED"

    # 6. 额外验证：对每个匹配的函数对进行详细字段比较
    # 如果数量匹配且集合匹配，但仍需确保字段级正确
    if len(expected_results) == len(actual_results) and expected_keys == actual_keys:
        for i in range(len(expected_results)):
            expected_func = expected_results[i]
            actual_func = actual_results[i]
            # 检查是否为车辆控制相关函数
            if ('action' in expected_func["args"] and
                    expected_func["args"]["action"] == 'switch' and
                    'vehicle_model' in expected_func["args"]):

                # 详细比较字段
                field_errors = compare_dicts(expected_func["args"], actual_func["args"], case_id)
                if field_errors:
                    error_details.extend([f"字段级比较错误: {e}" for e in field_errors])
                    is_passed = False
                    break

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
    logger.info("=== 4o_vehicle_control API对比工具启动 ===")
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
        result = compare_multi_api_fields(row['name'], row['arguments'], row['rname'], row['rarguments'], row['CaseID'])
        results_with_indices.append(result)

        # 每处理1000行报告一次进度
        if (idx + 1) % 1000 == 0:
            logger.info(f"已处理 {idx + 1} 行数据")

    # 分片写入数据以避免API限制
    write_results_in_chunks(sheet, RESULT_COLUMN_NAME, results_with_indices)

    logger.info(f"=== 4o_vehicle_control 流程完成，耗时: {time.time() - start_time:.2f}秒 ===")


# ================= 配置项 =================
FEISHU_DOC_URL = r'https://li.feishu.cn/sheets/BiJTsozjFhTURAts5DecfaMLnQg?sheet=8qjVBv'  # 具体Sheet链接
EXPECTED_NAME_COLUMN = "name"  # 预期name列名
EXPECTED_ARGS_COLUMN = "arguments"  # 预期arguments列名
ACTUAL_RNAME_COLUMN = "rname"  # 实际rname列名
ACTUAL_RARGS_COLUMN = "rarguments"  # 实际rarguments列名
RESULT_COLUMN_NAME = "APIINFO测试结果000"  # 结果写入列名
CASE_ID_COLUMN_NAME = "CaseID"  # CaseID列名
# =========================================

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
