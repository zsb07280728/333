#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
必过集泛化工具 - LLM驱动的测试数据生成
功能：
1. 读取必过集（query + function）
2. 按 function 分组
3. LLM 泛化：1个query → N个语义相同但表达不同的query
4. 泛化要求：贴近生活、真实、自然
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

# ====================== 配置 ======================
# 泛化比例：1个原始query泛化成多少个新query
GENERALIZATION_RATIO = 2

# LLM配置
LLM_CONFIG = {
    "url": "http://api-hub.inner.chj.cloud/llm-gateway/v1/chat/completions/azure-gpt-4o",
    "gw_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzb29icjRrdFFoU0ZGWGdNVnlhQkJSOFVLQ2ZjSHlxdSJ9.uyQX3JXjnYhvREYm4Yw8-6dcB4B-lGf6I-NO6Lz5cZc",
    "temperature": 0.5,  # 降低温度，减少随机性，生成更稳定简洁的内容
    "max_retries": 2,
    "base_sleep_time": 3,
    "request_interval": 0.5,
    "last_request_time": 0
}

llm_cache = {}

# ====================== LLM调用 ======================
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
    log_dir = os.path.join(script_dir, "log", "generalize_log")
    os.makedirs(log_dir, exist_ok=True)
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_dir, f"generalize_{current_time}.log")

    logger = logging.getLogger("generalize")
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

