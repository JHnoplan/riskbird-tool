"""
test_gui.py - GUI 功能测试脚本
用途：在 Mac 上测试 GUI 是否正常工作
"""

import sys
import os

# 确保能导入 riskbird_build 目录的模块
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """测试所有模块是否能正常导入"""
    print("测试模块导入...")
    try:
        from riskbird_gui import MainWindow
        print("✓ riskbird_gui 导入成功")
    except Exception as e:
        print(f"✗ riskbird_gui 导入失败：{e}")
        return False
    
    try:
        from riskbird_crawler import RiskBirdCrawler
        print("✓ riskbird_crawler 导入成功")
    except Exception as e:
        print(f"✗ riskbird_crawler 导入失败：{e}")
        return False
    
    try:
        from qizhidao_crawler import QiZhiDaoCrawler
        print("✓ qizhidao_crawler 导入成功")
    except Exception as e:
        print(f"✗ qizhidao_crawler 导入失败：{e}")
        return False
    
    try:
        from aiqicha_crawler import AiQiChaCrawler
        print("✓ aiqicha_crawler 导入成功")
    except Exception as e:
        print(f"✗ aiqicha_crawler 导入失败：{e}")
        return False
    
    return True


def test_gui_launch():
    """测试 GUI 能否正常启动"""
    print("\n测试 GUI 启动...")
    try:
        from PyQt5.QtWidgets import QApplication
        from riskbird_gui import MainWindow
        
        app = QApplication(sys.argv)
        window = MainWindow()
        
        print("✓ GUI 窗口创建成功")
        print(f"  窗口标题：{window.windowTitle()}")
        print(f"  窗口大小：{window.size().width()} x {window.size().height()}")
        
        # 不显示窗口，只测试创建
        # window.show()
        # app.exec_()
        
        print("✓ GUI 测试通过")
        # 收尾：趁 QApplication 还活着把窗口交出去。函数返回时解释器按**定义顺序**清局部变量，
        # 先没的是 app、后拆的是 window 的 wrapper，撞上已随 app 消失的 C++ 对象就是段错误
        # （退出码随机 139：这里明明打印了「测试通过」，批跑照样读成一次失败）。
        # 同一条纪律与成因见硬门第 21 条；本脚本只验窗口能否创建，用 hide() 不进生产 closeEvent。
        window.hide()
        window = None
        app.processEvents()
        return True
    except Exception as e:
        print(f"✗ GUI 启动失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_crawler_creation():
    """测试爬虫对象能否正常创建"""
    print("\n测试爬虫对象创建...")
    try:
        from riskbird_crawler import RiskBirdCrawler
        crawler1 = RiskBirdCrawler()
        print(f"✓ 风鸟爬虫创建成功 (BASE_URL: {crawler1.BASE_URL})")
    except Exception as e:
        print(f" 风鸟爬虫创建失败：{e}")
        return False
    
    try:
        from qizhidao_crawler import QiZhiDaoCrawler
        crawler2 = QiZhiDaoCrawler()
        print(f"✓ 企知道爬虫创建成功 (BASE_URL: {crawler2.BASE_URL})")
    except Exception as e:
        print(f"✗ 企知道爬虫创建失败：{e}")
        return False
    
    try:
        from aiqicha_crawler import AiQiChaCrawler
        crawler3 = AiQiChaCrawler()
        print(f"✓ 爱企查爬虫创建成功 (BASE_URL: {crawler3.BASE_URL})")
    except Exception as e:
        print(f"✗ 爱企查爬虫创建失败：{e}")
        return False
    
    return True


def main():
    print("="*60)
    print("企业数据采集工具 - GUI 测试")
    print("="*60)
    
    # 测试 1: 模块导入
    if not test_imports():
        print("\n❌ 模块导入测试失败")
        return
    
    # 测试 2: 爬虫对象创建
    if not test_crawler_creation():
        print("\n❌ 爬虫对象创建测试失败")
        return
    
    # 测试 3: GUI 启动
    if not test_gui_launch():
        print("\n❌ GUI 启动测试失败")
        return
    
    print("\n" + "="*60)
    print("✅ 所有测试通过！GUI 可以正常工作")
    print("="*60)
    print("\n下一步：")
    print("1. 运行 python3 dump_page_html.py 获取页面结构")
    print("2. 把生成的 HTML 文件发给我分析")
    print("3. 我会根据真实 HTML 完善爬虫选择器")


if __name__ == "__main__":
    main()
