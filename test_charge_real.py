"""旧版真实开电测试入口已停用。

旧脚本硬编码了设备、端口，并把金额单位用于当前计量协议，存在误开电风险。
请使用 ``main.py``，先保持 ``NEPTUNE_DRY_RUN=true`` 完成演练；确认日志后再
通过同一入口启用真实续充。
"""


if __name__ == "__main__":
    raise SystemExit(
        "此旧版真实开电测试已安全停用。请运行 main.py，并先保持 "
        "NEPTUNE_DRY_RUN=true。"
    )
