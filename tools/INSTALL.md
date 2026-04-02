# 独立工具包安装和使用指南

这是一个完全独立的工具包，包含以下工具：
- [4o_vehicle_control.py](file:///Users/zhaoshuaibo/Code/vehicle_control_tools/tools/4o_vehicle_control.py) - 4O链路车辆控制功能测试工具
- [im_vehicle_control.py](file:///Users/zhaoshuaibo/Code/vehicle_control_tools/tools/im_vehicle_control.py) - IM链路车辆控制功能测试工具
- [4o_note_planning.py](file:///Users/zhaoshuaibo/Code/ALL/auto_eval_project/tools/4o_note.py) - 4O链路记事本规划功能测试工具
- [im_note_planning.py](file:///Users/zhaoshuaibo/Code/ALL/auto_eval_project/tools/4o_note.py) - IM链路记事本规划功能测试工具

## 目录结构

```
vehicle_control_tools/
├── tools/                 # 工具脚本
│   ├── 4o_vehicle_control.py         # 4O链路车辆控制功能测试工具
│   ├── im_vehicle_control.py         # IM链路车辆控制功能测试工具
│   ├── 4o_note_planning.py           # 4O链路记事本规划功能测试工具
│   └── im_note_planning.py           # IM链路记事本规划功能测试工具
├── fastfeishu/            # 飞书API集成库
│   ├── __init__.py
│   ├── configs/
│   │   ├── __init__.py
│   │   ├── properties.yaml
│   │   └── settings.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── request.py
│   │   ├── sheet.py
│   │   └── [other files...]
│   ├── exceptions/
│   │   ├── __init__.py
│   │   └── [other files...]
│   ├── models/
│   │   ├── __init__.py
│   │   └── [other files...]
│   └── utils/
│       ├── __init__.py
│       └── [other files...]
├── requirements.txt       # 项目依赖
├── setup.py              # 包配置
├── README.md             # 说明文档
└── INSTALL.md            # 安装说明文档
```

## 安装步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. （可选）安装为Python包

```bash
pip install -e .
```

## 使用方法

### 4O链路车辆控制工具

```bash
python tools/4o_vehicle_control.py
```

### IM链路车辆控制工具

```bash
python tools/im_vehicle_control.py
```

### 4O链路记事本规划工具

```bash
python tools/4o_note_planning.py
```

### IM链路记事本规划工具

```bash
python tools/im_note_planning.py
```

## 配置说明

### 4o_vehicle.py 配置项
- FEISHU_DOC_URL: 飞书表格链接
- EXPECTED_NAME_COLUMN: 预期name列名
- EXPECTED_ARGS_COLUMN: 预期arguments列名
- ACTUAL_RNAME_COLUMN: 实际rname列名
- ACTUAL_RARGS_COLUMN: 实际rarguments列名
- RESULT_COLUMN_NAME: 结果写入列名
- CASE_ID_COLUMN_NAME: CaseID列名

### im_vehicle.py 配置项
- FEISHU_DOC_URL: 飞书表格链接
- EXPECTED_COLUMN_NAME: 预期APIINFO列名
- PLANNING_COLUMN_NAME: 第一步-规划列名
- RESULT_COLUMN_NAME: 结果写入列名
- CASE_ID_COLUMN_NAME: CaseID列名

## 功能特性

- 自动连接飞书表格
- 多结果无序对比功能
- 支持复杂JSON数据解析
- 日志记录功能
- 分片写入避免API限制
- 支持大表格处理

## 注意事项

1. 请确保网络连接畅通，以便访问飞书API
2. 飞书表格URL需要有相应的访问权限
3. 工具会在运行时自动创建日志文件夹
4. 如果表格数据量很大，工具会自动分片处理以避免API限制