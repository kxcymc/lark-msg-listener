"""各业务端点处理器。

每个 handler 签名固定为 handler(request: Request) -> Response。
文件按 sub-agent 职责分组，互不重叠：
  - business.py：records / analysis-jobs / demands / dev-tasks / repos / replay / media
  - overview.py：overview / timeseries / exports
  - monitor.py ：monitor(processes/queues/system) / logs / health / config
  - weekly.py  ：weekly-reports
"""
