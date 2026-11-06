# CloudPath main delivery

- Use main as the sole working and delivery branch. Keep completed repairs on main.
- The current backend is the 4090 at connect.bjb1.seetacloud.com, SSH port 36243.
  Runtime root: /root/autodl-tmp/adaptive-book-ocr; API loopback port 18126.
- The latest user-authorized application path is iPhone/simulator -> Germany VPS
  HTTPS https://213-145-87-189.2a12-6e40-eff6-cc7b--1.sslip.io:18443 -> this 4090 API. App traffic must not depend
  on the remote Mac or Windows. See deployment/germany4090/README.md.
- Reuse the deployed OCR, BGE models and Whisper; preserve existing model
  environments, data, credentials and unrelated processes.
- Read deployment/direct4090/README.md before deployment. Keep credentials out of Git.
- Acceptance requires real simulator upload, OCR, course and plan generation,
  browsing, AI conversation, handwriting and other available note/media flows.
  Continue fixing and validating until these scenarios pass. Do not substitute
  fake model results for acceptance; only the configured tester email may use
  fixed mock authentication.
- Use isolated output directories and record commit IDs, request IDs, actual
  results and limitations. Reuse completed requests and query uncertain ones
  before initiating another paid operation.

- The user disabled strict local lecture citation/pedagogical quality review.
  Leave it off by default; retain only decoding and ownership consistency checks.

- Discovery教材保留在4090；登录和浏览只加载目录/展示信息。仅用户确认导入或此前确认的中断任务才下载完整资料。
- 新安装不得从旧服务器导出自动恢复个人课程、生成内容、进度或笔记；个人学习数据属于本次App安装。
- AI工具发现教材导入需打开详情等待用户确认，不能直接自动下载。
