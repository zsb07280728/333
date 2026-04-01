#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
必过集query拆分工具
功能：读取飞书必过集，将一个单元格中的多个query拆分成多行，写入新文档
"""

import logging
import time
import os
import sys
import uuid
import requests
from datetime import datetime
from fastfeishu.exceptions.exception import FeiShuException
from fastfeishu.core import FeiShuSheet

# ====================== LLM 配置 ======================
LLM_CONFIG = {
    "url": "http://api-hub.inner.chj.cloud/llm-gateway/v1/chat/completions/azure-gpt-4o",
    "gw_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzb29icjRrdFFoU0ZGWGdNVnlhQkJSOFVLQ2ZjSHlxdSJ9.uyQX3JXjnYhvREYm4Yw8-6dcB4B-lGf6I-NO6Lz5cZc",
    "temperature": 0.1,
    "max_retries": 2,
    "base_sleep_time": 3,
    "request_interval": 0.5,
    "last_request_time": 0
}

# LLM 缓存
llm_cache = {}

def call_llm(prompt: str) -> str:
    """调用LLM（带缓存）"""
    global llm_cache
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

    for retry in range(LLM_CONFIG["max_retries"]):
        try:
            response = requests.post(LLM_CONFIG['url'], headers=headers, json=request_data, timeout=60)
            LLM_CONFIG["last_request_time"] = time.time()
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            llm_cache[prompt] = content
            return content
        except Exception as e:
            logging.error(f"LLM调用失败 第{retry+1}次: {e}")
            time.sleep(LLM_CONFIG["base_sleep_time"] * (2 ** retry))

    logging.error("LLM调用最终失败，返回空")
    llm_cache[prompt] = ""
    return ""

# ====================== 日志 ======================
def setup_logger():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "log", "split_query_log")
    os.makedirs(log_dir, exist_ok=True)
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"split_query_{current_time}.log")

    logger = logging.getLogger("split_query")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# ====================== 工具函数 ======================
def safe_str(value):
    return str(value).strip() if value is not None else ""

def split_queries_with_llm(query_text: str, logger) -> list:
    """
    使用LLM智能拆分query文本为多个独立query
    支持：
    1. 换行符分隔的query
    2. 连续文本中的多个独立query
    """
    if not query_text:
        return []

    # 如果包含换行符，先按换行符拆分
    if '\n' in query_text:
        queries = [q.strip() for q in query_text.split('\n') if q.strip()]
        logger.info(f"按换行符拆分，得到{len(queries)}个query")
        return queries

    # 单行文本，使用LLM智能拆分
    prompt = f"""请将下面这段文本拆分成多个独立的用户query，每个query一行。

规则：
1. 识别文本中的多个独立意图/请求
2. 每个query保持完整语义
3. 直接输出拆分结果，每个query一行，不要添加序号、标点或其他内容
4. 如果只有一个query，就输出一行

示例：
输入：我要右边停车帮我前面停车开始左边停车
输出：
我要右边停车
帮我前面停车
开始左边停车