def generalize_query(original_query: str, num_variants: int, logger, context = None) -> list:
    """
    使用LLM泛化单个query，生成多个语义相同但表达不同的变体

    Args:
        original_query: 原始query
        num_variants: 要生成的变体数量
        logger: 日志记录器
        context: 上下文信息（可选，包含L1、类别L2、语意等）

    Returns:
        泛化后的query列表
    """
    if not original_query:
        return []

    # 构建上下文信息（如果有的话）
    context_info = ""
    if context:
        l1 = context.get("L1", "")
        l2 = context.get("类别L2", "")
        semantic = context.get("语意", "")

        if l1 or l2 or semantic:
            context_info = "\n参考信息（帮助你理解query的意图和场景）：\n"
            if l1:
                context_info += f"- 大类场景：{l1}\n"
            if l2:
                context_info += f"- 具体意图：{l2}\n"
            if semantic:
                context_info += f"- 语义细节：{semantic}\n"
            context_info += "\n"

    prompt = f"""你是一个智能驾驶测试数据生成专家。请根据给定的用户query，生成{num_variants}个语义相同但表达方式不同的变体query。

【重要】泛化原则：
{context_info}
1. 语义完全一致：新query的意图、目标和原query必须完全相同
2. 保持核心关键词：功能性关键词必须保留，不能替换成近义词
   - 例如："泊车"不能改成"停车"
   - 例如："离车泊入"不能改成"下车停车"
   - 例如："搜索车位"不能改成"找车位"

3. 【关键】简洁为主（重中之重）：
   - 80%以上的变体必须在5-8个字以内
   - 最多20%可以适当加长（但不超过12个字）
   - 严禁添加任何背景、原因、情境描述
   - 每个变体必须不同，不能重复

4. 表达多样化方式（仅从以下选择，不要混用太多）：
   - 基础型：直接说功能（例：泊车、离车泊入）
   - 礼貌型：加"帮我"、"麻烦"（例：帮我泊车）
   - 语气型：加"吧"、"呢"（例：泊车吧）
   - 询问型：加"可以吗"（例：可以泊车吗）
   - 动词变化：换动词（例：开启泊车、启动泊车）

严格禁止：
❌ 绝不添加原因/背景/情境："我得先去拿东西"、"车位太窄"、"我赶时间"、"电不够了"
❌ 绝不生成超过12个字的变体
❌ 绝不生成相同或高度相似的变体
❌ 绝不过度堆砌语气词："麻烦帮我xxx吧"、"请帮我xxx呢"

标准示例（必须参考）：

示例1：
原始query：离车泊入
✅ 正确变体（简洁、多样）：
1. 离车泊入（保持原样）
2. 帮我离车泊入（加礼貌词）
3. 开启离车泊入（换动词）
4. 离车泊入吧（加语气）
5. 启动离车泊入（换动词）
6. 现在离车泊入（加时间）
7. 麻烦离车泊入（礼貌型）

❌ 错误变体（太啰嗦）：
- 麻烦帮我离车泊入吧，我得先去拿个东西（17字，有背景）
- 帮我开启离车泊入吧，车位太窄了（15字，有原因）
- 快点离车泊入吧，我赶时间（12字，有情境）

示例2：
原始query：泊车
✅ 正确变体：
1. 泊车（保持）
2. 帮我泊车（礼貌）
3. 开始泊车（动词）
4. 泊车吧（语气）
5. 启动泊车（动词）
6. 现在泊车（时间）
7. 可以泊车吗（询问）

❌ 错误变体：
- 我要泊车了，快点（9字，有状态）
- 帮我泊车吧，找个位置停（11字，有补充）

示例3：
原始query：搜索车位
✅ 正确变体：
1. 搜索车位（保持）
2. 帮我搜索车位（礼貌）
3. 开始搜索车位（动词）
4. 搜索车位吧（语气）
5. 搜索一下车位（口语）
6. 找找车位（注意："找找"是口语化"搜索"，不是替换关键词）

❌ 错误变体：
- 搜索车位，快点我赶时间（11字，有情境）
- 帮我搜索车位吧，停车场满了（13字，有背景）

现在请为下面的query生成{num_variants}个变体：
原始query：{original_query}

【严格要求】：
1. 每个变体必须在5-10个字
2. 80%以上必须在5-8个字
3. 绝不添加背景、原因、情境
4. 每个变体都不同
5. 直接输出，不要序号

变体："""

    logger.info(f"调用LLM泛化query：{original_query}")
    llm_result = call_llm(prompt)

    if not llm_result:
        logger.warning(f"LLM泛化失败，返回原query")
        return [original_query]

    # 解析LLM返回结果
    variants = [q.strip() for q in llm_result.split('\n') if q.strip()]

    # 过滤掉可能的序号（如"1. "、"变体1："等）
    cleaned_variants = []
    for v in variants:
        # 移除常见的序号格式
        v = v.lstrip('0123456789.')
        v = v.lstrip('变体')
        v = v.lstrip(':：')
        v = v.strip()

        # 后处理过滤：确保query简洁且符合要求
        if v and v != original_query:
            # 1. 过滤长度：超过15个字的直接丢弃（考虑中文字符）
            if len(v) > 15:
                logger.info(f"过滤过长query（{len(v)}字）：{v}")
                continue

            # 2. 过滤包含明显背景/原因的关键词
            bad_keywords = ['赶时间', '太窄', '拿东西', '电不够', '满了', '不够', '等会', '马上要', '因为', '所以']
            if any(keyword in v for keyword in bad_keywords):
                logger.info(f"过滤包含背景描述的query：{v}")
                continue

            # 3. 过滤包含逗号的（通常是复句，过于啰嗦）
            if '，' in v or ',' in v:
                logger.info(f"过滤包含逗号的复句：{v}")
                continue

            cleaned_variants.append(v)

    logger.info(f"LLM泛化成功，生成{len(cleaned_variants)}个有效变体（过滤后）")

    # 如果生成数量不足，补充原query
    while len(cleaned_variants) < num_variants:
        cleaned_variants.append(original_query)
        logger.warning(f"泛化数量不足，使用原query补充")

    return cleaned_variants[:num_variants]

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

                if "403" in error_msg or "Forbidden" in error_msg:
                    logger.error(f"权限错误：无法写入列[{column_name}]")
                    break

                time.sleep(2 * (retry + 1))
                if retry == 2:
                    logger.error(f"批次{i // chunk_size + 1}写入最终失败，已跳过")

    logger.info(f"写入完成[{column_name}]：总条数{total} | 成功写入{success_count}条")

