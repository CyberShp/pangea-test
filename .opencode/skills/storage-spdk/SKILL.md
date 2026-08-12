---
name: storage-spdk
description: SPDK 应用与事件框架语义方法，仅在 SPDK 证据命中时使用。
---

# SPDK 分析方法

关注 `spdk_app_opts` 初始化、参数解析、`spdk_app_start` 的启动与阻塞边界、启动/关闭回调、
`spdk_app_fini`、reactor/thread/poller 上下文、mempool/DMA buffer 的所有权和错误退出路径。

应用壳层只证明启动与配置行为；数据面、协议状态机或资源语义位于关联库时，必须记录 supporting
context Gap，不能把应用入口的源码行覆盖宣称为整个 SPDK 子系统覆盖。