现在请拆分：
{query_text}"""

    logger.info(f"调用LLM拆分query：{query_text[:50]}...")
    llm_result = call_llm(prompt)

    if not llm_result:
        logger.warning(f"LLM拆分失败，返回原文本")
        return [query_text]

    # 解析LLM返回结果
    queries = [q.strip() for q in llm_result.split('\n') if q.strip()]
    logger.info(f"LLM拆分成功，得到{len(queries)}个query")
    return queries

# ====================== 飞书读写 ======================
def read_sheet_data(sheet, logger):
    """读取飞书表格数据"""
    all_rows = []
    row_count = 0
    for idx, row in enumerate(sheet.iterrows()):
        row_count += 1
        all_rows.append(row)
        if row_count % 100 == 0:
            logger.info(f"已读取{row_count}行数据")
    logger.info(f"表格读取完成，共{len(all_rows)}行有效数据")
    return all_rows

def write_data_in_chunks(sheet, column_name, data, chunk_size=50, start_row=2):
    """分片写入飞书（带数据清洗）"""
    logger = logging.getLogger(__name__)
    total = len(data)
    if total == 0:
        logger.info(f"无数据写入[{column_name}]")
        return

    # 数据清洗
    cleaned_data = []
    for item in data:
        clean_item = safe_str(item)
        if len(clean_item) > 2000:
            clean_item = clean_item[:2000] + "..."
        clean_item = clean_item.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        cleaned_data.append(clean_item)

    logger.info(f"开始分片写入[{column_name}]，共{total}条数据，批次大小：{chunk_size}")

    success_count = 0
    for i in range(0, total, chunk_size):
        chunk = cleaned_data[i:i + chunk_size]
        current_row = start_row + i

        for retry in range(3):
            try:
                time.sleep(1.0)
                sheet.write_column(column_name, chunk, start_row=current_row)
                success_count += len(chunk)
                logger.info(f"已写入：{min(i + chunk_size, total)}/{total}条 | 累计成功：{success_count}条")

                if (i // chunk_size) % 10 == 0 and i > 0:
                    time.sleep(2.0)
                break

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"写入批次{i // chunk_size + 1}失败（第{retry + 1}次重试）")
                logger.warning(f"错误详情：{error_msg}")
                logger.warning(f"尝试写入位置：行{current_row}~{current_row + len(chunk) - 1}，列{column_name}")

                # 403权限错误，直接记录并跳过
                if "403" in error_msg or "Forbidden" in error_msg:
                    logger.error(f"权限错误：无法写入列[{column_name}]的行{current_row}开始的数据")
                    logger.error("可能原因：1) 飞书应用缺少写入权限 2) 目标表格被保护 3) 协作权限不足")
                    if retry == 2:  # 最后一次重试失败
                        logger.error(f"批次{i // chunk_size + 1}最终放弃，建议检查飞书权限设置")
                    break  # 403错误不需要重试

                time.sleep(2 * (retry + 1))
                if retry == 2:
                    logger.error(f"批次{i // chunk_size + 1}写入最终失败，已跳过（列：{column_name}）")

    logger.info(f"写入完成[{column_name}]：总条数{total} | 成功写入{success_count}条")

# ====================== 主函数 ======================
def main(source_url: str = None, target_url: str = None):
    logger = setup_logger()
    logger.info("=== 必过集query拆分工具启动 ===")
    start_time = time.time()

    # 默认源文档和目标文档URL
    if not source_url:
        source_url = input("请输入源飞书文档URL（包含必过集query和glasses_function列）：").strip()

    if not target_url:
        target_url = input("请输入目标飞书文档URL（将写入query和预期APIINFO列）：").strip()

    # 1. 读取源文档
    try:
        logger.info(f"正在连接源文档：{source_url}")
        source_sheet = FeiShuSheet(source_url)
        logger.info("成功加载源飞书表格")
    except FeiShuException as e:
        logger.error(f"源飞书表格连接失败：{e}")
        return

    # 2. 读取数据
    all_rows = read_sheet_data(source_sheet, logger)
    if not all_rows:
        logger.error("未读取到数据")
        return

    # 2.5. 检查表头，帮助用户确认列名
    if all_rows:
        first_row = all_rows[0][1]
        available_columns = list(first_row.keys())
        logger.info(f"源表格可用列名：{available_columns}")

    # 3. 处理数据：拆分query
    queries_list = []
    apiinfo_list = []
    total_queries = 0

    for idx, (_, row) in enumerate(all_rows):
        row = {k: v or "" for k, v in row.items()}

        # 读取必过集query和glasses_function（支持多种可能的列名）
        query_text = safe_str(row.get("必过集query", "") or row.get("必过集Query", "") or row.get("query", ""))
        function_text = safe_str(row.get("glasses_function", "") or row.get("function", ""))

        # 只有当两列都为空时才跳过
        if not query_text and not function_text:
            logger.warning(f"第{idx+1}行：必过集query和glasses_function都为空，跳过")
            continue

        # 其他情况都正常处理（允许其中一列为空）

        # 拆分query（使用LLM智能拆分）
        # 如果query_text为空，也要写入一条记录（query为空，function有值的情况）
        if query_text:
            queries = split_queries_with_llm(query_text, logger)
            logger.info(f"第{idx+1}行：拆分出{len(queries)}个query，function={function_text[:50] if function_text else '空'}...")
        else:
            # query为空但function有值，写入一条空query记录
            queries = [""]
            logger.info(f"第{idx+1}行：query为空，保留function={function_text[:50]}...")

        # 每个query对应同一个function
        for query in queries:
            queries_list.append(query)
            apiinfo_list.append(function_text)
            total_queries += 1

    logger.info(f"数据处理完成：共拆分出{total_queries}条query")

    # 4. 连接目标文档
    try:
        logger.info(f"正在连接目标文档：{target_url}")
        target_sheet = FeiShuSheet(target_url)
        logger.info("成功加载目标飞书表格")
    except FeiShuException as e:
        logger.error(f"目标飞书表格连接失败：{e}")
        return

    # 5. 写入目标文档
    try:
        logger.info("开始写入目标文档...")
        write_data_in_chunks(target_sheet, "query", queries_list)
        write_data_in_chunks(target_sheet, "预期APIINFO", apiinfo_list)
    except Exception as e:
        logger.error(f"写入失败：{e}")
        return

    # 6. 完成
    elapsed_time = time.time() - start_time
    logger.info(f"\n=== 执行完成 ===")
    logger.info(f"总耗时：{elapsed_time:.2f}s ≈ {elapsed_time/60:.1f}min")
    logger.info(f"共处理{len(all_rows)}行必过集，拆分出{total_queries}条query")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        main(sys.argv[1], sys.argv[2])
    else:
        main()
