# GPU 探测

用于验证 MoviePilot 容器内是否可以看到并调用 Intel GPU。

检测项包括：

- `/dev/dri` 与 `renderD*` 节点是否存在
- 当前容器用户是否有 render 节点读写权限
- OpenVINO 是否能发现 GPU，并执行一个最小 GPU 推理
- Vulkan、VAAPI、OpenCL、FFmpeg 硬件设备初始化情况

插件只做探测，不处理媒体文件，也不会修改系统配置。
