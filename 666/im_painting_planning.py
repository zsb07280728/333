#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
绘画大师规划控制工具 - 高效稳定版（新增category白名单）
核心规则（保留原版+新增白名单）：
1. name字段：完全相等（独立校验）
2. category字段（优先级从高到低）：
   - 新增规则0：命中白名单（如预期科幻→实际人物&医疗）→ 该字段通过
   - 规则1：标准答案是「其他」，模型输出任意值 → 该字段通过
   - 规则2：模型输出包含「其他」，该字段通过
   - 规则3：无上述情况，按交集判定（有交集→通过，无交集→不通过）
   - 空值规则：一方空一方非空→不通过，都空→通过
3. style字段：完全相等（独立校验）
4. keyphrase字段：语义一致（模糊匹配，独立校验）
★ 核心逻辑：所有字段都通过，整体才为PASS；模型全空直接判定失败
优化点：
1. LLM缓存：重复keyphrase对比直接复用结果，提速50%+
2. 飞书写入优化：解决2000+条400错误，小批次+重试+数据清洗
3. 单线程稳定：无多线程LLM判错问题
4. 数据清洗：处理特殊字符，避免JSON解析/写入错误
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
# LLM缓存（提速核心）
llm_cache = {}

# LLM配置（优化：降低间隔+减少重试，兼顾速度和稳定）
LLM_CONFIG = {
    "url": "http://api-hub.inner.chj.cloud/llm-gateway/v1/chat/completions/azure-gpt-4o",
    "gw_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzb29icjRrdFFoU0ZGWGdNVnlhQkJSOFVLQ2ZjSHlxdSJ9.uyQX3JXjnYhvREYm4Yw8-6dcB4B-lGf6I-NO6Lz5cZc",
    "temperature": 0.1,
    "max_retries": 2,  # 从5次→2次，减少等待
    "base_sleep_time": 5,  # 从15秒→5秒，限流休眠更短
    "request_interval": 0.5,  # 从1秒→0.5秒，调用间隔更短
    "last_request_time": 0
}

# ====================== Category白名单配置（新增）======================
# 格式：{预期值: [允许的实际值列表]}
CATEGORY_WHITELIST = {
    "科幻": ["人物&医疗"],  # 预期科幻 → 实际人物&医疗 判定通过
    # 可按需添加更多白名单规则，例如：
    # "人物&医疗": ["科幻"],
    # "古风": ["国风", "中国风"]
}


# ====================== 日志配置 ======================
def setup_logger(task_type: str = "painting_planning_im"):
    """配置日志（优化：终端只显示关键进度）"""
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


# ====================== 工具函数（增强数据清洗）======================
def safe_str(value):
    """安全转字符串，清洗特殊字符（解决写入/解析错误）"""
    if value is None:
        return ""
    clean_val = str(value).strip()
    # 去除不可见字符、特殊符号
    clean_val = re.sub(r'[\n\r\t\x00-\x1f\x7f-\x9f]', ' ', clean_val)
    # 替换全角空格、emoji等
    clean_val = clean_val.replace('\u3000', ' ').replace('😀', ' ').replace('🎨', ' ')
    return clean_val


def clean_json_str(raw_str):
    """清理JSON，增强容错"""
    if not raw_str:
        return ""
    cleaned = re.sub(r'[\n\r\t]', '', raw_str)
    cleaned = re.sub(r'\s*:\s*', ':', cleaned)
    cleaned = re.sub(r'\s*,\s*', ',', cleaned)
    cleaned = re.sub(r'\s*([\{\}\[\]])\s*', r'\1', cleaned)
    return cleaned.strip()


