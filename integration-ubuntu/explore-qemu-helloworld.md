# Explore task: RuyiSDK GNU upstream QEMU Hello World

在现有的 RuyiSDK Eclipse IDE 工作台状态上，新增一个真实可运行的 Catsnail
GUI 测试，验证完整用户流程：

1. 通过 Eclipse/RuyiSDK 的图形界面创建一个名为 `helloworld` 的项目。
2. 在 IDE 编辑器中编写最小的 Hello World 程序并保存。
3. 通过 RuyiSDK 的图形界面创建虚拟环境，选择 GNU upstream 工具链，并完成配置。
4. 在项目的图形化构建/运行配置中选择 QEMU 目标（如果 IDE 提供该选项）。
5. 使用 IDE 的 Build 和 Run 用户界面编译并运行这个示例。
6. 用截图断言验证：项目树、GNU upstream 工具链已选中、构建成功、QEMU
   运行成功的最终可见状态。命令输出不能单独作为通过依据。

测试必须使用普通用户会使用的鼠标点击和键盘输入，不得打开终端、调用串口、
直接写入虚拟机文件系统或通过宿主机命令绕过 IDE。每个重要状态都要从当前画面
确认后再继续，并优先复用现有场景中的稳定 fixture 和辅助函数。

如果 RuyiSDK 或 IDE 确实无法完成上述需求，先至少重复运行两次并排除截图、输入、
网络、QEMU 和环境问题；只有确认是稳定的产品可见缺陷时，才按 explore 的 XFAIL
证据协议记录，保留原始断言和失败产物。
