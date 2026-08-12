---
description: 显式重试一个失败或 Review 未通过的 PANGEA v1 Run
agent: pangea-test
---

只执行下面这一个 bash tool call，不得执行任何前置探测：

```text
python3 runtime/runctl.py retry $ARGUMENTS
```
