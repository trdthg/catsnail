# RuyiSDK Eclipse Plugin Integration

The source material lives in `../ruyisdk-eclipse-plugins-test/`. The automated
graph stays in one Python module, [ruyisdk_ide.py](ruyisdk_ide.py), because its
four domains share one Ubuntu machine and typed Catsnail checkpoints. Splitting
the functions across independently collected modules would duplicate roots and
lose those reusable checkpoints.

The function order follows the source-test directories:

1. `RuyiSDK管理`
2. `包管理器`
3. `新闻`
4. `虚拟环境`

Run all supported acceptance tests:

```sh
uv run catsnail run integration-ubuntu
```

## RuyiSDK管理

| Source document | Automated test | Coverage |
| --- | --- | --- |
| `安装插件.md` | `测试RuyiSDK插件已安装` | Release archive and offline update site installation. Eclipse Marketplace is not included because it depends on a mutable third-party catalog. |
| `自动检测与安装ruyi.md` | `测试RuyiSDK自动检测与安装Ruyi` | Full GUI installation flow. |
| `调查问卷.md` | `测试RuyiSDK自动检测与安装Ruyi`, `测试RuyiSDK界面布局` | The QR dialog appears on first launch and restart; its visible UI is asserted and it can be closed with the keyboard. |
| `UI界面布局.md` | `测试RuyiSDK界面布局` | Resets the perspective, restarts Eclipse, and asserts the default Website, Ruyi Venv, Ruyi News, and Package Explorer regions. |

## 包管理器

| Source document | Automated test | Coverage |
| --- | --- | --- |
| `开发板选择框排序.md` | `测试包管理器按名称和ID排序` | Name and ID sort UI. |
| `开发板型号过滤Packages.md` | `测试包管理器按开发板筛选` | All packages without a device, then Milk-V Duo filtering. |
| `安装包.md` | `测试包管理器安装软件包` | Reproduces the documented close-during-download defect as an expected failure. The reusable installation prerequisite remains an internal checkpoint. |
| `移除包.md` | `测试包管理器移除软件包` | GUI uninstall and finished operation state. |

## 新闻

| Source document | Automated test | Coverage |
| --- | --- | --- |
| `打开新闻.md` | `测试打开RuyiNews` | Opens the Ruyi News view and its list. |
| `切换仅未读.md` | `测试RuyiNews仅显示未读`, `测试RuyiNews白色主题未读标记` | Verifies filtering behavior, then records the documented light-theme visibility defect as an expected failure. |
| `跟踪阅读状态.md` | `测试RuyiNews跟踪阅读状态` | Reopens the view and verifies persisted read state. |
| `搜索关键词.md` | `测试RuyiNews搜索关键词` | Filters the stable `0.40` title and ID through the search field. |
| `新闻缓存.md` | `测试RuyiNews离线缓存` | Disables the guest's default-route NIC after the list is cached, then reads the cached 0.40 release note. |

## 虚拟环境

| Source document | Automated test | Coverage |
| --- | --- | --- |
| `RuyiSDK项目模板.md` | `测试RuyiSDK项目模板` | Creates the Milk-V Duo template through the GUI and asserts the project tree. |
| `创建ruyi-venv时profile列表排序.md` | `测试新建RuyiVenv时Profile列表排序` | Name and required-quirks sort UI. |
| `venv创建Ruyi-venv名称默认情况下finish按钮报错.md` | `测试RuyiVenv默认名称需要项目` | Default-name/project requirement UI. |
| `New Virtual environment响应时间过长.md` | `测试新建RuyiVenv响应时间` | Verifies that the configuration page opens within the accepted response time. |
| `创建虚拟环境并应用到项目.md` | `测试创建并应用RuyiVenv` | Creates the no-sysroot Venv, associates it with the CDT project, then applies it through the Ruyi Venv view. |
| `项目右键扩展.md` | `测试项目右键RuyiSDK扩展` | Right-clicks the RuyiSDK template project and asserts the New, Apply, and Delete Venv submenu. |
| `项目绑定.md` | `测试项目绑定RuyiVenv` | Selects the existing CDT project from the Venv configuration page. |
| `虚拟环境信息.md` | `测试RuyiVenv信息` | Asserts the created Venv's linked project and selected toolchain profile. |
| `虚拟环境删除后项目目录更新.md` | `测试删除RuyiVenv后项目目录更新` | Deletes the Venv and asserts that its project directory disappears from Project Explorer. |
| `构建运行.md` | `测试RuyiSDK项目构建运行` | Builds the documented template and verifies that its output is a RISC-V ELF. The supplied template explicitly has no runnable board target, so execution cannot be claimed without inventing target configuration. |
| `中文操作系统创建虚拟环境.md` | Out of scope | This suite fixes its environment to Ubuntu 24.04 English. It belongs in a separate Chinese-locale scenario. |
| `使用不包含 sysroot 的工具链时，指定其他工具链时仍包含该工具链.md` | `测试RuyiVenv无sysroot工具链过滤` | Reproduces the stale toolchain-provided-sysroot choice as an expected failure. |
| `虚拟环境激活状态.md` | `测试RuyiVenv激活状态可见` | Records the absence of a direct active-Venv status indicator as an expected failure. |
| `GUI界面流程的QEMU调试.md` | Catsnail Studio | This is framework workflow coverage, not a RuyiSDK plugin behavior test. |
