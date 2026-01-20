from setuptools import setup, find_packages

# 读取 requirements.txt 文件中的依赖
with open('requirements.txt', 'r') as f:
    install_requires = [line.strip() for line in f.readlines() if line.strip()]

setup(
    name='vehicle-control-tools',
    version='1.0.0',
    description='独立的飞书表格对比工具包，包含IM和Vehicle控制功能测试工具',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',  # 指定描述内容类型为 Markdown
    packages=find_packages(),  # 自动查找项目中的包
    install_requires=install_requires,
    python_requires='>=3.11',  # 更新Python版本要求
)