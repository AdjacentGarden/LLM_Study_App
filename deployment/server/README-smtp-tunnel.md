# SMTP TCP 隧道

当后端机器无法直连邮件服务器的 465 端口时，可通过既有受管 SSH 反向转发传输 TCP。应用仍在后端机器上与原邮件服务器完成 TLS、SMTP 登录和发信；Windows 转发机不保存 SMTP 用户名、授权码或明文邮件。

将以下非秘密连接选项添加到后端现有私密环境文件，其余邮件配置保持原值：

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_TUNNEL_HOST=127.0.0.1
SMTP_TUNNEL_PORT=18465
```

`SMTP_FROM`、`SMTP_USER` 和 `SMTP_PASSWORD` 继续只保留在后端私密配置中。不要将 `SMTP_HOST` 改成 `127.0.0.1`：它用于 TLS SNI 与证书主机名验证。`SMTP_TUNNEL_HOST/PORT` 只改变 TCP 连接目标，不替换邮件服务身份，不关闭 CA 验证。

在具备邮件服务器网络连通性的转发机上，由已有 SSH 身份及进程管理器建立等价的反向转发：

```sh
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:18465:smtp.qq.com:465 <backend-ssh-destination>
```

此命令是配置示例；部署时使用该环境已有的密钥路径、SSH 主机与受管进程方式。只在后端回环地址绑定 18465，禁止改成所有网卡。隧道没有 HTTP CONNECT 或 TLS 中间人，不应给通用模型 HTTP relay 添加邮件凭据或开放任意转发权限。

两项 tunnel 选项都为空时，维持现有直连行为。只填一项、无效端口、URL 形式的主机或非 `ssl` 模式均拒绝发送，`email_available` 也返回 false；不会偷偷退回直连。隧道断开、TLS 主机名或证书验证失败时，发信失败并使本次验证码失效，不打印配置、授权码或验证码。

应用更改私密环境文件后按现有方式重启后端，再在用户授权的验收邮箱上完成一次真实 OTP 注册/登录。单元测试验证 socket 路由、证书验证设置、失败释放、直连 SSL、STARTTLS 与测试 plain SMTP 兼容；它们不代表真实邮件投递成功。正式验收需分别记录隧道 TLS 连通性、产品接口发送结果、邮箱实际收到验证码与验证成功，避免将验证码或 cookie 写进公开日志。
