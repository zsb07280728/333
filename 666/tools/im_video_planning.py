#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
播客规划控制工具 - 最终修复版（解决name误判+startTime格式问题）
"""

import json
import logging
import re
import time
import os
import sys
from datetime import datetime
import uuid
import requests
from fastfeishu.exceptions.exception import FeiShuException
from fastfeishu.core import FeiShuSheet

# ====================== 全局配置 ======================
llm_cache = {}

LLM_CONFIG = {
    "url": "http://api-hub.inner.chj.cloud/llm-gateway/v1/chat/completions/azure-gpt-4o",
    "gw_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzb29icjRrdFFoU0ZGWGdNVnlhQkJSOFVLQ2ZjSHlxdSJ9.uyQX3JXjnYhvREYm4Yw8-6dcB4B-lGf6I-NO6Lz5cZc",
    "temperature": 0.1,
    "max_retries": 2,
    "base_sleep_time": 5,
    "request_interval": 0.5,
    "last_request_time": 0
}


# ====================== 日志配置 ======================
def setup_logger(task_type: str = "podcast_planning_im"):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "log", f"{task_type}_log")
    os.makedirs(log_dir, exist_ok=True)

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"{task_type}_{current_time}.log")

    logger = logging.getLogger(f"{task_type}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    class TerminalFilter(logging.Filter):
        def filter(self, record):
            return (record.levelno == logging.INFO and
                    ("启动" in record.getMessage() or "完成" in record.getMessage() or
                     "加载飞书文档" in record.getMessage() or "分片写入" in record.getMessage() or
                     "已处理" in record.getMessage()))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TerminalFilter())
    logger.addHandler(console_handler)

    return logger


# ====================== 核心工具函数 ======================
def safe_str(value):
    if value is None:
        return ""
    clean_val = str(value).strip()
    clean_val = re.sub(r'[\n\r\t\x00-\x1f\x7f-\x9f]', ' ', clean_val)
    clean_val = clean_val.replace('\u3000', ' ')
    return clean_val


def parse_first_function(raw_json: str, case_id: str = "未知") -> dict:
    """最终修复版：预处理startTime转义，保证name字段提取"""
    logger = logging.getLogger(__name__)
    if not raw_json:
        logger.warning(f"CaseID:{case_id} - JSON原始值为空")
        return {}

    try:
        # 关键：预处理startTime的嵌套字符串，修复转义
        cleaned = re.sub(r'[\n\r\t]', '', raw_json)
        cleaned = re.sub(r'(?<="startTime": ")(\{.*?\})(?=")', lambda m: m.group(1).replace('"', '\\"'), cleaned)

        parsed = json.loads(cleaned)
        if isinstance(parsed, list) and len(parsed) > 0:
            parsed = parsed[0]

        if isinstance(parsed, dict) and "function" in parsed:
            parsed = parsed["function"]

        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as e:
        logger.error(f"CaseID:{case_id} - JSON解析失败：{raw_json[:100]} | 错误：{e}")
        # 兜底：手动提取name字段，避免完全为空
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', raw_json)
        return {"name": name_match.group(1)} if name_match else {}


# ====================== LLM调用 ======================
def call_llm(prompt: str) -> str:
    if prompt in llm_cache:
        return llm_cache[prompt]

    current_time = time.time()
    time_since_last = current_time - LLM_CONFIG["last_request_time"]
    if time_since_last < LLM_CONFIG["request_interval"]:
        time.sleep(LLM_CONFIG["request_interval"] - time_since_last)

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

    for retry in range(LLM_CONFIG['max_retries']):
        try:
            response = requests.post(
                LLM_CONFIG['url'],
                headers=headers,
                json=request_data,
                timeout=60
            )
            LLM_CONFIG["last_request_time"] = time.time()
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            llm_cache[prompt] = content
            return content
        except requests.exceptions.HTTPError as e:
            if response and response.status_code == 429:
                sleep_time = LLM_CONFIG["base_sleep_time"] * (2 ** retry)
                logging.error(f"LLM限流（第{retry + 1}次重试）：休眠{sleep_time}秒")
                time.sleep(sleep_time)
            else:
                raise e
        except Exception as e:
            logging.error(f"LLM调用异常（第{retry + 1}次重试）：{e}")
            time.sleep(LLM_CONFIG["base_sleep_time"] * (2 ** retry))

    logging.error("LLM调用多次失败，返回空")
    llm_cache[prompt] = ""
    return ""


# ====================== 核心校验逻辑（最终修复版）=====================
def strict_field_validate(expected_val, actual_val, field_name, case_id, error_list, logger):
    """统一格式后严格对比，避免类型不一致导致的误判"""

    def normalize_val(val):
        val_str = safe_str(val)
        if isinstance(val, dict):
            return json.dumps(val, sort_keys=True, ensure_ascii=False)
        if val_str.startswith("{") and val_str.endswith("}"):
            try:
                return json.dumps(json.loads(val_str.replace("'", '"')), sort_keys=True, ensure_ascii=False)
            except:
                return val_str
        return val_str

    exp_norm = normalize_val(expected_val)
    act_norm = normalize_val(actual_val)

    if exp_norm != act_norm:
        error_msg = f"{field_name}不匹配：预期[{exp_norm}]，实际[{act_norm}]"
        error_list.append(error_msg)
        logger.info(f"CaseID:{case_id} - {error_msg}")
    else:
        logger.info(f"CaseID:{case_id} - {field_name}字段：完全匹配")


def compare_query_semantic(text1: str, text2: str) -> bool:
    t1, t2 = safe_str(text1), safe_str(text2)
    if t1 == "" and t2 == "":
        return True
    if t1 == "" or t2 == "":
        return False

    prompt = f"""请判定播客查询的两段文本核心语义是否一致，仅返回「是」或「否」。
