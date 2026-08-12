---
name: storage-resource-recovery
description: 存储资源所有权、异常回滚与恢复方法，按源码资源信号加载。
---

# 资源与恢复

为资源建立申请、所有者、转移、正常释放、异常释放和重复执行后的守恒关系。覆盖内存、buffer、
连接、fd/socket、timer/poller、thread、queue、pool、引用计数和外部注册项。

比较正常路径与每个错误出口，检查部分初始化、失败回滚、重复 close/fini、重连、超时、取消和
压力解除后的恢复。没有申请或所有权证据时不要臆测泄漏；把所需关联源码或运行观测记录为 Gap。