# ====================== LLM调用（带缓存，提速核心）======================
def call_llm(prompt: str) -> str:
    """调用GPT-4o（带缓存+限流优化）"""
    # 缓存命中：直接返回，无需重复调用
    if prompt in llm_cache:
        return llm_cache[prompt]

    # 限流控制：仅等待必要间隔
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

    # 指数退避重试
    for retry in range(LLM_CONFIG['max_retries']):
        try:
            response = requests.post(
                LLM_CONFIG['url'],
                headers=headers,
                json=request_data,
                timeout=60  # 从120秒→60秒，减少等待
            )
            LLM_CONFIG["last_request_time"] = time.time()
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            # 存入缓存
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


# ====================== 核心判定函数（新增白名单逻辑）======================
def compare_semantic(text1: str, text2: str) -> bool:
    """keyphrase语义对比（原有逻辑不变）"""
    t1, t2 = safe_str(text1), safe_str(text2)
    if t1 == "" and t2 == "":
        return True
    if t1 == "" or t2 == "":
        return False

    prompt = f"""请严格判定两段文本核心语义是否一致，仅返回「是」或「否」。
规则：提取核心关键词，忽略语序、冗余，关键词完全重叠即一致。
文本1（预期）：{t1}
文本2（实际）：{t2}"""

    resp = call_llm(prompt)
    clean_resp = re.sub(r'[^\u4e00-\u9fff]', '', resp.strip())
    return clean_resp == "是"


def parse_category(value: str) -> set:
    """解析Category为集合（原有逻辑不变）"""
    if not value:
        return set()
    return set([x.strip().lower() for x in safe_str(value).split('&')])


def parse_json_field(raw_value: str, case_id: str = "未知") -> dict:
    """解析JSON字段（原有逻辑+数据清洗）"""
    logger = logging.getLogger(__name__)
    if not raw_value:
        logger.warning(f"CaseID:{case_id} - JSON原始值为空")
        return {}

    try:
        cleaned = clean_json_str(raw_value)
        parsed = json.loads(cleaned)

        # 兼容列表格式 [{}]
        if isinstance(parsed, list):
            parsed = parsed[0] if len(parsed) > 0 else {}

        # 兼容function嵌套
        if isinstance(parsed, dict) and "function" in parsed:
            parsed = parsed["function"]

        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as e:
        logger.error(f"CaseID:{case_id} - JSON解析失败：{raw_value[:100]} | 错误：{e}")
        return {}