规则：忽略语气词、语序差异，核心查询内容完全重叠即一致。
文本1（预期）：{t1}
文本2（实际）：{t2}"""

    resp = call_llm(prompt)
    clean_resp = re.sub(r'[^\u4e00-\u9fff]', '', resp.strip())
    return clean_resp == "是"


def compare_podcast_planning(actual_raw, expected_raw, case_id, logger):
    error_reasons = []

    # 解析JSON（修复版）
    actual_func = parse_first_function(actual_raw, case_id)
    expected_func = parse_first_function(expected_raw, case_id)

    # 提取字段（分层提取，保证name字段优先）
    act_name = actual_func.get("name", "")
    act_arguments = actual_func.get("arguments", {})
    act_mediaType = act_arguments.get("mediaType", "")
    act_startTime = act_arguments.get("startTime", "")
    act_query = act_arguments.get("query", "")
    act_range = act_arguments.get("range", "")

    exp_name = expected_func.get("name", "")
    exp_mediaType = expected_func.get("mediaType", "")
    exp_startTime = expected_func.get("startTime", "")
    exp_query = expected_func.get("query", "")
    exp_range = expected_func.get("range", "")

    # 空值检查
    all_act_empty = all([
        safe_str(act_name) == "",
        safe_str(act_mediaType) == "",
        safe_str(act_startTime) == "",
        safe_str(act_query) == "",
        safe_str(act_range) == ""
    ])
    if all_act_empty:
        error_reasons.append("模型输出核心字段全为空，判定不通过")
        final_result = "FAILED"
        error_reason = "; ".join(error_reasons)
        logger.info(f"CaseID:{case_id} - 整体结果: {final_result} | 详情: {error_reason}")
        return final_result, error_reason

    # 字段校验（按优先级，逐个校验）
    strict_field_validate(exp_name, act_name, "name", case_id, error_reasons, logger)
    strict_field_validate(exp_mediaType, act_mediaType, "mediaType", case_id, error_reasons, logger)
    strict_field_validate(exp_startTime, act_startTime, "startTime", case_id, error_reasons, logger)
    strict_field_validate(exp_range, act_range, "range", case_id, error_reasons, logger)

    # query语义匹配
    if not compare_query_semantic(exp_query, act_query):
        error_reasons.append(f"query语义不匹配：预期[{exp_query}]，实际[{act_query}]")
        logger.info(f"CaseID:{case_id} - query字段：语义不匹配")
    else:
        logger.info(f"CaseID:{case_id} - query字段：语义匹配通过")

    # 最终结果
    final_result = "PASS" if not error_reasons else "FAILED"
    error_reason = "; ".join(error_reasons) if error_reasons else "所有字段校验通过"
    logger.info(f"CaseID:{case_id} - 整体结果: {final_result} | 详情: {error_reason}")
    return final_result, error_reason


# ====================== 飞书读写 ======================
def write_results_in_chunks(sheet, column_name, data, chunk_size=50):
    logger = logging.getLogger(__name__)
    total = len(data)
    if total == 0:
        logger.info(f"无数据写入[{column_name}]")
        return

    cleaned_data = []
    for item in data:
        clean_item = safe_str(item)
        if len(clean_item) > 2000:
            clean_item = clean_item[:2000] + "..."
        cleaned_data.append(clean_item)

    logger.info(f"开始分片写入[{column_name}]，共{total}条数据，批次大小：{chunk_size}")
    success_count = 0

    for i in range(0, total, chunk_size):
        chunk = cleaned_data[i:i + chunk_size]
        start_row = i + 2

        for retry in range(3):
            try:
                time.sleep(1.0)
                sheet.write_column(column_name, chunk, start_row=start_row)

                batch_success = len(chunk)
                success_count += batch_success
                logger.info(f"已写入：{min(i + chunk_size, total)}/{total}条 | 累计成功：{success_count}条")

                if (i // chunk_size) % 10 == 0 and i > 0:
                    time.sleep(2.0)
                break

            except Exception as e:
                error_msg = str(e)[:100]
                if "400" in error_msg or "Bad Request" in error_msg:
                    logger.warning(f"批次{i // chunk_size + 1}失败（第{retry + 1}次）：400错误，拆分批次重试")
                    if retry == 1 and chunk_size > 10:
                        sub_chunk_size = 10
                        for sub_i in range(0, len(chunk), sub_chunk_size):
                            sub_chunk = chunk[sub_i:sub_i + sub_chunk_size]
                            sub_start_row = start_row + sub_i
                            time.sleep(1.5)
                            sheet.write_column(column_name, sub_chunk, start_row=sub_start_row)
                        success_count += len(chunk)
                        logger.info(f"拆分批次写入成功：{len(chunk)}条")
                        break
                else:
                    logger.warning(f"批次{i // chunk_size + 1}失败（第{retry + 1}次）：{error_msg}")

                time.sleep(2 * (retry + 1))
                if retry == 2:
                    logger.error(f"批次{i // chunk_size + 1}写入最终失败，已跳过")

    logger.info(f"写入完成[{column_name}]：总条数{total} | 成功{success_count}条 | 失败{total - success_count}条")


def read_sheet_in_small_batches(sheet, logger, batch_size=200):
    all_rows = []
    row_count = 0
    for idx, row in enumerate(sheet.iterrows()):
        row_count += 1
        all_rows.append(row)
        if row_count % batch_size == 0:
            logger.info(f"已读取{row_count}行数据")
            time.sleep(0.2)
    logger.info(f"表格读取完成，共{len(all_rows)}行有效数据")
    return all_rows


# ====================== 主函数 ======================
def main(feishu_doc_url: str = None):
    logger = setup_logger()
    logger.info("=== 播客规划控制工具（最终修复版）启动 ===")
    start_time = time.time()

    if not feishu_doc_url:
        feishu_doc_url = "https://li.feishu.cn/sheets/MnFxspT2jh4lyCtWCitcIffHn2b?sheet=IKFZIo"

    try:
        sheet = FeiShuSheet(feishu_doc_url)
        logger.info("成功加载飞书表格")
    except FeiShuException as e:
        logger.error(f"飞书表格连接失败：{e}")
        logger.error("请检查：1. 环境变量FS_APP_ID/FS_APP_SECRET 2. 表格权限 3. URL正确性")
        return

    all_cases = read_sheet_in_small_batches(sheet, logger)
    if not all_cases:
        logger.error("未读取到任何用例数据，程序退出")
        return

    results = []
    error_reasons = []
    total = len(all_cases)
    logger.info(f"开始处理{total}条播客用例...")

    for idx, (_, row) in enumerate(all_cases):
        row_count = idx + 1
        row = {k: v if v is not None else "" for k, v in row.items()}
        case_id = safe_str(row.get("CaseID", f"ROW_{row_count}"))
        exp_raw = safe_str(row.get("预期APIINFO", ""))
        actual_raw = safe_str(row.get("第一步-规划", ""))

        try:
            result, error_reason = compare_podcast_planning(actual_raw, exp_raw, case_id, logger)
        except Exception as e:
            logger.error(f"CaseID:{case_id} - 处理失败：{str(e)[:100]}")
            result = "FAILED"
            error_reason = f"用例处理异常：{str(e)[:100]}"

        results.append(result)
        error_reasons.append(error_reason)

        if row_count % 500 == 0:
            pass_count = results.count("PASS")
            pass_rate = pass_count / row_count * 100
            elapsed = time.time() - start_time
            remain = (total - row_count) * (elapsed / row_count) / 60
            logger.info(f"进度：{row_count}/{total}条 | 通过：{pass_count}（{pass_rate:.2f}%） | 预计剩余{remain:.1f}分钟")

    try:
        write_results_in_chunks(sheet, "APIINFO测试结果23", results)
        write_results_in_chunks(sheet, "播客-对比错误原因23", error_reasons)
    except Exception as e:
        logger.error(f"写入结果失败：{e}")
        local_file = f"podcast_results_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(local_file, 'w', encoding='utf-8') as f:
            json.dump({"results": results, "error_reasons": error_reasons}, f, ensure_ascii=False, indent=2)
        logger.info(f"结果已备份到本地文件：{local_file}")
        return

    pass_count = results.count("PASS")
    pass_rate = (pass_count / total * 100) if total > 0 else 0
    total_elapsed = time.time() - start_time
    logger.info(f"\n=== 执行完成（总耗时：{total_elapsed:.2f}秒 ≈ {total_elapsed / 60:.1f}分钟）===")
    logger.info(f"总用例数：{total} | 通过：{pass_count}（{pass_rate:.2f}%） | 失败：{total - pass_count}")
    logger.info(f"LLM缓存命中次数：{len(llm_cache) - sum(1 for v in llm_cache.values() if v == '')}")


if __name__ == "__main__":
    feishu_url = sys.argv[1] if len(sys.argv) > 1 else None
    main(feishu_url)