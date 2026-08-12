---
name: sfmea
description: confirmed risk 的软件失效模式与影响分析方法。
---

# SFMEA

每条 SFMEA 对应一个可描述的失效模式，记录机理、外部影响、S/O/D 评分、可观测表现、建议验证和
关联 canonical risk。代码事实进入 evidence；无法由源码证明的机理必须在文字中标明推断边界。

S/O/D 取 1–10，用于相对排序而非替代严重度。测试被阻塞时仍保留 SFMEA，并把建议验证写成待补
条件，不伪造可执行步骤。