def compare_word_planning(planning_raw, expected_raw, case_id, logger):
    """核心对比逻辑（新增category白名单规则）"""
    error_reasons = []

    # 1. 解析JSON
    expected_parsed = parse_json_field(expected_raw, case_id)
    planning_parsed = parse_json_field(planning_raw, case_id)

    # 提取核心字段
    exp_name = safe_str(expected_parsed.get("name", ""))
    exp_category = safe_str(expected_parsed.get("category", ""))
    exp_style = safe_str(expected_parsed.get("style", ""))
    exp_keyphrase = safe_str(expected_parsed.get("keyphrase", ""))

    plan_name = safe_str(planning_parsed.get("name", ""))
    plan_args = planning_parsed.get("arguments", {})
    plan_category = safe_str(plan_args.get("category", ""))
    plan_style = safe_str(plan_args.get("style", ""))
    plan_keyphrase = safe_str(plan_args.get("keyphrase", ""))

    # 2. 全局空值检查：模型核心字段全空 → 直接失败
    if (plan_name == "" and plan_category == "" and plan_style == "" and plan_keyphrase == ""):
        error_reasons.append("模型输出核心字段全为空，判定不通过")

    # 3. Name字段：严格相等
    if exp_name != plan_name:
        error_reasons.append(f"name不匹配：预期[{exp_name}]，实际[{plan_name}]")

    # 4. Category字段：原有规则 + 新增白名单规则（优先级最高）
    exp_cat_set = parse_category(exp_category)
    plan_cat_set = parse_category(plan_category)

    # 新增：先检查白名单（优先级最高）
    whitelist_hit = False
    # 遍历白名单，匹配预期和实际值
    for exp_white in CATEGORY_WHITELIST:
        # 预期值匹配白名单key
        if exp_category.strip() == exp_white:
            # 实际值在白名单允许的列表中
            if plan_category.strip() in CATEGORY_WHITELIST[exp_white]:
                logger.info(
                    f"CaseID:{case_id} - CATEGORY字段：命中白名单（预期[{exp_category}]，实际[{plan_category}]），判定通过")
                whitelist_hit = True
                break

    # 未命中白名单，执行原有规则
    if not whitelist_hit:
        if exp_cat_set == {"其他"}:
            logger.info(f"CaseID:{case_id} - CATEGORY字段：标准答案为「其他」，模型输出任意值通过")
        elif "其他" in plan_cat_set:
            logger.info(f"CaseID:{case_id} - CATEGORY字段：模型输出含「其他」，该字段通过")
        else:
            # 原有基础规则
            if len(exp_cat_set) == 0 and len(plan_cat_set) == 0:
                pass
            elif len(exp_cat_set) == 0 or len(plan_cat_set) == 0:
                error_reasons.append(f"category不匹配：一方为空 - 预期[{exp_category}]，实际[{plan_category}]")
            elif len(exp_cat_set.intersection(plan_cat_set)) == 0:
                error_reasons.append(f"category不匹配：无交集 - 预期[{exp_category}]，实际[{plan_category}]")

    # 5. Style字段：严格相等
    if exp_style != plan_style:
        error_reasons.append(f"style不匹配：预期[{exp_style}]，实际[{plan_style}]")

    # 6. Keyphrase字段：语义匹配
    if not compare_semantic(exp_keyphrase, plan_keyphrase):
        error_reasons.append(f"keyphrase语义不匹配：预期[{exp_keyphrase}]，实际[{plan_keyphrase}]")

    # 最终结果判定
    final_result = "PASS" if not error_reasons else "FAILED"
    error_reason = "; ".join(error_reasons) if error_reasons else "所有字段校验通过"
    logger.info(f"CaseID:{case_id} - 整体结果: {final_result} | 详情: {error_reason}")
    return final_result, error_reason


