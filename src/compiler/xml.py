import os
import shutil
from xml.etree import ElementTree as ET
from xml.dom import minidom


def backup_xml(xml_path: str) -> None:
    try:
        bak_path = f"{xml_path}.bak"
        shutil.copy2(xml_path, bak_path)
        print(f"✅ 备份XML文件：{os.path.basename(bak_path)}")
    except Exception as e:
        print(f"⚠️ XML备份失败：{str(e)}")


def modify_xml_module(xml_path: str, module_name: str, so_abs_path: str) -> bool:
    try:
        # 保留默认命名空间
        ET.register_namespace("", "")
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 定位目标 Alpha 节点 (通过 id 匹配)
        alpha_node = root.find(f".//Modules/Alpha[@id='{module_name}']")
        if alpha_node is None:
            raise Exception(f"未找到id={module_name}的Alpha节点")
        
        # 更新module属性
        alpha_node.set("module", so_abs_path)

        # 美化xml并保存
        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
        xml_str = os.linesep.join([line for line in xml_str.splitlines() if line.strip()])
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
        return True
    except Exception as e:
        print(f"❌ XML修改失败: {str(e)}")
        return False
