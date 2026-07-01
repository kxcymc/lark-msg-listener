"""dashboard-monitor 前端消费的本地只读 API 服务。

独立旁路进程：不参与采集/分析业务，仅以只读方式暴露现有产物
（out/index.sqlite、record.json、媒体、logs、mywork）供前端经 /api 代理消费。
"""
