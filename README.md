# 独立工具包

这是一个独立的工具包，包含以下工具：
- [4o_vehicle_control.py](file:///Users/zhaoshuaibo/Code/vehicle_control_tools/tools/4o_vehicle_control.py) - 4O链路车辆控制功能测试工具
- [im_vehicle_control.py](file:///Users/zhaoshuaibo/Code/vehicle_control_tools/tools/im_vehicle_control.py) - IM链路车辆控制功能测试工具
- [4o_note_planning.py](file:///Users/zhaoshuaibo/Code/ALL/auto_eval_project/tools/4o_note.py) - 4O链路记事本规划功能测试工具
- [im_note_planning.py](file:///Users/zhaoshuaibo/Code/ALL/auto_eval_project/tools/4o_note.py) - IM链路记事本规划功能测试工具

## 项目结构

```
vehicle_control_tools/
├── tools/
│   ├── 4o_vehicle_control.py          # 4O链路车辆控制功能测试工具
│   ├── im_vehicle_control.py          # IM链路车辆控制功能测试工具
│   ├── 4o_note_planning.py            # 4O链路记事本规划功能测试工具
│   └── im_note_planning.py            # IM链路记事本规划功能测试工具
├── fastfeishu/             # 飞书API集成库
│   ├── __init__.py
│   ├── configs/
│   ├── core/
│   ├── exceptions/
│   ├── models/
│   └── utils/
├── requirements.txt        # 项目依赖
├── setup.py               # 包配置
└── README.md              # 本说明文件
```

## 安装依赖

在使用工具前，请先安装所需依赖：

```bash
pip install -r requirements.txt
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

## 配置

工具的配置信息位于各自文件的底部，包括飞书表格URL和列名配置。

## 功能特性

- 自动连接飞书表格
- 多结果无序对比功能
- 支持复杂JSON数据解析
- 日志记录功能
- 分片写入避免API限制