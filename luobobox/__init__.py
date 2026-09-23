"""萝卜盒 LuoboBox —— codebuddy2api 本地网关的 Windows 桌面控制台。

设计原则：
  1. 绝不修改 codebuddy2api 目录下的代码（除 desensitize 补丁守护外），
     保证上游 release 可以直接覆盖升级。
  2. 网关永远是独立子进程，绝不在本进程内 runpy —— UI 不能被网关阻塞。
  3. 所有用户态数据放 %LOCALAPPDATA%\\LuoboBox，卸载时一删即净。
"""

__version__ = "1.1.6"
APP_NAME = "萝卜盒"
APP_NAME_EN = "LuoboBox"