# ====================== 主函数 ======================
def main(source_url: str = None, target_url: str = None, ratio: int = None):
    logger = setup_logger()
    logger.info("=== 必过集泛化工具启动 ===")
    start_time = time.time()

    # 获取泛化比例
    if ratio is None:
        ratio_input = input(f"请输入泛化比例（1个原query生成N个新query，默认{GENERALIZATION_RATIO}）：").strip()
        ratio = int(ratio_input) if ratio_input.isdigit() else GENERALIZATION_RATIO
    logger.info(f"泛化比例：1 → {ratio}")

    # 获取文档URL
    if not source_url:
        source_url = input("请输入源飞书文档URL（包含必过集query和function列）：").strip()

    if not target_url:
        target_url = input("请输入目标飞书文档URL（将写入泛化后的query和预期APIINFO列）：").strip()

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

    # 2.5. 检查表头
    if all_rows:
        first_row = all_rows[0][1]
        available_columns = list(first_row.keys())
        logger.info(f"源表格可用列名：{available_columns}")

    # 3. 按 ID 分组，并保存function
    id_groups = {}  # {id: {"queries": [...], "function": "..."}}

    for idx, (_, row) in enumerate(all_rows):
        row = {k: v or "" for k, v in row.items()}

        # 读取ID
        id_value = safe_str(row.get("ID", "") or row.get("id", ""))
        if not id_value:
            logger.warning(f"第{idx+1}行：ID为空，跳过")
            continue

        # 读取query和function
        query_text = safe_str(row.get("必过集query", "") or row.get("query", ""))
        function_text = safe_str(row.get("glasses_function", "") or row.get("function", "") or row.get("预期APIINFO", ""))

        if not query_text or not function_text:
            logger.warning(f"第{idx+1}行：query或function为空，跳过")
            continue

        # 单行query不需要拆分，直接作为单个query
        query_text = query_text.strip()
        logger.info(f"第{idx+1}行：ID={id_value}，query={query_text[:30]}...")

        # 按ID分组，同时保存function
        if id_value not in id_groups:
            id_groups[id_value] = {
                "queries": [],
                "function": function_text
            }
        else:
            # 如果同一ID对应不同function，取第一个function
            if id_groups[id_value]["function"] != function_text:
                logger.warning(f"第{idx+1}行：ID={id_value}对应多个不同的function，保持使用第一个function")

        # 将query加入该ID组
        id_groups[id_value]["queries"].append(query_text)

    logger.info(f"数据分组完成：共{len(id_groups)}个ID组")

    # 4. 泛化每个ID组的query（整体泛化，不是单独泛化）
    generalized_queries = []
    generalized_apiinfo = []
    total_generated = 0

    for id_idx, (id_value, group_data) in enumerate(id_groups.items(), 1):
        queries = group_data["queries"]
        function = group_data["function"]

        logger.info(f"正在处理ID组 {id_idx}/{len(id_groups)}：ID={id_value}")
        logger.info(f"该ID组有{len(queries)}个原始query：{queries}")
        logger.info(f"对应function：{function[:50]}...")

        # 计算需要新生成的数量：每个ID组固定生成ratio个新query
        # 例如：3个原始query属于同一ID组，比例1:20，该ID组固定生成20个新query（不是3*20=60个）
        need_to_generate = ratio
        logger.info(f"该ID组需要新生成：{need_to_generate}个泛化query（比例1:{ratio}）")

        # 泛化新的query（不包含原始query）
        generated_count = 0
        query_index = 0
        generated_set = set()  # 用于去重，记录已生成的query
        # 同时也要记录原始query，避免生成重复
        for q in queries:
            generated_set.add(q)

        # 批量生成策略：每次生成多个变体，提高效率
        batch_size = 5  # 每次生成5个变体
        max_attempts = need_to_generate * 10  # 最大尝试次数，避免无限循环
        attempt_count = 0

        while generated_count < need_to_generate and attempt_count < max_attempts:
            original_query = queries[query_index % len(queries)]

            # 计算还需要生成多少个
            remaining = need_to_generate - generated_count
            # 每次生成的数量：取batch_size和remaining中的较小值
            current_batch = min(batch_size, remaining * 2)  # 生成2倍数量，以应对去重

            logger.info(f"  基于原始query生成{current_batch}个变体：{original_query[:30]}...")

            # 批量泛化：生成多个变体
            variants = generalize_query(original_query, current_batch, logger, context=None)

            # 过滤重复的query
            for new_query in variants:
                if generated_count >= need_to_generate:
                    break

                # 去重检查：只有不重复的query才会被添加（包括不与原始query重复）
                if new_query not in generated_set:
                    generalized_queries.append(new_query)
                    generalized_apiinfo.append(function)
                    generated_set.add(new_query)
                    generated_count += 1
                    total_generated += 1
                    logger.info(f"  ✓ 已生成 {generated_count}/{need_to_generate}：{new_query[:30]}...")
                else:
                    logger.info(f"  ✗ 跳过重复query：{new_query[:30]}...")

            query_index += 1
            attempt_count += 1

        # 如果仍未达到目标数量，使用兜底策略强制生成
        if generated_count < need_to_generate:
            logger.warning(f"ID组 {id_value} 当前仅生成{generated_count}/{need_to_generate}个，启动兜底策略...")

            # 兜底策略：提高temperature并强制生成
            remaining_needed = need_to_generate - generated_count
            original_temp = LLM_CONFIG['temperature']

            try:
                # 临时提高temperature增加随机性
                LLM_CONFIG['temperature'] = 0.8
                logger.info(f"  提高LLM temperature至{LLM_CONFIG['temperature']}，增加变体多样性")

                # 再次尝试批量生成，每次生成更多变体
                fallback_attempts = 0
                max_fallback_attempts = 50

                while generated_count < need_to_generate and fallback_attempts < max_fallback_attempts:
                    original_query = queries[query_index % len(queries)]
                    remaining_needed = need_to_generate - generated_count

                    # 生成3倍数量来增加成功率
                    current_batch = min(remaining_needed * 3, 15)

                    logger.info(f"  兜底策略：第{fallback_attempts+1}次尝试，生成{current_batch}个变体")
                    variants = generalize_query(original_query, current_batch, logger, context=None)

                    for new_query in variants:
                        if generated_count >= need_to_generate:
                            break

                        if new_query not in generated_set:
                            generalized_queries.append(new_query)
                            generalized_apiinfo.append(function)
                            generated_set.add(new_query)
                            generated_count += 1
                            total_generated += 1
                            logger.info(f"  ✓ [兜底] 已生成 {generated_count}/{need_to_generate}：{new_query[:30]}...")
                        else:
                            logger.debug(f"  ✗ [兜底] 跳过重复query：{new_query[:30]}...")

                    query_index += 1
                    fallback_attempts += 1

            finally:
                # 恢复原始temperature
                LLM_CONFIG['temperature'] = original_temp

            # 最终检查
            if generated_count < need_to_generate:
                logger.error(f"ID组 {id_value} 最终未能达到目标：期望{need_to_generate}个，实际{generated_count}个")
            else:
                logger.info(f"ID组 {id_value} 通过兜底策略成功达到目标：{need_to_generate}个")

        logger.info(f"ID组 {id_idx} 完成：基于{len(queries)}个原始query，新生成{generated_count}个泛化query")

    # 统计总的原始query数量
    total_original_queries = sum(len(group["queries"]) for group in id_groups.values())

    logger.info(f"数据泛化完成：从{len(all_rows)}行原始数据中提取{total_original_queries}个query，基于{len(id_groups)}个ID组，共生成{total_generated}个新query（仅泛化数据）")

    # 5. 连接目标文档
    try:
        logger.info(f"正在连接目标文档：{target_url}")
        target_sheet = FeiShuSheet(target_url)
        logger.info("成功加载目标飞书表格")
    except FeiShuException as e:
        logger.error(f"目标飞书表格连接失败：{e}")
        return

    # 6. 写入目标文档
    try:
        logger.info("开始写入目标文档...")
        write_data_in_chunks(target_sheet, "query", generalized_queries)
        write_data_in_chunks(target_sheet, "预期APIINFO", generalized_apiinfo)
    except Exception as e:
        logger.error(f"写入失败：{e}")
        return

    # 7. 完成
    elapsed_time = time.time() - start_time
    total_original_queries = sum(len(group["queries"]) for group in id_groups.values())

    logger.info(f"\n=== 执行完成 ===")
    logger.info(f"总耗时：{elapsed_time:.2f}s ≈ {elapsed_time/60:.1f}min")
    logger.info(f"原始数据：{len(all_rows)}行，包含{total_original_queries}个query，{len(id_groups)}个ID组")
    logger.info(f"泛化后：生成{total_generated}条新query（泛化比例 1:{ratio}，仅输出泛化数据）")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        ratio_arg = int(sys.argv[3]) if len(sys.argv) >= 4 else None
        main(sys.argv[1], sys.argv[2], ratio_arg)
    else:
        main()
