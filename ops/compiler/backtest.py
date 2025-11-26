import subprocess


def run_gsim(venv_path: str, xml_path: str) -> bool:
    try:
        run_cmd = f"{venv_path}/bin/python /usr/local/gsim/run.py {xml_path}"
        process = subprocess.Popen(run_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        # 实时打印回测日志
        while process.poll() is None:
            if process.stdout is None:
                return False # TODO:
        
            line = process.stdout.readline()
            print(line)
            if line:
                # TODO: log
                print(f"[回测日志] {line.strip()}")
        
        if process.returncode != 0:
            raise Exception(f"回测退出码: {process.returncode}")
        print("✅ 回测成功")
        return True
    
    except Exception as e:
        print(f"❌ 回测失败: {str(e)}")
        return False