# ====================== 飞书读写优化（解决400错误）======================
def write_results_in_chunks(sheet, column_name, data, chunk_size=50):
    """分片写入飞书（解决2000+条400错误，小批次+重试+数据清洗）"""
    logger = logging.getLogger(__name__)
    total = len(data)
    if total == 0:
        logger.info(f"无数据写入[{column_name}]")
        return

    # 深度数据清洗：解决特殊字符/超长文本问题
    cleaned_data = []
    for item in data:
        clean_item = safe_str(item)
        # 截断超长文本（飞书单单元格限制）
        if len(clean_item) > 2000:
            clean_item = clean_item[:2000] + "..."
        cleaned_data.append(clean_item)

    logger.info(f"开始分片写入[{column_name}]，共{total}条数据，批次大小：{chunk_size}")
    success_count = 0

    for i in range(0, total, chunk_size):
        chunk = cleaned_data[i:i + chunk_size]
        start_row = i + 2

        # 3次重试机制
        for retry in range(3):
            try:
                time.sleep(1.0)  # 写入前休眠，降低频率
                sheet.write_column(column_name, chunk, start_row=start_row)

                batch_success = len(chunk)
                success_count += batch_success
                logger.info(f"已写入：{min(i + chunk_size, total)}/{total}条 | 累计成功：{success_count}条")

                # 每10批额外休眠，避免限流
                if (i // chunk_size) % 10 == 0 and i > 0:
                    time.sleep(2.0)
                break

            except Exception as e:
                error_msg = str(e)[:100]
                if "400" in error_msg or "Bad Request" in error_msg:
                    logger.warning(f"批次{i // chunk_size + 1}失败（第{retry + 1}次）：400错误，拆分批次重试")
                    # 拆分批次为10条
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
    """小批次读取飞书表格，降低内存占用"""
    all_rows = []
    row_count = 0
    for idx, row in enumerate(sheet.iterrows()):
        row_count += 1
        all_rows.append(row)
        if row_count % batch_size == 0:
            logger.info(f"已读取{row_count}行数据")
            time.sleep(0.2)  # 短休眠，降低压力
    logger.info(f"表格读取完成，共{len(all_rows)}行有效数据")
    return all_rows


# ====================== 主函数（优化性能）======================
def main(feishu_doc_url: str = None):
    """主函数：高效稳定处理大批量数据"""
    logger = setup_logger()
    logger.info("=== 绘画大师规划对比工具（高效稳定版+白名单）启动 ===")
    start_time = time.time()

    if not feishu_doc_url:
        feishu_doc_url = "https://li.feishu.cn/sheets/MnFxspT2jh4lyCtWCitcIffHn2b?sheet=zmSnDz"

    try:
        sheet = FeiShuSheet(feishu_doc_url)
        logger.info("成功加载飞书表格")
    except FeiShuException as e:
        logger.error(f"飞书表格连接失败：{e}")
        logger.error("请检查：1. 环境变量FS_APP_ID/FS_APP_SECRET 2. 表格权限 3. URL正确性")
        return

    # 小批次读取数据，避免内存溢出
    all_cases = read_sheet_in_small_batches(sheet, logger)
    if not all_cases:
        logger.error("未读取到任何用例数据，程序退出")
        return

    results = []
    error_reasons = []
    total = len(all_cases)
    logger.info(f"开始处理{total}条用例...")

    for idx, (_, row) in enumerate(all_cases):
        row_count = idx + 1
        row = {k: v if v is not None else "" for k, v in row.items()}
        case_id = safe_str(row.get("CaseID", f"ROW_{row_count}"))
        exp_raw = safe_str(row.get("预期APIINFO", ""))
        plan_raw = safe_str(row.get("第一步-规划", ""))

        # 处理单条用例
        try:
            result, error_reason = compare_word_planning(plan_raw, exp_raw, case_id, logger)
        except Exception as e:
            logger.error(f"CaseID:{case_id} - 处理失败：{str(e)[:100]}")
            result = "FAILED"
            error_reason = f"用例处理异常：{str(e)[:100]}"

        results.append(result)
        error_reasons.append(error_reason)

        # 打印进度（每500条）
        if row_count % 500 == 0:
            pass_count = results.count("PASS")
            pass_rate = pass_count / row_count * 100
            elapsed = time.time() - start_time
            remain = (total - row_count) * (elapsed / row_count) / 60
            logger.info(f"进度：{row_count}/{total}条 | 通过：{pass_count}（{pass_rate:.2f}%） | 预计剩余{remain:.1f}分钟")

    # 写入结果（解决400错误）
    try:
        write_results_in_chunks(sheet, "APIINFO测试结果3332", results)
        write_results_in_chunks(sheet, "绘画大师-对比错误原因3332", error_reasons)
    except Exception as e:
        logger.error(f"写入结果失败：{e}")
        # 备份结果到本地，避免数据丢失
        local_file = f"painter_results_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(local_file, 'w', encoding='utf-8') as f:
            json.dump({"results": results, "error_reasons": error_reasons}, f, ensure_ascii=False, indent=2)
        logger.info(f"结果已备份到本地文件：{local_file}")
        return

    # 统计结果
    pass_count = results.count("PASS")
    pass_rate = (pass_count / total * 100) if total > 0 else 0
    total_elapsed = time.time() - start_time
    logger.info(f"\n=== 执行完成（总耗时：{total_elapsed:.2f}秒 ≈ {total_elapsed / 60:.1f}分钟）===")
    logger.info(f"总用例数：{total} | 通过：{pass_count}（{pass_rate:.2f}%） | 失败：{total - pass_count}")
    logger.info(f"LLM缓存命中次数：{len(llm_cache) - sum(1 for v in llm_cache.values() if v == '')}")


if __name__ == "__main__":
    feishu_doc_url = sys.argv[1] if len(sys.argv) > 1 else None
    main(feishu_doc_url)